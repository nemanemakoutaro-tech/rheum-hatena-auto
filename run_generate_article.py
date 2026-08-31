"""Lean/safe entry point for the daily article generator.

The wrapper reduces OpenAI usage by:
- choosing the daily theme entirely in local Python (no theme-selection API call),
- keeping full-history duplicate detection local,
- making the normal successful path use OpenAI only for article generation,
- capping output/search context,
- retrying only short-lived 429s and failing fast on long limits.
"""

import os
import time

import generate_article as ga


LONG_RATE_LIMIT_SECONDS = 120.0
MAX_SHORT_WAIT_SECONDS = 90.0
MAX_OUTPUT_TOKENS = 3400
LOCAL_DUPLICATE_THRESHOLD = 0.48


THEME_POOL = [
    ("SLE", "妊娠・周産期", "SLE患者の妊娠前評価と妊娠中の薬剤調整をどう行うか", "sle-pregnancy-medication"),
    ("SLE", "神経精神病変", "NPSLEを疑う症例で感染・薬剤・代謝性要因とどう鑑別するか", "sle-npsle-differential"),
    ("SLE", "肺病変", "SLEのshrinking lung syndromeをどう診断し治療するか", "sle-shrinking-lung"),
    ("SLE", "血液病変", "SLEの免疫性血小板減少に対する治療をどう選ぶか", "sle-immune-thrombocytopenia"),
    ("SLE", "心病変", "SLEの心膜炎・心筋炎をどう評価し治療するか", "sle-pericarditis-myocarditis"),
    ("SLE", "感染予防", "SLEの免疫抑制治療中にワクチンと感染予防をどう最適化するか", "sle-infection-prevention"),
    ("ループス腎炎", "治療反応評価", "ループス腎炎で蛋白尿・尿沈渣・腎機能をどう用いて治療反応を判定するか", "ln-response-assessment"),
    ("ループス腎炎", "再燃", "ループス腎炎の再燃をどう定義し再導入治療を選ぶか", "ln-relapse-management"),
    ("関節リウマチ", "肺病変", "RA-ILDでDMARDをどう選び呼吸器リスクを管理するか", "ra-ild-dmard-selection"),
    ("関節リウマチ", "心血管リスク", "RA患者の心血管リスクを日常診療でどう評価し介入するか", "ra-cardiovascular-risk"),
    ("関節リウマチ", "寛解後治療", "持続寛解RAでDMARD減量をどの順序で検討するか", "ra-remission-dmard-taper"),
    ("関節リウマチ", "高齢者", "高齢発症RAで有効性と感染リスクを踏まえ治療をどう選ぶか", "ra-elderly-treatment"),
    ("関節リウマチ", "足病変", "RAの前足部痛・変形をどう評価し保存的治療につなげるか", "ra-foot-disease"),
    ("関節リウマチ", "頸椎病変", "RAの頸椎病変をいつ疑い画像評価するか", "ra-cervical-spine"),
    ("全身性強皮症", "消化管病変", "SScの胃食道逆流と食道運動障害をどう評価し治療するか", "ssc-esophageal-disease"),
    ("全身性強皮症", "胃前庭部毛細血管拡張症", "SScのGAVEをいつ疑いどう治療するか", "ssc-gave"),
    ("全身性強皮症", "腎病変", "強皮症腎クリーゼを早期認識しACE阻害薬をどう使うか", "ssc-renal-crisis"),
    ("全身性強皮症", "末梢血管病変", "SScの難治性Raynaud現象と指尖潰瘍をどう治療するか", "ssc-digital-ulcer"),
    ("全身性強皮症", "肺高血圧", "SSc患者をPAHについていつ・どうスクリーニングするか", "ssc-pah-screening"),
    ("全身性強皮症", "筋骨格病変", "SScに伴う筋力低下を筋炎・廃用・薬剤性からどう鑑別するか", "ssc-muscle-weakness"),
    ("ANCA関連血管炎", "耳鼻科病変", "限局型GPAの耳鼻科病変をどう認識し全身評価につなげるか", "gpa-ent-disease"),
    ("ANCA関連血管炎", "眼病変", "AAVの強膜炎・眼窩病変をどう評価し緊急治療につなげるか", "aav-eye-disease"),
    ("ANCA関連血管炎", "末梢神経病変", "AAVの多発単神経炎をどう診断し治療反応を評価するか", "aav-mononeuritis-multiplex"),
    ("ANCA関連血管炎", "腎病変", "AAV腎炎で腎生検所見を予後予測と治療判断にどう使うか", "aav-kidney-biopsy"),
    ("ANCA関連血管炎", "再燃予測", "AAVでANCA値を再燃予測にどこまで使えるか", "aav-anca-relapse"),
    ("EGPA", "心病変", "EGPAの心病変を誰にどこまでスクリーニングするか", "egpa-cardiac-screening"),
    ("EGPA", "末梢神経病変", "EGPAの神経障害をどう評価し機能予後を改善するか", "egpa-neuropathy"),
    ("巨細胞性動脈炎", "眼虚血", "GCAで視力障害を防ぐため診断直後の治療をどう進めるか", "gca-visual-loss"),
    ("巨細胞性動脈炎", "大型血管病変", "GCAの大動脈病変をいつ画像フォローするか", "gca-aortic-surveillance"),
    ("高安動脈炎", "画像評価", "高安動脈炎の活動性評価でMRI・CT・PETをどう使い分けるか", "takayasu-imaging-activity"),
    ("結節性多発動脈炎", "消化管病変", "PANの腹痛で腸管虚血をいつ疑いどう評価するか", "pan-mesenteric-ischemia"),
    ("結節性多発動脈炎", "腎血管病変", "PANの腎動脈病変・腎梗塞・高血圧をどう評価するか", "pan-renal-vascular"),
    ("ベーチェット病", "血管病変", "血管型Behçetの静脈血栓症で免疫抑制と抗凝固をどう考えるか", "behcet-vascular-thrombosis"),
    ("ベーチェット病", "眼病変", "Behçetぶどう膜炎の視力予後を改善する治療戦略は何か", "behcet-uveitis"),
    ("再発性多発軟骨炎", "気道病変", "再発性多発軟骨炎の気道病変をいつ疑いどう評価するか", "rpc-airway-disease"),
    ("IgG4関連疾患", "大動脈・後腹膜病変", "IgG4関連大動脈周囲炎・後腹膜線維症をどう評価し治療するか", "igg4rd-aortitis-rpf"),
    ("IgG4関連疾患", "腎病変", "IgG4関連腎臓病を他の間質性腎炎からどう鑑別するか", "igg4rd-kidney"),
    ("IgG4関連疾患", "唾液腺・涙腺病変", "IgG4関連涙腺唾液腺炎をSjögren病やリンパ腫からどう鑑別するか", "igg4rd-salivary-lacrimal"),
    ("Sjögren病", "リンパ腫", "Sjögren病でリンパ腫を疑う赤旗所見と検査は何か", "sjogren-lymphoma-redflags"),
    ("Sjögren病", "末梢神経病変", "Sjögren病の末梢神経障害を病型別にどう診断し治療するか", "sjogren-neuropathy"),
    ("Sjögren病", "肺病変", "Sjögren病のILDをいつスクリーニングし治療するか", "sjogren-ild"),
    ("炎症性筋疾患", "嚥下障害", "炎症性筋疾患の嚥下障害をどう評価し誤嚥を予防するか", "iim-dysphagia"),
    ("炎症性筋疾患", "心病変", "炎症性筋疾患の潜在性心病変をどうスクリーニングするか", "iim-cardiac-screening"),
    ("炎症性筋疾患", "ILD", "抗MDA5抗体陽性皮膚筋炎のILDを早期にどうリスク層別化するか", "mda5-ild-risk"),
    ("炎症性筋疾患", "悪性腫瘍", "皮膚筋炎で悪性腫瘍スクリーニングをどう個別化するか", "dm-cancer-screening"),
    ("抗リン脂質抗体症候群", "妊娠", "産科APSでアスピリンとヘパリンをどう使い分けるか", "aps-obstetric-management"),
    ("抗リン脂質抗体症候群", "血小板減少", "APSに伴う血小板減少をどう評価し抗凝固と両立させるか", "aps-thrombocytopenia"),
    ("成人Still病", "MAS", "AOSDでMASを早期診断するため何を追跡するか", "aosd-mas-screening"),
    ("成人Still病", "治療戦略", "AOSDでステロイド依存を避ける治療導入をどう考えるか", "aosd-steroid-sparing"),
    ("リウマチ性多発筋痛症", "鑑別診断", "PMR様症状で感染・悪性腫瘍・EORAをどう鑑別するか", "pmr-differential"),
    ("リウマチ性多発筋痛症", "再燃", "PMRの再燃時にステロイド増量と追加治療をどう判断するか", "pmr-relapse"),
    ("CPPD", "慢性関節炎", "慢性CPP結晶性関節炎をRAからどう鑑別し治療するか", "cppd-chronic-arthritis"),
    ("CPPD", "二次性原因", "CPPD診断後に代謝性・内分泌性背景をどこまで検索するか", "cppd-secondary-causes"),
    ("サルコイドーシス", "筋骨格病変", "サルコイドーシスの関節炎を他の炎症性関節炎からどう鑑別するか", "sarcoid-arthritis"),
    ("VEXAS症候群", "診断", "高齢男性の炎症・血球減少でVEXASをいつ疑い遺伝子検査するか", "vexas-diagnosis"),
    ("ADA2欠損症", "PAN様血管炎", "若年発症PAN様血管炎でDADA2をいつ疑うか", "dada2-pan-differential"),
    ("Cogan症候群", "診断", "炎症性眼病変と聴覚前庭症状からCogan症候群をどう診断するか", "cogan-diagnosis"),
    ("Whipple病", "鑑別診断", "治療抵抗性の血清反応陰性関節炎でWhipple病をいつ疑うか", "whipple-arthritis"),
    ("Erdheim-Chester病", "鑑別診断", "Erdheim-Chester病をIgG4関連疾患からどう鑑別するか", "ecd-igg4rd-differential"),
    ("Schnitzler症候群", "診断", "慢性蕁麻疹と単クローン性IgMからSchnitzler症候群をどう診断するか", "schnitzler-diagnosis"),
    ("TAFRO症候群", "診断", "TAFRO症候群を感染症・自己免疫疾患・悪性腫瘍からどう鑑別するか", "tafro-differential"),
    ("免疫抑制治療", "ニューモシスチス肺炎予防", "リウマチ疾患でPCP予防を誰に開始しどこで中止するか", "rheum-pcp-prophylaxis"),
    ("免疫抑制治療", "B型肝炎再活性化", "リウマチ治療前後のHBVスクリーニングと再活性化予防をどう行うか", "rheum-hbv-reactivation"),
    ("免疫抑制治療", "結核", "生物学的製剤・JAK阻害薬開始前の潜在性結核をどう評価するか", "rheum-ltbi-screening"),
    ("免疫抑制治療", "帯状疱疹", "免疫抑制患者の帯状疱疹ワクチンをいつ接種するか", "rheum-zoster-vaccine"),
    ("グルココルチコイド", "骨粗鬆症", "ステロイド開始時の骨折リスク評価と骨粗鬆症治療をどう行うか", "gcio-bone-protection"),
]


