import html
import json
import os
import re
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from openai import OpenAI, RateLimitError


ROOT = Path(__file__).resolve().parent

HISTORY = ROOT / "history.json"
PROMPT = ROOT / "prompt.md"
OUT = ROOT / "generated_article.json"
ARTICLES_DIR = ROOT / "articles"


def load_history():
    if not HISTORY.exists():
        return {"posts": []}

    try:
        return json.loads(
            HISTORY.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return {"posts": []}


def strip_html_tags(text: str) -> str:
    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = html.unescape(text)

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def extract_article_summary(path: Path) -> str:
    try:
        raw = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )
    except OSError:
        return ""

    h1 = re.findall(
        r"<h1[^>]*>(.*?)</h1>",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )

    h2 = re.findall(
        r"<h2[^>]*>(.*?)</h2>",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )

    h3 = re.findall(
        r"<h3[^>]*>(.*?)</h3>",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )

    parts = []

    for item in h1[:1]:
        parts.append(
            strip_html_tags(item)
        )

    for item in h2[:12]:
        parts.append(
            strip_html_tags(item)
        )

    for item in h3[:8]:
        parts.append(
            strip_html_tags(item)
        )

    if not parts:
        plain = strip_html_tags(raw)
        parts.append(
            plain[:1200]
        )

    return " / ".join(
        p for p in parts if p
    )


def build_history_catalog():
    history = load_history()

    catalog = []

    for post in history.get(
        "posts",
        [],
    ):
        date = post.get(
            "date",
            "",
        )

        title = post.get(
            "title",
            "",
        )

        categories = post.get(
            "categories",
            [],
        )

        disease = post.get(
            "disease",
            "",
        )

        focus = post.get(
            "focus",
            "",
        )

        clinical_question = post.get(
            "clinical_question",
            "",
        )

        topic_key = post.get(
            "topic_key",
            "",
        )

        category_text = ", ".join(
            str(x)
            for x in categories
        )

        parts = [
            date,
            title,
            disease,
            focus,
            clinical_question,
            topic_key,
            category_text,
        ]

        line = " | ".join(
            str(x).strip()
            for x in parts
            if str(x).strip()
        )

        if line:
            catalog.append(line)

    if ARTICLES_DIR.exists():
        for path in sorted(
            ARTICLES_DIR.glob("*.html")
        ):
            summary = extract_article_summary(
                path
            )

            if summary:
                catalog.append(
                    f"{path.stem} | {summary}"
                )

    deduped = []
    seen = set()

    for item in catalog:
        item = item.strip()

        if not item:
            continue

        if item in seen:
            continue

        seen.add(item)
        deduped.append(item)

    return deduped


