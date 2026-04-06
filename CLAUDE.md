# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SMZDM (什么值得买 / "What's Worth Buying") deal scraper. Monitors SMZDM API for deals, applies three-stage filtering (composite scoring + user level shill detection + interaction anomaly detection), deduplicates via SQLite, and pushes to WeChat via WXPusher.

## Running

```bash
# Local (set env vars, needs playwright chromium installed)
pip install -r requirements.txt && playwright install --with-deps chromium
WXPUSHER_APP_TOKEN=xxx WXPUSHER_UID=xxx python3 smzdm.py

# Deployed via GitHub Actions cron (every 10 min)
```

Single-run mode: scan once -> filter -> push -> exit. No loop.

## Architecture

**`smzdm.py`** — single-file script, class `SmzdmScraper`:

- **Data source**: `https://api.smzdm.com/v1/list?limit=20&offset=N` — paginated deal listings, scans from page 1 with time-window stop (max 6 hours)
- **Stage 1 filter**: Composite scoring — weighted engagement score (comments×3 + collection×2 + worthy×1 >= 40) + minimum total engagement + score rate >= 70%
- **Stage 2 filter**: User level shill detection via Playwright — loads article page in headless Chromium, extracts comment user levels from `img[src*="/level/"]` URLs (e.g. `/level/8.png` = Lv8). If low-level users (< Lv6) exceed 50% of commenters, item is flagged as shill.
- **Stage 3 filter**: Interaction anomaly detection (worthy/unworthy ratio, comment/worthy ratio). Requires multiple flags to trigger.
- **Dedup**: SQLite (`smzdm.db`) + in-memory `seen_ids` set. 30-day auto-cleanup.
- **Push**: WXPusher API, HTML format

## Configuration

All in `CONFIG` dict at top of `smzdm.py`. Credentials via environment variables:
- `WXPUSHER_APP_TOKEN` / `WXPUSHER_UID` — WXPusher credentials
- `SMZDM_DB_PATH` — SQLite path (default: `smzdm.db`)

## Deployment (GitHub Actions)

`.github/workflows/smzdm.yml` — cron every 10 min. SQLite persisted via `actions/cache`. Secrets: `WXPUSHER_APP_TOKEN`, `WXPUSHER_UID`.

## Key Technical Notes

- SMZDM article pages have WAF protection (returns 202 with JS challenge) — `requests` cannot bypass it, but Playwright with headless Chromium can. Used for Stage 2 user level detection.
- Playwright browser is lazily initialized — only starts when a candidate passes Stage 1 and needs level checking. Reuses the same browser instance across all candidates.
- User levels are encoded in image URLs on the page: `https://res.smzdm.com/h5/h5_user/dist/assets/level/{N}.png`
- `tongji_hudong` field: comma-separated string (`评论_5,收藏_3,值_10,不值_2`) parsed for precise interaction data, with fallback to top-level API fields.
- Comment API (`article-api.smzdm.com`) exists but requires request signing — not feasible without reverse-engineering the mobile app's HMAC. Playwright approach is more reliable.
