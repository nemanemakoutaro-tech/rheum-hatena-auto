import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from openai import OpenAI


# ============================================================
# Paths
# ============================================================

# 現在のGitHub配置:
#
# rheum-hatena-auto/
# ├── generate_article.py
# ├── post_hatena.py
# ├── prompt.md
# ├── history.json
# └── ...
#
# generate_article.py がリポジトリ直下にあるため .parent を使用する。
ROOT = Path(__file__).resolve().parent

HISTORY = ROOT / "history.json"
PROMPT = ROOT / "prompt.md"
OUT = ROOT / "generated_article.json"


# ============================================================
# History
# ============================================================

def load_history():
    if not HISTORY.exists():
        return {"posts": []}

    try:
        return json.loads(
            HISTORY.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return {"posts": []}


# ============================================================
# URL cleaning
# ============================================================

def remove_tracking_params_from_url(url: str) -> str:
    """
    URLから utm_* などの不要なtracking parameterを除去する。
    """

    try:
        parts = urlsplit(url)

        query = []

        for key, value in parse_qsl(
            parts.query,
            keep_blank_values=True,
        ):
            lower_key = key.lower()

            # utm_source / utm_medium / utm_campaign など
            if lower_key.startswith("utm_"):
                continue

            # その他の不要なtracking parameter
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
    HTML本文中のhref URLからtracking parameterを除去する。
    """

    pattern = re.compile(
        r'href=(["\'])(https?://[^"\']+)\1',
        flags=re.IGNORECASE,
    )

    def replace(match):
        quote = match.group(1)
        url = match.group(2)

        clean_url = remove_tracking_params_from_url(
            url
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


# ============================================================
# Markdown -> HTML fallback
# ============================================================

def normalize_mixed_markup(text: str) -> str:
    """
    AIがbody_html内に誤って混ぜた簡単なMarkdownを
    HTMLへ自動変換する。

    毎朝の自動運用で軽微なMarkdown混入だけを理由に
    workflow全体が失敗することを防ぐ。
    """

    # --------------------------------------------------------
    # Markdown link
    #
    # [PubMed](https://example.com)
    #
    # ↓
    #
    # <a href="https://example.com">PubMed</a>
    # --------------------------------------------------------

    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )

    # --------------------------------------------------------
    # Markdown bold
    #
    # **重要**
    #
    # ↓
    #
    # <strong>重要</strong>
    # --------------------------------------------------------

    text = re.sub(
        r"\*\*(.+?)\*\*",
        r"<strong>\1</strong>",
        text,
        flags=re.DOTALL,
    )

    # --------------------------------------------------------
    # Markdown italic
    #
    # *Ann Rheum Dis.*
    #
    # ↓
    #
    # <em>Ann Rheum Dis.</em>
    # --------------------------------------------------------

    text = re.sub(
        r"(?<!\*)\*([^*\n]+?)\*(?!\*)",
        r"<em>\1</em>",
        text,
    )

    # --------------------------------------------------------
    # Markdown heading level 3
    # --------------------------------------------------------

    text = re.sub(
        r"(?m)^###\s+(.+?)\s*$",
        r"<h3>\1</h3>",
        text,
    )

    # --------------------------------------------------------
    # Markdown heading level 2
    # --------------------------------------------------------

    text = re.sub(
        r"(?m)^##\s+(.+?)\s*$",
        r"<h2>\1</h2>",
        text,
    )

    # --------------------------------------------------------
    # Markdown heading level 1
    # h1はブログ本文では使わずh2へ変換
    # --------------------------------------------------------

    text = re.sub(
        r"(?m)^#\s+(.+?)\s*$",
        r"<h2>\1</h2>",
        text,
    )

    # --------------------------------------------------------
    # 単独行に残った裸URL
    #
    # https://example.com
    #
    # ↓
    #
    # <p><a href="...">リンク</a></p>
    # --------------------------------------------------------

    text = re.sub(
        r"(?m)^\s*(https?://\S+)\s*$",
        r'<p><a href="\1">リンク</a></p>',
        text,
    )

    return text.strip()


# ============================================================
# HTML validation
# ============================================================

def validate_html_body(body_html: str):
    """
    自動補正後にも明らかなMarkdownや不要なSources欄が
    残っていないか最終確認する。
    """

    if not body_html:
        raise RuntimeError(
            "body_html is empty."
        )

    if len(body_html.strip()) < 300:
        raise RuntimeError(
            "body_html is unexpectedly short."
        )

    forbidden_patterns = [
        # Markdown heading
        r"(?m)^\s*#{1,6}\s+",

        # Markdown link
        r"\[[^\]]+\]\(https?://",

        # code fence
        r"```",

        # 不要なSources / References
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
                "body_html contains an unwanted "
                f"pattern: {pattern}"
            )


# ============================================================
# Web search source collection
# ============================================================

def collect_urls(obj):
    """
    OpenAI Web Searchで実際に取得されたURLを抽出する。

    これは監査・確認用としてgenerated_article.jsonに
    保存するだけで、ブログ本文末尾には自動追加しない。
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
                    urls.add(
                        remove_tracking_params_from_url(
                            value
                        )
                    )

                else:
                    walk(value)

        elif isinstance(x, list):

            for item in x:
                walk(item)

    walk(obj)

    return sorted(urls)


# ============================================================
# Main
# ============================================================

def main():

    # --------------------------------------------------------
    # Environment
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # OpenAI client
    # --------------------------------------------------------

    client = OpenAI(
        api_key=api_key
    )

    # --------------------------------------------------------
    # History
    # --------------------------------------------------------

    history = load_history()

    # 直近40投稿を重複回避用に利用
    recent_posts = history.get(
        "posts",
        [],
    )[-40:]

    # --------------------------------------------------------
    # Date
    # --------------------------------------------------------

    today = datetime.now(
        ZoneInfo("Asia/Tokyo")
    )

    # --------------------------------------------------------
    # Prompt
    # --------------------------------------------------------

    if not PROMPT.exists():
        raise RuntimeError(
            f"prompt.md not found: {PROMPT}"
        )

    base_prompt = PROMPT.read_text(
        encoding="utf-8"
    )

    if recent_posts:

        history_text = "\n".join(
            (
                f"- {post.get('date', '')}: "
                f"{post.get('title', '')}"
            )
            for post in recent_posts
        )

    else:

        history_text = (
            "- 過去投稿なし"
        )

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
海外情報だけで判断せず、
可能な限り国内一次情報を確認してください。

特に海外研究の薬剤用量や治療戦略が
日本の標準的な診療と異なる場合は、
その違いを明確にしてください。

記事はMarkdownではなくHTML本文として作成してください。

リンクは本文中に乱立させず、
原則として「③ 論文情報」に集約してください。

URLを裸で表示せず、
PubMed、原著論文、Free full text、PMDAなど
短いリンク名を使用してください。

段落は原則2〜4文程度とし、
見出しの前後では必ず段落を分けてください。
"""

    # --------------------------------------------------------
    # Responses API
    #
    # Structured Outputsで以下を強制:
    #
    # title
    # body_html
    # categories
    #
    # これによりJSONのキー揺れを防止する。
    # --------------------------------------------------------

    response = client.responses.create(

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

                "strict":
                    True,

                "schema": {
                    "type":
                        "object",

                    "properties": {

                        "title": {
                            "type":
                                "string",
                        },

                        "body_html": {
                            "type":
                                "string",
                        },

                        "categories": {
                            "type":
                                "array",

                            "items": {
                                "type":
                                    "string",
                            },

                            "minItems":
                                1,

                            "maxItems":
                                3,
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

    # --------------------------------------------------------
    # Parse JSON
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Basic validation
    # --------------------------------------------------------

    required_keys = {
        "title",
        "body_html",
        "categories",
    }

    missing_keys = (
        required_keys
        - set(article.keys())
    )

    if missing_keys:

        raise RuntimeError(
            "Missing required JSON keys: "
            + ", ".join(
                sorted(missing_keys)
            )
        )

    if not isinstance(
        article["title"],
        str,
    ):
        raise RuntimeError(
            "title must be a string."
        )

    if not isinstance(
        article["body_html"],
        str,
    ):
        raise RuntimeError(
            "body_html must be a string."
        )

    if not isinstance(
        article["categories"],
        list,
    ):
        raise RuntimeError(
            "categories must be a list."
        )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    article["title"] = (
        article["title"].strip()
    )

    # --------------------------------------------------------
    # Categories
    # --------------------------------------------------------

    article["categories"] = [
        str(category).strip()

        for category
        in article["categories"]

        if str(category).strip()
    ][:3]

    # --------------------------------------------------------
    # HTML normalization
    #
    # 重要:
    #
    # AIが誤ってMarkdownを混ぜても、
    # 軽微なものは自動的にHTMLへ変換する。
    # --------------------------------------------------------

    article["body_html"] = (
        normalize_mixed_markup(
            article[
                "body_html"
            ].strip()
        )
    )

    # --------------------------------------------------------
    # Tracking parameter除去
    # --------------------------------------------------------

    article["body_html"] = (
        clean_tracking_links(
            article[
                "body_html"
            ]
        )
    )

    # --------------------------------------------------------
    # 最終HTML validation
    # --------------------------------------------------------

    validate_html_body(
        article[
            "body_html"
        ]
    )

    # --------------------------------------------------------
    # Search sources
    #
    # ブログ本文には追加せず、
    # generated_article.json内に監査用保存。
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    article["date"] = (
        today.strftime(
            "%Y-%m-%d"
        )
    )

    article["generated_at"] = (
        today.isoformat()
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    OUT.write_text(

        json.dumps(
            article,
            ensure_ascii=False,
            indent=2,
        ),

        encoding="utf-8",
    )

    print(
        f"Generated: "
        f"{article['title']}"
    )

    print(
        f"Saved to: {OUT}"
    )


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    main()