def normalize_topic_text(text: str) -> str:
    text = text.lower()

    replacements = {
        "全身性強皮症": " ssc ",
        "強皮症": " ssc ",
        "systemic sclerosis": " ssc ",

        "関節リウマチ": " ra ",
        "rheumatoid arthritis": " ra ",

        "全身性エリテマトーデス": " sle ",
        "systemic lupus erythematosus": " sle ",

        "抗好中球細胞質抗体関連血管炎": " aav ",
        "anca関連血管炎": " aav ",
        "anca-associated vasculitis": " aav ",

        "顕微鏡的多発血管炎": " mpa ",
        "多発血管炎性肉芽腫症": " gpa ",
        "好酸球性多発血管炎性肉芽腫症": " egpa ",
        "結節性多発動脈炎": " pan ",

        "シェーグレン症候群": " sjogren ",
        "シェーグレン病": " sjogren ",
        "sjögren": " sjogren ",

        "炎症性筋疾患": " iim ",
        "炎症性筋炎": " iim ",
        "idiopathic inflammatory myopathy": " iim ",

        "巨細胞性動脈炎": " gca ",
        "giant cell arteritis": " gca ",

        "リウマチ性多発筋痛症": " pmr ",
        "polymyalgia rheumatica": " pmr ",

        "igg4関連疾患": " igg4rd ",
        "igg4-related disease": " igg4rd ",

        "心臓": " heart ",
        "心筋": " heart ",
        "心病変": " heart ",
        "心血管": " cardiovascular ",

        "間質性肺疾患": " ild ",
        "間質性肺炎": " ild ",

        "末梢神経": " peripheralnerve ",
        "ニューロパチー": " peripheralnerve ",
        "neuropathy": " peripheralnerve ",

        "筋病変": " muscle ",
        "筋炎": " muscle ",

        "腎病変": " kidney ",
        "腎障害": " kidney ",

        "眼病変": " eye ",
        "眼症状": " eye ",

        "嚥下障害": " dysphagia ",
        "嚥下": " dysphagia ",

        "スクリーニング": " screening ",
        "screening": " screening ",

        "維持療法": " maintenance ",
        "寛解維持": " maintenance ",

        "ステロイド減量": " steroidtaper ",
        "gc減量": " steroidtaper ",

        "感染症": " infection ",
        "ワクチン": " vaccine ",
    }

    for src, dst in replacements.items():
        text = text.replace(
            src,
            dst,
        )

    text = re.sub(
        r"https?://\S+",
        " ",
        text,
    )

    text = re.sub(
        r"[^a-z0-9ぁ-んァ-ヶ一-龥]+",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def char_ngrams(text: str, n=3):
    compact = re.sub(
        r"\s+",
        "",
        normalize_topic_text(text),
    )

    if len(compact) < n:
        return {compact} if compact else set()

    return {
        compact[i:i + n]
        for i in range(
            len(compact) - n + 1
        )
    }


def semantic_similarity(
    a: str,
    b: str,
) -> float:
    na = normalize_topic_text(a)
    nb = normalize_topic_text(b)

    if not na or not nb:
        return 0.0

    seq = SequenceMatcher(
        None,
        na,
        nb,
    ).ratio()

    ga = char_ngrams(na)
    gb = char_ngrams(nb)

    if ga and gb:
        jaccard = (
            len(ga & gb)
            / len(ga | gb)
        )
    else:
        jaccard = 0.0

    return max(
        seq,
        jaccard,
    )


def local_duplicate_check(
    candidate_text: str,
    history_catalog,
):
    best_score = 0.0
    best_match = ""

    for item in history_catalog:
        score = semantic_similarity(
            candidate_text,
            item,
        )

        if score > best_score:
            best_score = score
            best_match = item

    return best_score, best_match


def compact_history_for_prompt(
    history_catalog,
    per_item_chars=220,
    max_chars=24000,
):
    compact_items = []

    for item in history_catalog:
        item = re.sub(
            r"\s+",
            " ",
            str(item),
        ).strip()

        if not item:
            continue

        if len(item) > per_item_chars:
            item = item[:per_item_chars].rstrip() + "…"

        compact_items.append(
            "- " + item
        )

    text = "\n".join(
        compact_items
    )

    if len(text) <= max_chars:
        return text

    # Keep the newest entries when the compact catalog itself
    # becomes very large. Full-history duplicate detection is still
    # performed locally against history_catalog below.
    selected = []
    total = 0

    for item in reversed(compact_items):
        cost = len(item) + 1

        if selected and total + cost > max_chars:
            break

        selected.append(item)
        total += cost

    selected.reverse()

    return "\n".join(selected)


def closest_history_items(
    selected_theme,
    history_catalog,
    limit=8,
):
    query = (
        selected_theme.get("disease", "")
        + " "
        + selected_theme.get("focus", "")
        + " "
        + selected_theme.get("clinical_question", "")
        + " "
        + selected_theme.get("topic_key", "")
    )

    scored = []

    for item in history_catalog:
        scored.append(
            (
                semantic_similarity(
                    query,
                    item,
                ),
                item,
            )
        )

    scored.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    return [
        item
        for _, item in scored[:limit]
    ]


def parse_rate_limit_wait_seconds(exc):
    response = getattr(
        exc,
        "response",
        None,
    )

    headers = getattr(
        response,
        "headers",
        None,
    )

    if headers:
        retry_after = headers.get(
            "retry-after"
        )

        if retry_after:
            try:
                return max(
                    1.0,
                    float(retry_after),
                )
            except (TypeError, ValueError):
                pass

    message = str(exc)

    match = re.search(
        r"try again in\s*"
        r"(?:(\d+)h)?"
        r"(?:(\d+)m)?"
        r"([0-9.]+)s",
        message,
        flags=re.IGNORECASE,
    )

    if match:
        hours = int(
            match.group(1) or 0
        )
        minutes = int(
            match.group(2) or 0
        )
        seconds = float(
            match.group(3) or 0
        )

        return (
            hours * 3600
            + minutes * 60
            + seconds
        )

    return None


def create_response_with_rate_limit_retry(
    client,
    operation_name,
    max_attempts=3,
    **kwargs,
):
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return client.responses.create(
                **kwargs
            )

        except RateLimitError as exc:
            last_error = exc

            if attempt >= max_attempts:
                break

            requested_wait = (
                parse_rate_limit_wait_seconds(
                    exc
                )
            )

            fallback_wait = 20 * attempt

            if requested_wait is None:
                wait_seconds = fallback_wait
            else:
                # Do not leave a GitHub Actions runner sleeping for
                # hours. After reducing prompt size, ordinary TPM
                # collisions should clear with a short delay.
                wait_seconds = min(
                    max(requested_wait, 5.0),
                    90.0,
                )

            print(
                f"Rate limit during {operation_name} "
                f"(attempt {attempt}/{max_attempts}). "
                f"Retrying in {wait_seconds:.1f}s."
            )

            if (
                requested_wait is not None
                and requested_wait > 90
            ):
                print(
                    "API suggested a longer wait "
                    f"({requested_wait:.1f}s); capped at 90s "
                    "to avoid tying up the Actions runner."
                )

            time.sleep(
                wait_seconds
            )

    raise RuntimeError(
        f"{operation_name} failed after "
        f"{max_attempts} attempts because of "
        "OpenAI rate limits."
    ) from last_error


def remove_tracking_params_from_url(
    url: str,
) -> str:
    try:
        parts = urlsplit(url)

        query = []

        for key, value in parse_qsl(
            parts.query,
            keep_blank_values=True,
        ):
            lower_key = key.lower()

            if lower_key.startswith(
                "utm_"
            ):
                continue

            if lower_key in {
                "source",
                "campaign",
                "ref",
                "referrer",
                "tracking",
            }:
                continue

            query.append(
                (key, value)
            )

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query),
                parts.fragment,
            )
        )

    except Exception:
        return url


