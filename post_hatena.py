import html
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests.auth import HTTPBasicAuth


# 現在のGitHub配置ではPythonファイルが
# repository直下にあるため parent を使う。
ROOT = Path(__file__).resolve().parent

ARTICLE = ROOT / "generated_article.json"
HISTORY = ROOT / "history.json"
ARTICLES_DIR = ROOT / "articles"


ATOM = "http://www.w3.org/2005/Atom"
APP = "http://www.w3.org/2007/app"
HATENABLOG = (
    "http://www.hatena.ne.jp/info/"
    "xmlns#hatenablog"
)


ET.register_namespace(
    "",
    ATOM,
)

ET.register_namespace(
    "app",
    APP,
)

ET.register_namespace(
    "hatenablog",
    HATENABLOG,
)


def sub(
    parent,
    tag,
    text=None,
    **attrs,
):
    element = ET.SubElement(
        parent,
        tag,
        attrs,
    )

    if text is not None:
        element.text = text

    return element


def load_article():
    if not ARTICLE.exists():
        raise RuntimeError(
            "generated_article.json "
            f"not found: {ARTICLE}"
        )

    article = json.loads(
        ARTICLE.read_text(
            encoding="utf-8"
        )
    )

    required = {
        "title",
        "body_html",
        "categories",
        "date",
    }

    missing = required - set(article)

    if missing:
        raise RuntimeError(
            "Article JSON is missing: "
            + ", ".join(sorted(missing))
        )

    return article


def extract_public_url(response):
    """AtomPubレスポンスから公開URLを取得。"""

    try:
        root = ET.fromstring(
            response.content
        )

        for link in root.findall(
            f"{{{ATOM}}}link"
        ):
            if (
                link.attrib.get("rel")
                == "alternate"
            ):
                return link.attrib.get(
                    "href",
                    "",
                )

    except ET.ParseError:
        pass

    return ""


def main():
    hatena_id = os.environ[
        "HATENA_ID"
    ].strip()

    api_key = os.environ[
        "HATENA_API_KEY"
    ].strip()

    blog_id = os.getenv(
        "HATENA_BLOG_ID",
        "rheuma.hatenablog.com",
    ).strip()

    draft = (
        os.getenv(
            "HATENA_DRAFT",
            "yes",
        )
        .strip()
        .lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )

    article = load_article()

    now = datetime.now(
        ZoneInfo("Asia/Tokyo")
    )

    # Atom Entry作成
    entry = ET.Element(
        f"{{{ATOM}}}entry"
    )

    sub(
        entry,
        f"{{{ATOM}}}title",
        article["title"],
    )

    author = sub(
        entry,
        f"{{{ATOM}}}author",
    )

    sub(
        author,
        f"{{{ATOM}}}name",
        hatena_id,
    )

    # 重要:
    # MarkdownではなくHTMLとして投稿する。
    #
    # ElementTreeはXMLとして必要なescapeを
    # 自動的に行うため、
    # body_html自体をさらにhtml.escapeしない。
    sub(
        entry,
        f"{{{ATOM}}}content",
        article["body_html"],
        type="text/html",
    )

    sub(
        entry,
        f"{{{ATOM}}}updated",
        now.isoformat(),
    )

    # カテゴリ
    categories = (
        article.get(
            "categories",
            []
        )[:3]
    )

    for category in categories:
        category = str(
            category
        ).strip()

        if category:
            sub(
                entry,
                f"{{{ATOM}}}category",
                term=category,
            )

    # 下書き指定
    control = sub(
        entry,
        f"{{{APP}}}control",
    )

    sub(
        control,
        f"{{{APP}}}draft",
        "yes" if draft else "no",
    )

    xml_body = ET.tostring(
        entry,
        encoding="utf-8",
        xml_declaration=True,
    )

    endpoint = (
        "https://blog.hatena.ne.jp/"
        f"{hatena_id}/"
        f"{blog_id}/"
        "atom/entry"
    )

    response = requests.post(
        endpoint,
        data=xml_body,
        headers={
            "Content-Type":
                "application/atom+xml; "
                "charset=utf-8",
        },
        auth=HTTPBasicAuth(
            hatena_id,
            api_key,
        ),
        timeout=60,
    )

    if response.status_code != 201:
        raise RuntimeError(
            "Hatena post failed.\n"
            f"HTTP {response.status_code}\n"
            f"{response.text[:2000]}"
        )

    public_url = extract_public_url(
        response
    )

    # GitHub内にも記事を保存
    ARTICLES_DIR.mkdir(
        exist_ok=True
    )

    archive = (
        ARTICLES_DIR
        / f"{article['date']}.html"
    )

    archive_html = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>{html.escape(article["title"])}</title>
</head>
<body>
<h1>{html.escape(article["title"])}</h1>
{article["body_html"]}
</body>
</html>
"""

    archive.write_text(
        archive_html,
        encoding="utf-8",
    )

    # 投稿履歴更新
    if HISTORY.exists():
        try:
            history = json.loads(
                HISTORY.read_text(
                    encoding="utf-8"
                )
            )
        except json.JSONDecodeError:
            history = {
                "posts": []
            }
    else:
        history = {
            "posts": []
        }

    history.setdefault(
        "posts",
        [],
    ).append(
        {
            "date":
                article["date"],

            "title":
                article["title"],

            "url":
                public_url,

            "draft":
                draft,

            "categories":
                categories,
        }
    )

    # 最大365投稿を保存
    history["posts"] = (
        history["posts"][-365:]
    )

    HISTORY.write_text(
        json.dumps(
            history,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "Hatena post succeeded:"
    )

    print(
        public_url
        or response.headers.get(
            "Location",
            "posted",
        )
    )


if __name__ == "__main__":
    main()
