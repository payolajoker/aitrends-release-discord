# AI Trends Article Discord Notifier

Posts completed AI Trends articles to a Discord webhook.

## What it sends

- Source
- Source type
- Korean title
- Korean summary
- Original article link
- AI Trends article link

The notifier reads:

```text
https://aitrends.kr/api/articles?summary_status=completed&sort_by=latest
```

If the AI Trends API returns an error such as `404 Not Found` or is temporarily unreachable, the run logs the issue and exits successfully so scheduled GitHub Actions do not keep sending failure emails.

## Local run

```bash
export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
python src/main.py
```

Dry run:

```bash
export DRY_RUN=1
python src/main.py
```

Useful environment variables:

- `FETCH_LIMIT` — number of latest completed articles to check. Default: `30`
- `ARTICLE_SUMMARY_STATUS` — article summary status filter. Default: `completed`
- `ARTICLE_SORT_BY` — article sort mode. Default: `latest`
- `EMBEDS_PER_MESSAGE` — Discord embeds per message. Default: `5`

## GitHub Actions

The workflow runs every hour at minute `0` and `30`.

Required repository secret:

- `DISCORD_WEBHOOK_URL`

The workflow persists dedupe state by committing `data/sent_releases.json` back to the repository when new items are sent. The file name is kept for backward compatibility, but new IDs are stored as `article:<id>`.