def clean_tracking_links(
    html_text: str,
) -> str:
    pattern = re.compile(
        r'href=(["\'])(https?://[^"\']+)\1',
        flags=re.IGNORECASE,
    )

    def replace(match):
        quote = match.group(1)
        url = match.group(2)

        clean_url = (
            remove_tracking_params_from_url(
                url
            )
        )

        return (
            f"href={quote}"
            f"{clean_url}"
            f"{quote}"
        )

    return pattern.sub(
        replace,
        html_text,
    )


def normalize_mixed_markup(
    text: str,
) -> str:
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )

    text = re.sub(
        r"\*\*(.+?)\*\*",
        r"<strong>\1</strong>",
        text,
        flags=re.DOTALL,
    )

    text = re.sub(
        r"(?<!\*)\*([^*\n]+?)\*(?!\*)",
        r"<em>\1</em>",
        text,
    )

    text = re.sub(
        r"(?m)^###\s+(.+?)\s*$",
        r"<h3>\1</h3>",
        text,
    )

    text = re.sub(
        r"(?m)^##\s+(.+?)\s*$",
        r"<h2>\1</h2>",
        text,
    )

    text = re.sub(
        r"(?m)^#\s+(.+?)\s*$",
        r"<h2>\1</h2>",
        text,
    )

    text = re.sub(
        r"(?m)^\s*(https?://\S+)\s*$",
        r'<p><a href="\1">リンク</a></p>',
        text,
    )

    return text.strip()


