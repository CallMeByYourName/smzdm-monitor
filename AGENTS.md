# AGENTS.md

This file gives coding agents the repository-specific context needed to change and validate the project. It reflects the code and workflow as of `2026-07-27`. Treat `smzdm.py` and `.github/workflows/smzdm.yml` as the source of truth when they differ from documentation.

## Project Overview

This repository contains a single-run SMZDM (什么值得买) deal monitor. It scans deal feeds, evaluates engagement quality and growth, performs limited comment-sample checks, deduplicates accepted products in SQLite, and sends WXPusher HTML notifications.

The main design constraint is asymmetric data quality: list engagement fields are broadly available, while full comment-level distributions and JD store/self-operated fields are not reliably available from GitHub Actions. Do not turn unavailable external data into a hard rejection unless the user explicitly changes that policy.

## Commands

Install and run:

```bash
pip install -r requirements.txt
WXPUSHER_APP_TOKEN=xxx WXPUSHER_UID=xxx python3 smzdm.py
```

Run the test suite:

```bash
python3 -m unittest -v
```

Optional environment variable:

- `SMZDM_DB_PATH` changes the SQLite path. The default is `smzdm.db`.

The script performs one scan, sends any accepted deals, and exits. Scheduling belongs to GitHub Actions and cron-job.org; do not add a local loop.

## Repository Layout

- `smzdm.py`: application code, configuration, SQLite schema, filtering, network access, and notification formatting.
- `test_smzdm.py`: unit tests for feed handling, title filters, quality paths, trends, comments, deduplication, and push logs.
- `.github/workflows/smzdm.yml`: GitHub Actions runner and SQLite cache handling.
- `README.md`: current user-facing behavior and deployment documentation.
- `analysis/`: dated historical log reviews. A dated report is evidence from that point in time, not current configuration.

The only runtime dependency is `requests`.

## Data Sources

- Main feed: `https://api.smzdm.com/v1/list?limit=100&offset=N`.
- The main scan reads at most 51 pages, 5100 rows, and six hours of `faxian`/`youhui` items. The live API currently returns no rows beyond offset 5000. At busy times the row cap, not the six-hour cap, determines coverage.
- Ranking supplement: `https://m.smzdm.com/sou/category_rank?page=1&limit=50&hour=N`.
- Ranking windows rotate through 1, 3, and 12 hours in 15-minute slots. Only one ranking request is made per run, and ranking membership never bypasses normal filters.
- Bounded late rechecks use `https://haojia-api.smzdm.com/detail/{article_id}` to refresh active status, price, mall, direct product link, and current interaction counts after a candidate has left both discovery feeds.
- Late rechecks select unsent snapshots no older than 18 hours when they previously had worthy >= 4 plus collection >= 4, worthy >= 6, or comments >= 8. They exclude IDs found by the current main/rank scan, use a backlog-scaled budget of 4–16 requests, wait at least 45 minutes per article, and prioritize never-checked rows nearest the age deadline before repeat checks.
- `faxian/list` and `youhui/list` channel endpoints opportunistically enrich `article_link`, `mall_no`, and `product_no`.
- `tongji_hudong` is preferred for comments, collection, worthy, and unworthy counts, with top-level fields as fallback.

## Filtering Pipeline

The weighted composite score is:

```text
comments * 3 + collection * 2 + worthy
```

Stage 1 has six paths:

- `超级好价`: comments >= 20, worthy >= 20, collection >= 10, score >= 120, score rate >= 90%.
- `均衡热度`: comments >= 6, worthy >= 8, collection >= 5, total engagement >= 25, score >= 65, score rate >= 90%.
- `高讨论`: comments >= 12, worthy >= 8, collection >= 5, total engagement >= 30, score >= 90, score rate >= 85%.
- `早期好价`: worthy >= 4, comments >= 6, collection >= 4, total engagement >= 16, score >= 35, score rate >= 95%.
- `早期强信号`: worthy >= 6, collection >= 8, total engagement >= 16, score >= 28, score rate >= 95%.
- `升温好价`: worthy >= 4, collection >= 4, total engagement >= 15, score >= 32, score rate >= 95%, plus qualifying snapshot growth with at least one new worthy vote.

Balanced, early, and warming paths have one controlled score-rate exception: at least six worthy votes, no more than one unworthy vote, and score rate >= 85%. Every other path threshold still applies. This prevents a single negative vote from vetoing a fast-growing `6:1` sample while retaining the global 85% floor; two negative votes do not receive this exception.

All paths require at least two worthy votes or two comments. Sold-out, timed-out, expired, and ended items are rejected.

There is no broad category or keyword blacklist. The title filter is intentionally narrow and blocks non-product reward/task posts such as variable `入会...京豆` forms, shop follow/add-to-cart/sign-in rewards, random-red-packet popups, points-to-Jingdou activities, non-guaranteed lucky bags, and ambiguous campaign pages. It normalizes full-width and decorated digits before matching. When changing these expressions, add both positive and negative regression samples; product false positives are a higher-risk failure than allowing one new activity-title variant through.

## Trend Logic

