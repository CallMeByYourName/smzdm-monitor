# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SMZDM (什么值得买 / "What's Worth Buying") deal scraper. Monitors SMZDM API for deals, applies two-stage filtering (interaction metrics + shill detection), deduplicates via SQLite, and pushes to WeChat via WXPusher.

## Running

```bash
# Local (set env vars)
WXPUSHER_APP_TOKEN=xxx WXPUSHER_UID=xxx python3 smzdm.py

# Deployed via GitHub Actions cron (every 10 min)
```

Single-run mode: scan once -> filter -> push -> exit. No loop.

## Architecture

**`smzdm.py`** — single-file script, class `SmzdmScraper`:

- **Data source**: `https://api.smzdm.com/v1/list?limit=20&offset=N` — paginated deal listings, scans from page 1 with smart stop (consecutive seen pages)
- **Stage 1 filter**: Interaction thresholds (comments, collection, worthy) + score rate
- **Stage 2 filter**: Shill detection via interaction pattern anomalies (worthy/unworthy ratio, comment/worthy ratio). Requires multiple flags to trigger.
- **Dedup**: SQLite (`smzdm.db`) + in-memory `seen_ids` set. 30-day auto-cleanup.
- **Push**: WXPusher API, HTML format

## Configuration

All in `CONFIG` dict at top of `smzdm.py`. Credentials via environment variables:
- `WXPUSHER_APP_TOKEN` / `WXPUSHER_UID` — WXPusher credentials
- `SMZDM_DB_PATH` — SQLite path (default: `smzdm.db`)

## Deployment (GitHub Actions)

`.github/workflows/smzdm.yml` — cron every 10 min. SQLite persisted via `actions/cache`. Secrets: `WXPUSHER_APP_TOKEN`, `WXPUSHER_UID`.

## Key Technical Notes

- SMZDM article pages have WAF protection (returns 202 with JS challenge) — cannot be scraped with `requests`. Shill detection uses list API data patterns instead.
- `tongji_hudong` field: comma-separated string (`评论_5,收藏_3,值_10,不值_2`) parsed for precise interaction data, with fallback to top-level API fields.
- `smzdm_old_optimized_final.py` is the legacy version (loop-based, hardcoded credentials). Keep for reference.
