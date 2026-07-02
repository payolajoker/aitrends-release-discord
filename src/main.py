from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get("AITRENDS_BASE_URL", "https://aitrends.kr").rstrip("/")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
FETCH_LIMIT = max(1, int(os.environ.get("FETCH_LIMIT", "30")))
STATE_FILE = Path(os.environ.get("STATE_FILE", "data/sent_releases.json"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
MAX_SENT_IDS = max(1, int(os.environ.get("MAX_SENT_IDS", "1000")))
ARTICLE_SUMMARY_STATUS = os.environ.get("ARTICLE_SUMMARY_STATUS", "completed")
ARTICLE_SORT_BY = os.environ.get("ARTICLE_SORT_BY", "latest")
EMBEDS_PER_MESSAGE = min(10, max(1, int(os.environ.get("EMBEDS_PER_MESSAGE", "5"))))

EMBED_COLORS = {
    "article": 3447003,
    "youtube": 16711680,
    "reddit": 16729344,
    "paper": 10181046,
    "rss": 3447003,
    "web": 5763719,
    "twitter": 1942002,
}


def fetch_json(url: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "AI-Trends-Discord-Notifier/1.0",
    }
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_state() -> dict[str, list[str]]:
    if not STATE_FILE.exists():
        return {"sent_ids": []}

    with STATE_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    sent_ids = data.get("sent_ids", [])
    if not isinstance(sent_ids, list):
        sent_ids = []
    return {"sent_ids": [str(item) for item in sent_ids]}


def save_state(state: dict[str, list[str]]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    pruned = state["sent_ids"][-MAX_SENT_IDS:]
    with STATE_FILE.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump({"sent_ids": pruned}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_articles_url() -> str:
    query = urllib.parse.urlencode(
        {
            "limit": FETCH_LIMIT,
            "summary_status": ARTICLE_SUMMARY_STATUS,
            "sort_by": ARTICLE_SORT_BY,
        }
    )
    return f"{BASE_URL}/api/articles?{query}"


def clean_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    compact = " ".join(lines)
    compact = compact.replace("**", "").replace("__", "").replace("`", "'")
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def article_state_id(item: dict[str, Any]) -> str:
    article_id = item.get("id") or item.get("link") or item.get("created_at") or "unknown"
    return f"article:{article_id}"


def article_page_url(item: dict[str, Any]) -> str:
    article_id = item.get("id")
    if article_id is None:
        return BASE_URL
    return f"{BASE_URL}/articles/{urllib.parse.quote(str(article_id))}"


def normalize_article(item: dict[str, Any]) -> dict[str, Any]:
    summary_json = item.get("ai_summary_json")
    if not isinstance(summary_json, dict):
        summary_json = {}

    source = item.get("sources")
    if not isinstance(source, dict):
        source = {}

    title = (
        item.get("hook_title_ko")
        or summary_json.get("hook_title_ko")
        or item.get("title_ko")
        or summary_json.get("title_ko")
        or item.get("title")
        or article_state_id(item)
    )
    summary = (
        summary_json.get("one_line_summary")
        or summary_json.get("tldr")
        or item.get("ai_summary_ko")
        or item.get("summary")
        or "기사 요약이 제공되지 않았습니다."
    )
    source_name = source.get("name") or "AI Trends"
    source_type = str(source.get("source_type") or "article").lower()

    return {
        "id": article_state_id(item),
        "article_id": item.get("id"),
        "source": clean_text(source_name, 80),
        "source_type": clean_text(source_type, 40),
        "category": clean_text(source.get("category") or summary_json.get("category") or "", 80),
        "title": clean_text(title, 200),
        "summary": clean_text(summary, 520),
        "url": item.get("link") or article_page_url(item),
        "aitrends_url": article_page_url(item),
        "published_at": item.get("published_at") or item.get("created_at"),
    }


def read_http_error(error: urllib.error.HTTPError) -> str:
    try:
        detail = error.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    if "<html" in detail[:200].lower() or "<!doctype" in detail[:200].lower():
        return "HTML error response"
    return clean_text(detail, 500)


def fetch_articles() -> list[dict[str, Any]]:
    url = build_articles_url()
    try:
        data = fetch_json(url)
    except urllib.error.HTTPError as error:
        detail = read_http_error(error)
        print(
            f"AI Trends API unavailable; skipping this run without failing. "
            f"HTTP {error.code}: {detail}",
            file=sys.stderr,
        )
        return []
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        print(
            f"AI Trends API unavailable; skipping this run without failing. {error}",
            file=sys.stderr,
        )
        return []

    if not isinstance(data, dict):
        print(
            "AI Trends API response was not a JSON object; skipping this run without failing.",
            file=sys.stderr,
        )
        return []

    raw_articles = data.get("articles")
    if not isinstance(raw_articles, list):
        print(
            "AI Trends API response did not include an articles list; "
            "skipping this run without failing.",
            file=sys.stderr,
        )
        return []

    items: dict[str, dict[str, Any]] = {}
    for raw_item in raw_articles:
        if not isinstance(raw_item, dict):
            continue
        normalized = normalize_article(raw_item)
        items[normalized["id"]] = normalized

    return sorted(
        items.values(),
        key=lambda item: (item.get("published_at") or "", item["source"], item["title"]),
    )


def chunked(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def iso_timestamp(value: str | None) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_embed(item: dict[str, Any]) -> dict[str, Any]:
    source_type = item["source_type"]
    description = (
        f"**제목** {item['title']}\n"
        f"**요약** {item['summary']}"
    )
    fields = [
        {"name": "Source", "value": f"`{item['source']}`", "inline": True},
        {"name": "Type", "value": f"`{source_type}`", "inline": True},
        {"name": "AI Trends", "value": item["aitrends_url"], "inline": False},
    ]
    if item["category"]:
        fields.insert(2, {"name": "Category", "value": f"`{item['category']}`", "inline": True})

    return {
        "title": f"[Article] {item['source']}",
        "url": item["url"],
        "description": description,
        "color": EMBED_COLORS.get(source_type, EMBED_COLORS["article"]),
        "fields": fields,
        "footer": {"text": "AI Trends Articles Monitor"},
        "timestamp": iso_timestamp(item.get("published_at")),
    }


def post_to_discord(articles: list[dict[str, Any]]) -> None:
    if not articles:
        return

    if not DISCORD_WEBHOOK_URL and not DRY_RUN:
        raise RuntimeError("DISCORD_WEBHOOK_URL is required unless DRY_RUN=1")

    for batch in chunked(articles, EMBEDS_PER_MESSAGE):
        payload = {
            "content": None,
            "embeds": [build_embed(item) for item in batch],
        }

        if DRY_RUN:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            continue

        request = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AI-Trends-Discord-Notifier/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 300:
                raise RuntimeError(f"Discord webhook failed with status {response.status}")


def main() -> int:
    try:
        state = load_state()
        articles = fetch_articles()
        sent_ids = set(state["sent_ids"])
        unsent = [item for item in articles if item["id"] not in sent_ids]

        if not unsent:
            print("No new completed AI Trends articles to send.")
            return 0

        post_to_discord(unsent)

        if not DRY_RUN:
            for item in unsent:
                state["sent_ids"].append(item["id"])
            save_state(state)

        print(f"Sent {len(unsent)} article notifications.")
        return 0
    except urllib.error.HTTPError as error:
        detail = read_http_error(error)
        print(f"HTTPError: {error.code} {detail}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
