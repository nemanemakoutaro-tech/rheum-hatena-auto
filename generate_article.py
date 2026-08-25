import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from openai import OpenAI


# 現在のGitHub配置:
#
# rheum-hatena-auto/
# ├── generate_article.py
# ├── post_hatena.py
# ├── prompt.md
# ├── history.json
# └── ...
#
ROOT = Path(__file__).resolve().parent

HISTORY = ROOT / "history.json"
PROMPT = ROOT / "prompt.md"
OUT = ROOT / "generated_article.json"


def load_history():
    if not HISTORY.exists():
        return {"posts": []}

    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"posts": []}


def strip_code_fence(text: str) -> str:
    """
    モデルが誤って ```json ... ``` を付けた場合に除去する。
    """
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    return text.strip()


def remove_tracking_params_from_url(url: str) -> str:
    """
    URLから utm_* などの不要なtracking parameterを除去する。
    """
    try:
        parts = urlsplit(url)

        query = []

        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            lower_key = key.lower()

            if lower_key.startswith("utm_"):
                continue

            if lower_key in {
                "source",
                "campaign",
                "ref",
                "referrer",
                "tracking",
            }:
                continue

            query.append((key, value))

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


def clean_tracking_links(html_text: str) -> str:
    """
    HTML本文中の href URL から tracking parameter を除去する。
    """

    pattern = re.compile(
        r'href=(["\'])(https?://[^"\']+)\1',
        flags=re.IGNORECASE,
    )

    def replace(match):
        quote = match.group(1)
        url = match.group(2)
        clean_url = remove_tracking_params_from_url(url)

        return f"href={quote}{clean_url}{quote}"

    return pattern.sub(replace, html_text)


def validate_html_body(body_html: str):
    """
    Markdown記法や不要なReferences欄が残っていないか確認する。
    """

    if not body_html or len(body_html.strip()) < 300:
        raise RuntimeError("body_html is unexpectedly short.")

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
        if re.search(pattern, body_html):
            raise RuntimeError(
                f"body_html contains an unwanted pattern: {pattern}"
            )


def collect_urls(obj):
    """
    Web検索で取得したURLを監査用に抽出する。
    ブログ本文には自動追加しない。
    """
    urls = set()

    def walk(x):
        if isinstance(x, dict):
            for key, value in x.items():
                if (
                    key == "url"
                    and isinstance(value, str)
                    and value.startswith("http")
                ):
                    urls.add(remove_tracking_params_from_url(value))
                else:
                    walk(value)

        elif isinstance(x, list):
            for item in x:
                walk(item)

    walk(obj)

    return sorted(urls)


def main():
    api_key = os.environ["OPENAI_API_KEY"]

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

    client = OpenAI(api_key=api_key)

    history = load_history()

    # 直近40投稿を重複回避用に利用
    recent_posts = history.get("posts", [])[-40:]

    today = datetime.now(
        ZoneInfo("Asia/Tokyo")
    )

    if not PROMPT.exists():
        raise RuntimeError(
            f"prompt.md not found: {PROMPT}"
        )

    base_prompt = PROMPT.read_text(
        encoding="utf-8"
    )

    if recent_posts:
        history_text = "\n".join(
            f"- {post.get('date', '')}: {post.get('title', '')}"
            for post in recent_posts
        )
    else:
        history_text = "- 過去投稿なし"

    user_input = f"""
{base_prompt}

今日の日付（日本時間）:
{today.strftime("%Y-%m-%d")}

直近の投稿履歴:
{history_text}

上記履歴と同一または非常に近いテーマは避けてください。

最新の公開情報をWeb検索し、
今日もっとも実臨床的価値の高いテーマを選んでください。

検索時は以下を優先してください。
- PubMed
- 出版社の原著論文ページ
- EULAR / ACR / BSR 等の公式学会文書
- PMDA
- 国内電子添文
- 日本リウマチ学会等の国内学会資料

日本での承認、保険適用、用量を断定する場合は、
海外情報だけで判断せず、可能な限り国内一次情報を確認してください。

最終回答は prompt.md で指定した
JSONオブジェクトのみ返してください。
"""

    response = client.responses.create(
        model=model,
        input=user_input,

        tools=[
            {
                "type": "web_search",
                "search_context_size": os.getenv(
                    "OPENAI_SEARCH_CONTEXT",
                    "medium",
                ),
                "user_location": {
                    "type": "approximate",
                    "country": "JP",
                    "timezone": "Asia/Tokyo",
                },
            }
        ],

        reasoning={
            "effort": os.getenv(
                "OPENAI_REASONING_EFFORT",
                "low",
            )
        },

        text={
            "verbosity": os.getenv(
                "OPENAI_VERBOSITY",
                "medium",
            )
        },

        max_tool_calls=max_tool_calls,
        max_output_tokens=max_output_tokens,

        include=[
            "web_search_call.action.sources"
        ],
    )

    raw_text = strip_code_fence(
        response.output_text
    )

    try:
        article = json.loads(raw_text)

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Model output was not valid JSON.\n\n"
            f"{raw_text}"
        ) from exc

    required_keys = {
        "title",
        "body_html",
        "categories",
    }

    missing_keys = required_keys - set(article.keys())

    if missing_keys:
        raise RuntimeError(
            "Missing required JSON keys: "
            + ", ".join(sorted(missing_keys))
        )

    if not isinstance(article["title"], str):
        raise RuntimeError("title must be a string.")

    if not isinstance(article["body_html"], str):
        raise RuntimeError("body_html must be a string.")

    if not isinstance(article["categories"], list):
        raise RuntimeError("categories must be a list.")

    article["title"] = article["title"].strip()

    article["categories"] = [
        str(category).strip()
        for category in article["categories"]
        if str(category).strip()
    ][:3]

    article["body_html"] = clean_tracking_links(
        article["body_html"].strip()
    )

    validate_html_body(
        article["body_html"]
    )

    # Web検索で実際に参照されたURLを監査用に保存
    try:
        response_dict = response.model_dump()

        searched_sources = collect_urls(
            response_dict
        )

    except Exception:
        searched_sources = []

    if searched_sources:
        article["searched_sources"] = searched_sources

    article["date"] = today.strftime("%Y-%m-%d")
    article["generated_at"] = today.isoformat()

    OUT.write_text(
        json.dumps(
            article,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Generated: {article['title']}")
    print(f"Saved to: {OUT}")


if __name__ == "__main__":
    main()
