# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SMZDM (什么值得买 / "What's Worth Buying") deal monitor. The project runs as a single Python script that scans SMZDM deal listings, filters candidates by engagement quality, checks comment user levels and comment concentration, deduplicates with SQLite, and pushes accepted deals to WeChat via WXPusher.

## Running

```bash
pip install -r requirements.txt
WXPUSHER_APP_TOKEN=xxx WXPUSHER_UID=xxx python3 smzdm.py
```

Optional:

- `SMZDM_DB_PATH` sets the SQLite database path. Default: `smzdm.db`.

The script is single-run mode: scan once -> filter -> push -> exit. There is no local loop.

## Architecture

**`smzdm.py`** is the main script and contains `SmzdmScraper`.

- **Data source**: `https://api.smzdm.com/v1/list?limit=20&offset=N`
- **Channel filter**: only `faxian` and `youhui`.
- **Time window**: max 6 hours.
- **Stage 1 filter**: weighted engagement scoring: `comments * 3 + collection * 2 + worthy`.
- **Stage 2 filter**: comment user level and comment concentration checks from `https://haojia.m.smzdm.com/detail_modul/user_related_modul?article_id={article_id}`.
- **Stage 3 fallback**: interaction anomaly detection using worthy/unworthy ratio and comment/worthy ratio.
- **Dedup**: SQLite `history` table plus in-memory article IDs and title fingerprints.
- **Pending review**: SQLite `pending_reviews` table for deals whose comment level data is unavailable and should be rechecked in later runs.
- **Push**: WXPusher HTML message.

## Current Filtering Rules

Stage 1 has three acceptance paths:

- `均衡热度`: total engagement >= 15, composite score >= 45, score rate >= 70%.
- `高讨论`: comments >= 8, total engagement >= 20, composite score >= 70, score rate >= 70%.
- `早期好价`: worthy >= 4, comments >= 3, total engagement >= 12, composite score >= 25, score rate >= 90%.

Basic signal requirement:

- worthy >= 2 or comments >= 2.
- inactive deals are filtered if sold out, timed out, expired, or ended.

There is currently no title keyword or category/tag hard exclusion. Carrier cards, coupons, red packets, finance, and similar content should be blocked downstream by WXPusher keyword rules if desired.

## Comment-Level Logic

Comment level checks use the SMZDM mobile JSON module, not Playwright. This module is not the full comment pagination endpoint; it usually returns hot/related comment samples. The code records module total, raw samples, author comments, and non-author samples for diagnosis.

- Low level: Lv5 and below.
- High level: Lv6 and above.
- Filter when low-level comment ratio is greater than 35%.
- When at least 4 comment samples are available, also filter if unique users are fewer than 3 or one user accounts for more than 50% of samples.
- Mature balanced/high-discussion deals may pass with only 2 samples when both are Lv6+, from different users, score rate >= 85%, and comments >= 10 or composite score >= 80.
- Mature balanced/high-discussion deals may also pass as a large-thread partial sample when the module total is >= 50, score rate >= 95%, comments >= 15, composite score >= 50, non-author samples >= 2, low samples <= 1, and at least one sample is Lv6+.
- Large threads with fewer than 5 non-author samples are not considered representative enough for direct low-level filtering; if they do not pass the large-thread rule, they are deferred as sample-unavailable instead.
- Emerging deals do not use the partial-sample pass.

Unavailable comment data is not treated as an automatic pass:

- The per-run comment check budget is dynamic: check all normally checkable candidates, capped at 80 to avoid excessive external requests during candidate spikes.
- Emerging deals are deferred when comment levels are unavailable.
- Balanced/high-discussion deals are deferred when data is unavailable, then may fallback after repeated unavailable observations for allowed reasons (`sample`, `external`) only if score rate >= 85%, comments >= 15, and composite score >= 90.
- Budget-unavailable deals are deferred unless they are strong signals: composite score >= 120 and comments >= 20.
- Pending review records are kept for 2 days.

## JD Handling

JD self-operated hard filtering is disabled by default. GitHub Actions often cannot reliably read JD store/self-operated fields from external pages, so JD deals are handled by the same generic comment-level, concentration, interaction anomaly, and dedup rules as other platforms.

The old JD self-check helpers remain in the code behind `jd_self_filter_enabled`, but the default config is `False`.

## Dedup and Re-Push Rules

- Only successfully pushed deals are saved to `history`.
- Same article ID is skipped after being saved.
- Similar title fingerprints are deduped for 3 days.
- Same fingerprint can be pushed again if the new price is at least 5 RMB lower or at least 5% lower than the previous pushed minimum.
- Deferred, filtered, or failed-push deals are not written to `history`, so later scans can reevaluate them with newer data.

## Deployment

`.github/workflows/smzdm.yml`:

- Runs every 30 minutes via GitHub schedule.
- Supports manual `workflow_dispatch`.
- Supports `repository_dispatch` for external cron-job.org triggers.
- Persists `smzdm.db` with `actions/cache`.
- Uses `concurrency: smzdm-scan`.

Note: `actions/cache` is not a fully reliable mutable database store. Closely spaced runs can restore an older cache and may produce duplicate pushes. A state branch, artifact, external KV, or external database would be more robust.

## Key Technical Notes

- Dependency list is intentionally minimal: `requests`.
- `tongji_hudong` is parsed for precise interaction data (`评论_5,收藏_3,值_10,不值_2`) with top-level field fallback.
- External comment/detail requests are throttled with random delays.
- WAF-like responses (`202`, `403`, `429`, captcha markers, `probe.js`, access-frequency text) suspend external checks for the rest of the current run.
- Notification content includes score tag, price, score rate, engagement numbers, comment level stats when available, price-drop notes, and the SMZDM detail link.