def validate_html_body(
    body_html: str,
):
    if not body_html:
        raise RuntimeError(
            "body_html is empty."
        )

    if len(
        body_html.strip()
    ) < 300:
        raise RuntimeError(
            "body_html is unexpectedly short."
        )

    forbidden_patterns = [
        r"(?m)^\s*#{1,6}\s+",
        r"\[[^\]]+\]\(https?://",
        r"```",
        r"(?i)<h[1-6][^>]*>\s*Sources?\s*</h[1-6]>",
        r"(?i)<h[1-6][^>]*>\s*References?\s*</h[1-6]>",
        r"(?i)<h[1-6][^>]*>\s*参考文献\s*</h[1-6]>",
        r"(?i)<h[1-6][^>]*>\s*参考サイト\s*</h[1-6]>",
    ]

    for pattern in forbidden_patterns:
        if re.search(
            pattern,
            body_html,
        ):
            raise RuntimeError(
                "body_html contains "
                "an unwanted pattern: "
                f"{pattern}"
            )


def collect_urls(obj):
    urls = set()

    def walk(x):
        if isinstance(
            x,
            dict,
        ):
            for key, value in x.items():
                if (
                    key == "url"
                    and isinstance(
                        value,
                        str,
                    )
                    and value.startswith(
                        "http"
                    )
                ):
                    urls.add(
                        remove_tracking_params_from_url(
                            value
                        )
                    )
                else:
                    walk(value)

        elif isinstance(
            x,
            list,
        ):
            for item in x:
                walk(item)

    walk(obj)

    return sorted(urls)