`candidate_snapshots` retains two days of candidate interaction history. Growth weights are worthy 3, collection 2, comments 4, and unworthy -2.

- Low-confidence `早期好价` and `早期强信号` candidates normally wait for another observation with meaningful growth and at least one new worthy vote.
- Already mature balanced candidates or highly discussed candidates can bypass this wait using the explicit thresholds in `CONFIG`.
- `升温好价` requires recent growth or sufficiently fast cumulative growth within its configured time window. Comment-only, collection-only, and very old slow growth do not qualify.
- Low-growth checks begin after about 25 minutes for low-comment candidates. Slow-growth checks handle long observation windows from 240 minutes.
- `超级好价` and `高讨论` are exempt from the slow-growth rejection; exact exemption lists are in `CONFIG`.

Filtered and failed-push items are not written to `history`, so later scans can reevaluate them as engagement changes.

Candidates can leave the feed before delayed comments and collections appear. `candidate_snapshots` therefore also seed the bounded late-recheck queue. Refreshed detail rows still pass every normal title, quality, trend, comment, anomaly, and deduplication stage; detail membership is not a bypass. Inactive rows are retired from the queue.

## Comment Checks

Comment samples come from:

```text
https://haojia.m.smzdm.com/detail_modul/user_related_modul?article_id={article_id}
```

This module usually exposes hot/related samples, not the complete comment pagination. The scraper excludes author comments using both `display_author` and module `author_smzdm_id`, records declared totals and sample counts, and may upgrade the list comment count when the module declares a larger total.

Level and concentration checks only become hard filters when the returned sample is representative: roughly 80% coverage or within two comments of the expected total.

- Lv5 and below is low level; Lv6 and above is high level.
- Low-level ratio above 35% is rejected.
- With at least four samples, fewer than three unique users or one user above 50% is rejected.
- A representative two-user high-level sample has a narrow mature-deal pass defined by `CONFIG`.
- Undercovered, missing, WAF-blocked, or budget-unavailable samples are diagnostic and do not block an interaction/trend-qualified deal by default.
- The dynamic budget checks all normally eligible candidates up to 80 external comment requests per run.

Legacy `pending_reviews` state remains for database compatibility, but unavailable-comment deferral is disabled.

## JD Handling

`jd_self_filter_enabled` is `False`. GitHub-hosted runners often receive empty shells, WAF pages, or redirects without reliable store and `isSelf` fields. JD deals therefore use the same title, engagement, trend, comment, anomaly, and dedup rules as other platforms.

Old JD lookup helpers remain behind the disabled flag. Do not enable hard rejection of unverified JD items without new runner-side evidence and false-negative testing.

## Deduplication

- Only successful pushes enter `history`.
- Article IDs, platform product keys, and normalized title fingerprints are checked.
- Fingerprints remain active for 30 days. Price text is normalized before fingerprinting.
- The same SKU or fingerprint may be pushed again when the parsed price is at least RMB 5 lower or at least 5% lower than the historical minimum.
- Successful push logs include article ID, quality path, parsed price, score rate, and engagement counts so Actions runs can be audited precisely.

## Network Stability

- GET requests use retry total 2, exponential backoff, and retries for HTTP 500/502/503/504.
- Connect/read timeouts are 8/20 seconds.
- Main-feed scanning stops after three consecutive failed pages and leaves recovery to the next scheduled run.
- External detail/comment calls use randomized throttling.
- HTTP 202/403/429, captcha markers, `probe.js`, or access-frequency responses suspend external checks for the rest of the run.
- Two consecutive comment or late-detail request failures also open the run-local circuit breaker.
- Late-detail traffic scales at 4% of the eligible backlog with a 4-request floor and 16-request hard cap. It is tracked independently in `late_recheck_state`; failure is diagnostic and does not stop main-feed discovery.

Keep the external request count bounded. New sources should first reuse existing JSON endpoints, have strict timeouts, and degrade without blocking the full scan.

## Deployment

`.github/workflows/smzdm.yml` accepts `workflow_dispatch` and `repository_dispatch` with event type `cron_trigger`. cron-job.org currently sends the external trigger every 15 minutes. There is deliberately no GitHub `schedule`, which avoids double triggering.

Manual dispatch has an optional `debug_article_id` input. It validates a numeric ID and prints matching `candidate_snapshots` plus successful-push history for the same ID, SKU, or fingerprint before the scan. Use this instead of guessing when a specific article is reported missing; leaving it blank has no effect on normal runs.

The workflow uses `concurrency: smzdm-scan`, a 12-minute job timeout, Python 3.11, and `actions/cache` for `smzdm.db`. Cache is not a transactional mutable store: closely spaced or retried runs can restore stale state. Do not claim exactly-once delivery without moving state to a more reliable store.

## Change Checklist

For filtering or parsing changes:

1. Confirm behavior against real API or Actions payloads; do not invent response fields.
2. Add focused positive and negative tests, especially for title regex and dedup fingerprints.
3. Run `python3 -m unittest -v` and `git diff --check`.
4. Keep README, AGENTS, and CLAUDE descriptions synchronized when behavior changes.
5. After deployment, review Actions summary counters and concrete successful-push log lines before tuning thresholds again.