def local_choose_theme(client, model, history_catalog, today, search_context):
    """Select a non-duplicate theme without calling OpenAI.

    Fresh evidence is searched during the subsequent article-generation call.
    This function only decides the clinical question and uses the full local
    archive for duplicate detection.
    """
    del client, model, search_context

    scored = []
    # Rotate the pool before scoring so equal scores do not always favor the
    # first item. The archive still dominates selection through similarity.
    offset = today.toordinal() % len(THEME_POOL)
    rotated = THEME_POOL[offset:] + THEME_POOL[:offset]

    for disease, focus, clinical_question, topic_key in rotated:
        candidate_text = f"{disease} {focus} {clinical_question} {topic_key}"
        score, match = ga.local_duplicate_check(candidate_text, history_catalog)
        scored.append((score, disease, focus, clinical_question, topic_key, match))

    scored.sort(key=lambda item: item[0])
    score, disease, focus, clinical_question, topic_key, match = scored[0]

    if score >= LOCAL_DUPLICATE_THRESHOLD:
        preview = "\n".join(
            f"- {item[4]}: {item[0]:.3f}"
            for item in scored[:8]
        )
        raise RuntimeError(
            "No sufficiently distinct local theme remains in THEME_POOL. "
            "Expand the pool before generating another article. "
            f"Best duplicate score={score:.3f}.\n{preview}"
        )

    print(
        "Local theme selection enabled: no OpenAI request was used for "
        "theme selection."
    )

    return {
        "disease": disease,
        "focus": focus,
        "clinical_question": clinical_question,
        "topic_key": topic_key,
        "why_now": (
            "実臨床で重要なClinical Questionであり、過去記事との重複が"
            "低いテーマとしてローカル選定した。最新の公開エビデンスは"
            "本文生成時のWeb検索で確認する。"
        ),
        "duplicate_risk": "low",
        "overlap_with": match if score >= 0.25 else "",
        "practice_changing_update": False,
        "local_duplicate_score": score,
        "local_closest_match": match,
    }