def choose_theme(
    client,
    model,
    history_catalog,
    today,
    search_context,
):
    history_text = (
        compact_history_for_prompt(
            history_catalog
        )
    )

    selector_prompt = f"""
あなたはリウマチ・膠原病専門医向け
抄読会のテーマ選定編集者です。

今日:
{today.strftime("%Y-%m-%d")}

以下は過去に公開した記事の圧縮一覧です。
重複判定自体は、この一覧とは別に全履歴を用いて
ローカルでも再確認します。

----- 過去記事 -----
{history_text or "- 過去記事なし"}
----- 過去記事ここまで -----


最新の公開情報を検索し、
今日扱う価値のある候補を3テーマ作ってください。

最優先:
・明日の外来・病棟で使える
・日本で実際に使える診療
・最近の重要エビデンス
・過去記事と実質的に重複しない


重要:
タイトル表現だけでなく、

疾患
臓器
Clinical Question
診療判断

が同じなら重複です。


単に新しいreviewが出ただけなら、
同じテーマを再採用しないでください。


既出テーマを再採用してよいのは、

・新guideline
・practice-changing RCT
・大規模研究
・新安全性情報
・日本で新承認

などにより、
以前の記事から診療が実際に変わる場合だけです。


候補ごとに、

disease:
疾患

focus:
臓器または診療テーマ

clinical_question:
臨床疑問

topic_key:
英数字中心の短いcanonical key
例:
ssc-heart-screening
aav-rituximab-maintenance
sle-steroid-taper

why_now:
今取り上げる理由

duplicate_risk:
low / medium / high

overlap_with:
重複しそうな過去記事。
なければ空文字。

practice_changing_update:
true / false

を判定してください。


selected_indexには
最も価値が高く、
かつ重複riskがlowの候補を選んでください。

候補が過去テーマと重複する場合は
別の疾患・臓器・Clinical Questionを探してください。
"""

    selection = None
    last_error = None
    last_raw_text = ""

    for attempt in range(1, 4):
        response = (
            create_response_with_rate_limit_retry(
                client=client,
                operation_name=(
                    "theme selection"
                ),
                max_attempts=3,
                model=model,
                input=selector_prompt,

                tools=[
                    {
                        "type": "web_search",
                        "search_context_size":
                            search_context,
                        "user_location": {
                            "type": "approximate",
                            "country": "JP",
                            "timezone": "Asia/Tokyo",
                        },
                    }
                ],

                reasoning={
                    "effort": "low",
                },

                text={
                    "verbosity": "low",

                    "format": {
                        "type": "json_schema",
                        "name": "theme_selection",
                        "strict": True,

                        "schema": {
                            "type": "object",

                            "properties": {
                                "candidates": {
                                    "type": "array",
                                    "minItems": 3,
                                    "maxItems": 3,

                                    "items": {
                                        "type": "object",

                                        "properties": {
                                            "disease": {
                                                "type": "string"
                                            },

                                            "focus": {
                                                "type": "string"
                                            },

                                            "clinical_question": {
                                                "type": "string"
                                            },

                                            "topic_key": {
                                                "type": "string"
                                            },

                                            "why_now": {
                                                "type": "string"
                                            },

                                            "duplicate_risk": {
                                                "type": "string",
                                                "enum": [
                                                    "low",
                                                    "medium",
                                                    "high",
                                                ],
                                            },

                                            "overlap_with": {
                                                "type": "string"
                                            },

                                            "practice_changing_update": {
                                                "type": "boolean"
                                            },
                                        },

                                        "required": [
                                            "disease",
                                            "focus",
                                            "clinical_question",
                                            "topic_key",
                                            "why_now",
                                            "duplicate_risk",
                                            "overlap_with",
                                            "practice_changing_update",
                                        ],

                                        "additionalProperties":
                                            False,
                                    },
                                },

                                "selected_index": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 2,
                                },
                            },

                            "required": [
                                "candidates",
                                "selected_index",
                            ],

                            "additionalProperties":
                                False,
                        },
                    },
                },

                max_tool_calls=2,
                max_output_tokens=4000,
            )
        )

        last_raw_text = (
            response.output_text or ""
        ).strip()

        try:
            selection = json.loads(
                last_raw_text
            )
            break

        except json.JSONDecodeError as exc:
            last_error = exc
            print(
                "Theme selection JSON parse failed "
                f"(attempt {attempt}/3): {exc}"
            )

            if attempt < 3:
                print(
                    "Retrying theme selection..."
                )

    if selection is None:
        preview = last_raw_text[-1200:]

        raise RuntimeError(
            "Theme selection failed after 3 attempts. "
            "The model repeatedly returned invalid or "
            "truncated JSON. Last output tail:\n"
            + preview
        ) from last_error

    candidates = selection[
        "candidates"
    ]

    scored = []

    for index, candidate in enumerate(
        candidates
    ):
        candidate_text = (
            candidate["disease"]
            + " "
            + candidate["focus"]
            + " "
            + candidate["clinical_question"]
            + " "
            + candidate["topic_key"]
        )

        score, match = (
            local_duplicate_check(
                candidate_text,
                history_catalog,
            )
        )

        candidate[
            "local_duplicate_score"
        ] = score

        candidate[
            "local_closest_match"
        ] = match

        scored.append(
            (
                index,
                score,
                candidate,
            )
        )

    selected_index = selection[
        "selected_index"
    ]

    selected = candidates[
        selected_index
    ]

    if (
        selected["duplicate_risk"]
        != "low"
        or (
            selected[
                "local_duplicate_score"
            ] >= 0.48
            and not selected[
                "practice_changing_update"
            ]
        )
    ):
        safe_candidates = [
            item
            for item in scored
            if (
                item[2][
                    "duplicate_risk"
                ] == "low"
                and (
                    item[1] < 0.48
                    or item[2][
                        "practice_changing_update"
                    ]
                )
            )
        ]

        if safe_candidates:
            safe_candidates.sort(
                key=lambda x: x[1]
            )

            selected = (
                safe_candidates[0][2]
            )

        else:
            raise RuntimeError(
                "All proposed themes appear "
                "to overlap with previous posts. "
                "No article was generated."
            )

    return selected


