import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests.auth import HTTPBasicAuth

ROOT = Path(__file__).resolve().parents[1]
ARTICLE = ROOT / "generated_article.json"
HISTORY = ROOT / "history.json"
ARTICLES_DIR = ROOT / "articles"

ATOM = "http://www.w3.org/2005/Atom"
APP = "http://www.w3.org/2007/app"
HATENABLOG = "http://www.hatena.ne.jp/info/xmlns#hatenablog"
ET.register_namespace("", ATOM)
ET.register_namespace("app", APP)
ET.register_namespace("hatenablog", HATENABLOG)


def sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = text
    return el


def main():
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.getenv("HATENA_BLOG_ID", "ctd-gim.hatenablog.com")
    draft = os.getenv("HATENA_DRAFT", "no").lower() in {"1", "true", "yes"}

    article = json.loads(ARTICLE.read_text(encoding="utf-8"))
    now = datetime.now(ZoneInfo("Asia/Tokyo"))

    entry = ET.Element(f"{{{ATOM}}}entry")
    sub(entry, f"{{{ATOM}}}title", article["title"])
    author = sub(entry, f"{{{ATOM}}}author")
    sub(author, f"{{{ATOM}}}name", hatena_id)
    content = sub(entry, f"{{{ATOM}}}content", article["body"], type="text/x-markdown")
    sub(entry, f"{{{ATOM}}}updated", now.isoformat())

    categories = article.get("categories", [])[:3]
    for category in categories:
        sub(entry, f"{{{ATOM}}}category", term=str(category))

    control = sub(entry, f"{{{APP}}}control")
    sub(control, f"{{{APP}}}draft", "yes" if draft else "no")

    xml_body = ET.tostring(entry, encoding="utf-8", xml_declaration=True)
    endpoint = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    r = requests.post(
        endpoint,
        data=xml_body,
        headers={"Content-Type": "application/atom+xml; charset=utf-8"},
        auth=HTTPBasicAuth(hatena_id, api_key),
        timeout=60,
    )
    if r.status_code != 201:
        raise RuntimeError(f"Hatena post failed: HTTP {r.status_code}\n{r.text[:2000]}")

    # Extract public URL if returned.
    public_url = ""
    try:
        root = ET.fromstring(r.content)
        for link in root.findall(f"{{{ATOM}}}link"):
            if link.attrib.get("rel") == "alternate":
                public_url = link.attrib.get("href", "")
                break
    except ET.ParseError:
        pass

    ARTICLES_DIR.mkdir(exist_ok=True)
    archive = ARTICLES_DIR / f"{article['date']}.md"
    archive.write_text(f"# {article['title']}\n\n{article['body']}\n", encoding="utf-8")

    if HISTORY.exists():
        history = json.loads(HISTORY.read_text(encoding="utf-8"))
    else:
        history = {"posts": []}
    history.setdefault("posts", []).append({
        "date": article["date"],
        "title": article["title"],
        "url": public_url,
        "draft": draft,
    })
    history["posts"] = history["posts"][-365:]
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    print(public_url or r.headers.get("Location", "posted"))


if __name__ == "__main__":
    main()
