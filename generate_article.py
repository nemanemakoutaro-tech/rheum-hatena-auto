import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history.json"
PROMPT = ROOT / "prompt.md"
OUT = ROOT / "generated_article.json"


def load_history():
    if not HISTORY.exists():
        return {"posts": []}
    return json.loads(HISTORY.read_text(encoding="utf-8"))


def strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def collect_urls(obj):
    urls = set()
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "url" and isinstance(v, str) and v.startswith("http"):
                    urls.add(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for y in x:
                walk(y)
    walk(obj)
    return sorted(urls)


def main():
    api_key = os.environ["OPENAI_API_KEY"]
    model = os.getenv("OPENAI_MODEL", "gpt-5")
    client = OpenAI(api_key=api_key)

    history = load_history()
    recent = history.get("posts", [])[-40:]
    today = datetime.now(ZoneInfo("Asia/Tokyo"))

    base_prompt = PROMPT.read_text(encoding="utf-8")
    history_text = "\n".join(
        f"- {p.get('date','')}: {p.get('title','')}" for p in recent
    ) or "- なし"

    user_input = f"""
{base_prompt}

今日の日付（日本時間）: {today.strftime('%Y-%m-%d')}

直近の投稿履歴（重複回避用）:
{history_text}

最新の公開情報をWeb検索して、今日の記事を作成してください。
検索ではPubMed、出版社原著、EULAR/ACR/BSR等の学会、PMDA/国内添付文書など一次情報を優先してください。
"""

    response = client.responses.create(
        model=model,
        tools=[{
            "type": "web_search",
            "search_context_size": "high",
            "user_location": {
                "type": "approximate",
                "country": "JP",
                "timezone": "Asia/Tokyo"
            }
        }],
        input=user_input,
    )

    raw = strip_fence(response.output_text)
    try:
        article = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Model output was not valid JSON:\n{raw}") from e

    for key in ("title", "body", "categories"):
        if key not in article:
            raise RuntimeError(f"Missing required key: {key}")

    # Preserve a machine-auditable list of URLs actually surfaced by web search.
    try:
        response_dict = response.model_dump()
        searched_urls = collect_urls(response_dict)
    except Exception:
        searched_urls = []

    if searched_urls:
        article["searched_sources"] = searched_urls

    article["date"] = today.strftime("%Y-%m-%d")
    article["generated_at"] = today.isoformat()
    OUT.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
    print(article["title"])


if __name__ == "__main__":
    main()