def main():
    api_key = os.environ[
        "OPENAI_API_KEY"
    ]

    model = os.getenv(
        "OPENAI_MODEL",
        "gpt-5.4-mini",
    )

    max_tool_calls = int(
        os.getenv(
            "OPENAI_MAX_TOOL_CALLS",
            "2",
        )
    )

    max_output_tokens = int(
        os.getenv(
            "OPENAI_MAX_OUTPUT_TOKENS",
            "4500",
        )
    )

    search_context = os.getenv(
        "OPENAI_SEARCH_CONTEXT",
        "medium",
    )

    reasoning_effort = os.getenv(
        "OPENAI_REASONING_EFFORT",
        "low",
    )

    verbosity = os.getenv(
        "OPENAI_VERBOSITY",
        "medium",
    )

    client = OpenAI(
        api_key=api_key
    )

    today = datetime.now(
        ZoneInfo(
            "Asia/Tokyo"
        )
    )

    if not PROMPT.exists():
        raise RuntimeError(
            f"prompt.md not found: {PROMPT}"
        )

    base_prompt = PROMPT.read_text(
        encoding="utf-8"
    )

    history_catalog = (
        build_history_catalog()
    )

    selected_theme = choose_theme(
        client=client,
        model=model,
        history_catalog=history_catalog,
        today=today,
        search_context=search_context,
    )

    print(
        "Selected theme: "
        + selected_theme[
            "clinical_question"
        ]
    )

    print(
        "Topic key: "
        + selected_theme[
            "topic_key"
        ]
    )

    print(
        "Closest previous topic score: "
        + str(
            round(
                selected_theme.get(
                    "local_duplicate_score",
                    0.0,
                ),
                3,
            )
        )
    )

    if selected_theme.get(
        "local_closest_match"
    ):
        print(
            "Closest previous article: "
            + selected_theme[
                "local_closest_match"
            ][:300]
        )

    relevant_history = (
        closest_history_items(
            selected_theme,
            history_catalog,
            limit=8,
        )
    )

    relevant_history_text = (
        "\n".join(
            "- " + item[:500]
            for item in relevant_history
        )
    )

    user_input = f"""
{base_prompt}


今日の日付:
{today.strftime("%Y-%m-%d")}


今回の記事テーマはすでに編集工程で決定済みです。


疾患:
{selected_theme["disease"]}


focus:
{selected_theme["focus"]}


Clinical Question:
{selected_theme["clinical_question"]}


topic_key:
{selected_theme["topic_key"]}


このテーマを今日取り上げる理由:
{selected_theme["why_now"]}


過去記事との重複候補:
{selected_theme["overlap_with"] or "なし"}


過去記事とのローカル類似度:
{selected_theme.get("local_duplicate_score", 0.0):.3f}


最も近い過去記事:
{selected_theme.get("local_closest_match", "") or "なし"}


practice-changing update:
{selected_theme["practice_changing_update"]}


以下は今回のテーマに近い過去記事だけを抽出した一覧です。
全履歴との重複判定はすでにローカルで実施済みです。

----- 関連する過去記事 -----
{relevant_history_text or "- 関連記事なし"}
----- 関連する過去記事ここまで -----


上記過去記事と同じ内容を繰り返さないでください。


今回のClinical Questionだけに集中してください。


過去と関連するテーマの場合でも、
過去記事で既に説明済みの一般論を長く繰り返さず、

「今回新たに分かったこと」

を中心にしてください。


最新の公開情報をWeb検索し、
原著論文、guideline、PMDA等を確認してください。


日本での承認、
保険適用、
用量

を断定する場合は、
可能な限り国内一次情報で確認してください。


本文は読みやすいHTMLにしてください。


最終回答は指定JSONのみ返してください。
"""

    response = (
        create_response_with_rate_limit_retry(
            client=client,
            operation_name=(
                "article generation"
            ),
            max_attempts=3,
            model=model,
            input=user_input,

            tools=[
                {
                    "type": "web_search",
                    "search_context_size":
                        search_context,
                    "user_location": {
                        "type": "approximate",
                        "country": "JP",
                        "timezone": "Asia/Tokyo",
                    },
                }
            ],

            reasoning={
                "effort":
                    reasoning_effort,
            },

            text={
                "verbosity":
                    verbosity,

                "format": {
                    "type": "json_schema",
                    "name":
                        "rheumatology_article",
                    "strict": True,

                    "schema": {
                        "type": "object",

                        "properties": {
                            "title": {
                                "type": "string",
                            },

                            "body_html": {
                                "type": "string",
                            },

                            "categories": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                },
                                "minItems": 1,
                                "maxItems": 3,
                            },
                        },

                        "required": [
                            "title",
                            "body_html",
                            "categories",
                        ],

                        "additionalProperties":
                            False,
                    },
                },
            },

            max_tool_calls=
                max_tool_calls,

            max_output_tokens=
                max_output_tokens,

            include=[
                "web_search_call.action.sources"
            ],
        )
    )

    raw_text = (
        response.output_text.strip()
    )

    try:
        article = json.loads(
            raw_text
        )

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Model output was not valid JSON."
            "\n\n"
            f"{raw_text}"
        ) from exc

    article["title"] = (
        article["title"].strip()
    )

    article["categories"] = [
        str(category).strip()
        for category
        in article["categories"]
        if str(category).strip()
    ][:3]

    article["body_html"] = (
        normalize_mixed_markup(
            article[
                "body_html"
            ].strip()
        )
    )

    article["body_html"] = (
        clean_tracking_links(
            article[
                "body_html"
            ]
        )
    )

    validate_html_body(
        article[
            "body_html"
        ]
    )

    final_topic_text = (
        selected_theme[
            "disease"
        ]
        + " "
        + selected_theme[
            "focus"
        ]
        + " "
        + selected_theme[
            "clinical_question"
        ]
        + " "
        + article[
            "title"
        ]
    )

    final_score, final_match = (
        local_duplicate_check(
            final_topic_text,
            history_catalog,
        )
    )

    if (
        final_score >= 0.60
        and not selected_theme[
            "practice_changing_update"
        ]
    ):
        raise RuntimeError(
            "Final article still appears "
            "too similar to a previous post. "
            f"Similarity={final_score:.3f}; "
            f"Closest={final_match[:500]}"
        )

    try:
        response_dict = (
            response.model_dump()
        )

        searched_sources = (
            collect_urls(
                response_dict
            )
        )

    except Exception:
        searched_sources = []

    if searched_sources:
        article[
            "searched_sources"
        ] = searched_sources

    article[
        "topic_key"
    ] = selected_theme[
        "topic_key"
    ]

    article[
        "disease"
    ] = selected_theme[
        "disease"
    ]

    article[
        "focus"
    ] = selected_theme[
        "focus"
    ]

    article[
        "clinical_question"
    ] = selected_theme[
        "clinical_question"
    ]

    article[
        "duplicate_score"
    ] = final_score

    article[
        "closest_previous_topic"
    ] = final_match

    article[
        "date"
    ] = today.strftime(
        "%Y-%m-%d"
    )

    article[
        "generated_at"
    ] = today.isoformat()

    OUT.write_text(
        json.dumps(
            article,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "Generated: "
        + article[
            "title"
        ]
    )

    print(
        "Final duplicate score: "
        + str(
            round(
                final_score,
                3,
            )
        )
    )

    print(
        f"Saved to: {OUT}"
    )


if __name__ == "__main__":
    main()
