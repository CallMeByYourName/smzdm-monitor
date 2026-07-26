# CLAUDE.md

This file provides repository guidance to Claude Code. It reflects the implementation as of `2026-07-26`. `AGENTS.md` contains the complete engineering guide; keep both files aligned when application behavior changes.

## Project

SMZDM (什么值得买) deal monitor. `smzdm.py` performs one scan, filters candidates, sends accepted deals through WXPusher, saves state in SQLite, and exits. Scheduling is external; there is no local polling loop.

The project deliberately treats list engagement and snapshot growth as its primary evidence. SMZDM does not expose a reliable complete distribution of comment-user levels through the endpoint available to GitHub Actions, and JD store/self-operated fields are also unreliable on hosted runners. Missing external data is diagnostic rather than a default rejection.

## Commands

```bash
pip install -r requirements.txt
WXPUSHER_APP_TOKEN=xxx WXPUSHER_UID=xxx python3 smzdm.py
python3 -m unittest -v
```

`SMZDM_DB_PATH` optionally overrides the default `smzdm.db` path. The sole runtime dependency is `requests`.

## Inputs and Scan Scope

- Main source: `https://api.smzdm.com/v1/list?limit=100&offset=N`.
- Scan cap: 51 pages, 5100 rows, six hours, and only `faxian`/`youhui` channels. At peak volume the API row cap is reached before the time cap.
- Ranking source: one request per run to `https://m.smzdm.com/sou/category_rank`, returning up to 50 rows while 1-hour, 3-hour, and 12-hour windows rotate every 15 minutes. Ranking items still pass all normal filters.
- `faxian/list` and `youhui/list` enrich `article_link`, `mall_no`, and `product_no` when present.
- `tongji_hudong` supplies the preferred comment, collection, worthy, and unworthy counts.

## Quality Paths

Composite score:

```text
comments * 3 + collection * 2 + worthy
```

- `超级好价`: comments >= 20, worthy >= 20, collection >= 10, score >= 120, score rate >= 90%.
- `均衡热度`: comments >= 6, worthy >= 8, collection >= 5, engagement >= 25, score >= 65, score rate >= 90%.
- `高讨论`: comments >= 12, worthy >= 8, collection >= 5, engagement >= 30, score >= 90, score rate >= 85%.
- `早期好价`: worthy >= 4, comments >= 6, collection >= 4, engagement >= 16, score >= 35, score rate >= 95%.
- `早期强信号`: worthy >= 6, collection >= 8, engagement >= 16, score >= 28, score rate >= 95%.
- `升温好价`: worthy >= 4, collection >= 4, engagement >= 15, score >= 32, score rate >= 95%, plus qualifying snapshot growth with at least one new worthy vote.

Balanced, early, and warming paths tolerate one unworthy vote only when worthy >= 6 and the resulting score rate remains >= 85%. This is a narrow exception to the stricter path rate, not a global threshold reduction; all count, score, and trend requirements remain.

All paths require at least two worthy votes or two comments. Inactive items are rejected.

The script has no broad category blacklist. Its narrow normalized-regex filter targets non-product tasks and reward pages, including variable `入会...京豆`, follow/add-to-cart/sign-in rewards, points conversion, popup red packets, ambiguous campaign pages, and the concrete variants found in Actions logs. Add paired blocked and allowed regression tests whenever changing title expressions.

## Trends

`candidate_snapshots` retains two days of interactions. Growth weights are worthy 3, collection 2, comments 4, and unworthy -2.

- Low-confidence early paths wait for meaningful later growth, including at least one new worthy vote, unless current engagement meets a mature/discussion bypass.
- Warming deals require recent or sufficiently fast short-window growth. Comment-only, collection-only, or very old slow growth cannot qualify.
- Low-comment candidates can be filtered for weak growth from about 25 minutes and for slow long-window growth from 240 minutes.
- `超级好价` and `高讨论` retain explicit trend-filter exemptions in `CONFIG`.

Filtered or failed-push items do not enter push history, so they remain eligible for later reevaluation.

## Comments

Comment samples use `https://haojia.m.smzdm.com/detail_modul/user_related_modul?article_id={article_id}`. This is normally a hot/related module, not full pagination. Author comments are excluded, and the module total may upgrade the list comment count.

Hard level/concentration decisions require a representative module response: about 80% coverage or no more than two missing comments.

- Lv5 and below is low level; Lv6 and above is high level.
- Reject a representative sample when low-level ratio exceeds 35%.
- With at least four samples, reject fewer than three users or one user above 50%.
- Undercovered or unavailable data does not block a deal by default.
- The dynamic external-check budget covers all normally eligible candidates up to 80 per run.

`pending_reviews` remains for compatibility, but unavailable-comment deferral is disabled.

## JD and Deduplication

JD self-operated hard filtering is disabled (`jd_self_filter_enabled = False`) because hosted runners cannot reliably retrieve store or `isSelf` data. JD follows the same generic quality rules as every other platform.

Only successful pushes are recorded. Dedup uses article ID, platform product key when available, and normalized title fingerprint. Fingerprints remain active for 30 days. A matching SKU or fingerprint can be pushed again when the parsed price drops by at least RMB 5 or 5% from the previous minimum.

## Stability and Deployment

Requests use 8/20-second connect/read timeouts and two GET retries for transient 500-series responses. Three consecutive main-page failures stop the scan. External requests are randomly throttled; WAF/captcha responses or two consecutive comment failures suspend external checks for the rest of that run.

`.github/workflows/smzdm.yml` supports manual and `repository_dispatch` (`cron_trigger`) starts. cron-job.org triggers it every 15 minutes; GitHub schedule is intentionally absent. The job uses Python 3.11, `concurrency: smzdm-scan`, a 12-minute timeout, and `actions/cache` for SQLite.

Manual runs accept optional `debug_article_id`; the workflow prints that article's cached candidate snapshots before scanning. Use it to investigate missing items from real stored metrics.

`actions/cache` is not transactional state. Closely spaced or retried runs may restore an older database, so the current system cannot promise exactly-once notifications.

## Change Discipline

- Base parser and filter changes on real API payloads or Actions logs.
- Preserve bounded external traffic and graceful degradation when optional sources fail.
- Add focused tests for new title variants, parsing assumptions, trends, and dedup behavior.
- Run `python3 -m unittest -v` and `git diff --check` before committing.
- Update `README.md`, `AGENTS.md`, and this file together when thresholds, sources, scheduling, or notification behavior changes.
