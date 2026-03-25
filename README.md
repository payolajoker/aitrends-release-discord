# AI Trends Release Discord Notifier

Posts `Critical` and `Notable` items from AI Trends releases to a Discord webhook.

## What it sends

- Repository (`owner/repo`)
- Version (`tag_name`)
- Korean title
- Korean summary
- GitHub release link

`Routine` items are ignored.

## Local run

```bash
set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
python src/main.py
```

Dry run:

```bash
set DRY_RUN=1
python src/main.py
```

## GitHub Actions

The workflow runs every hour at minute `0` and `30`.

Required repository secret:

- `DISCORD_WEBHOOK_URL`

The workflow persists dedupe state by committing `data/sent_releases.json` back to the repository when new items are sent.