def create_response_with_rate_limit_retry(client, operation_name, max_attempts=3, **kwargs):
    """Retry short 429s, but immediately hand long limits to later schedules."""
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return client.responses.create(**kwargs)
        except ga.RateLimitError as exc:
            last_error = exc
            requested_wait = ga.parse_rate_limit_wait_seconds(exc)

            if requested_wait is not None and requested_wait > LONG_RATE_LIMIT_SECONDS:
                raise RuntimeError(
                    f"{operation_name} hit a long OpenAI rate limit "
                    f"({requested_wait:.1f}s). Failing fast; a later scheduled "
                    "GitHub Actions run will retry."
                ) from exc

            if attempt >= max_attempts:
                break

            fallback_wait = 20.0 * attempt
            wait_seconds = (
                fallback_wait
                if requested_wait is None
                else max(requested_wait, 5.0)
            )
            wait_seconds = min(wait_seconds, MAX_SHORT_WAIT_SECONDS)

            print(
                f"Short rate limit during {operation_name} "
                f"(attempt {attempt}/{max_attempts}); retrying in "
                f"{wait_seconds:.1f}s."
            )
            time.sleep(wait_seconds)

    raise RuntimeError(
        f"{operation_name} failed after {max_attempts} attempts because of "
        "OpenAI rate limits."
    ) from last_error


def cap_generation_settings():
    try:
        configured_output = int(
            os.getenv("OPENAI_MAX_OUTPUT_TOKENS", str(MAX_OUTPUT_TOKENS))
        )
    except ValueError:
        configured_output = MAX_OUTPUT_TOKENS

    os.environ["OPENAI_MAX_OUTPUT_TOKENS"] = str(
        min(configured_output, MAX_OUTPUT_TOKENS)
    )
    os.environ["OPENAI_SEARCH_CONTEXT"] = "low"

    print(
        "Lean generation settings: "
        f"max_output_tokens={os.environ['OPENAI_MAX_OUTPUT_TOKENS']}, "
        "search_context=low, theme_selection=local_python."
    )


def main():
    cap_generation_settings()
    ga.choose_theme = local_choose_theme
    ga.create_response_with_rate_limit_retry = create_response_with_rate_limit_retry
    ga.main()


if __name__ == "__main__":
    main()
