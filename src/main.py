import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get("AITRENDS_BASE_URL", "https://aitrends.kr")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
FETCH_LIMIT = int(os.environ.get("FETCH_LIMIT", "30"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "data/sent_releases.json"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
MAX_SENT_IDS = int(os.environ.get("MAX_SENT_IDS", "1000"))

SIGNIFICANCE_ORDER = {"critical": 0, "notable": 1}
EMBED_COLORS = {
    "critical": 15158332,
    "notable": 3447003,
}
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def fetch_json(url: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "AI-Trends-Discord-Notifier/1.0",
    }
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_github_release(owner: str, repo: str, tag_name: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag_name)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AI-Trends-Discord-Notifier/1.0",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
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


def build_releases_url(significance: str) -> str:
    query = urllib.parse.urlencode({"significance": significance, "limit": FETCH_LIMIT})
    return f"{BASE_URL}/api/releases?{query}"


def clean_text(value: str, limit: int) -> str:
    text = (value or "").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    compact = " ".join(lines)
    compact = compact.replace("**", "").replace("__", "").replace("`", "'")
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def summarize_release_body(value: str) -> str:
    text = (value or "").replace("\r", "")
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("!["):
            continue
        if stripped.lower().startswith("full changelog"):
            continue
        if stripped.startswith("##"):
            continue
        if stripped.startswith("*") or stripped.startswith("-"):
            stripped = stripped[1:].strip()
        cleaned_lines.append(stripped)
        if len(" ".join(cleaned_lines)) >= 700:
            break

    summary = clean_text(" ".join(cleaned_lines), 700)
    if summary:
        return summary
    return "Release notes body is empty."


def release_id(item: dict[str, Any]) -> str:
    owner = item.get("owner", "unknown")
    repo = item.get("repo", "unknown")
    tag_name = item.get("tag_name", "unknown")
    return f"{owner}/{repo}@{tag_name}"


def normalize_release(item: dict[str, Any]) -> dict[str, Any]:
    title = item.get("title_ko") or item.get("release_name") or release_id(item)
    summary = item.get("one_line_summary") or "릴리즈 요약이 제공되지 않았습니다."
    return {
        "id": release_id(item),
        "repo": f"{item.get('owner', 'unknown')}/{item.get('repo', 'unknown')}",
        "version": item.get("tag_name", "unknown"),
        "title": clean_text(title, 200),
        "summary": clean_text(summary, 600),
        "github_url": item.get("github_url") or f"https://github.com/{item.get('owner', '')}/{item.get('repo', '')}",
        "significance": str(item.get("significance", "notable")).lower(),
        "published_at": item.get("published_at"),
    }


def enrich_release(item: dict[str, Any]) -> dict[str, Any]:
    owner, repo = item["repo"].split("/", 1)
    try:
        github_release = fetch_github_release(owner, repo, item["version"])
    except urllib.error.HTTPError:
        return item
    except urllib.error.URLError:
        return item

    title = github_release.get("name") or item["title"]
    summary = summarize_release_body(str(github_release.get("body") or ""))
    github_url = github_release.get("html_url") or item["github_url"]
    published_at = github_release.get("published_at") or item["published_at"]

    enriched = dict(item)
    enriched["title"] = clean_text(str(title), 200)
    enriched["summary"] = summary
    enriched["github_url"] = str(github_url)
    enriched["published_at"] = published_at
    time.sleep(0.2)
    return enriched


def fetch_releases() -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for significance in ("critical", "notable"):
        data = fetch_json(build_releases_url(significance))
        for raw_item in data.get("releases", []):
            normalized = normalize_release(raw_item)
            items[normalized["id"]] = normalized

    return sorted(
        items.values(),
        key=lambda item: (
            SIGNIFICANCE_ORDER.get(item["significance"], 99),
            item.get("published_at") or "",
            item["repo"],
        ),
    )


def chunked(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def iso_timestamp(value: str | None) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_embed(item: dict[str, Any]) -> dict[str, Any]:
    significance = item["significance"]
    label = significance.capitalize()
    description = (
        f"**제목** {item['title']}\n"
        f"**내용** {item['summary']}"
    )
    return {
        "title": f"[{label}] {item['repo']}",
        "url": item["github_url"],
        "description": description,
        "color": EMBED_COLORS.get(significance, 3447003),
        "fields": [
            {"name": "Repo", "value": f"`{item['repo']}`", "inline": True},
            {"name": "Version", "value": f"`{item['version']}`", "inline": True},
            {"name": "GitHub", "value": item["github_url"], "inline": False},
        ],
        "footer": {"text": "AI Trends Releases Monitor"},
        "timestamp": iso_timestamp(item.get("published_at")),
    }


def post_to_discord(releases: list[dict[str, Any]]) -> None:
    if not releases:
        return

    if not DISCORD_WEBHOOK_URL and not DRY_RUN:
        raise RuntimeError("DISCORD_WEBHOOK_URL is required unless DRY_RUN=1")

    for batch in chunked(releases, 10):
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
        releases = fetch_releases()
        sent_ids = set(state["sent_ids"])
        unsent = [item for item in releases if item["id"] not in sent_ids]

        if not unsent:
            print("No new Critical or Notable releases to send.")
            return 0

        enriched = [enrich_release(item) for item in unsent]
        post_to_discord(enriched)

        if not DRY_RUN:
            for item in enriched:
                state["sent_ids"].append(item["id"])
            save_state(state)

        print(f"Sent {len(enriched)} release notifications.")
        return 0
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        print(f"HTTPError: {error.code} {detail}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
