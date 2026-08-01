# SMZDM monitor review - 2026-08-01

This report records observed behavior before the `2026-08-01` title and late-recheck fixes. It is historical evidence, not the current configuration source of truth.

## Actions sample

- Window: `2026-07-31 22:45` through `2026-08-01 22:30` China time.
- Runs reviewed: 96; all completed successfully on commit `919dbca`.
- Average created-to-complete time: 222 seconds. The maximum was 540 seconds and included concurrency queue time; it remained below the 12-minute workflow timeout.
- Main feed rows: 461,699. Main-page failures and early aborts: 0.
- Rank rows: 4,800. The 1, 3, and 12-hour windows each ran 32 times. Rank unavailable responses: 0.
- Late detail requests/fetches: 841/841. Late detail unavailable responses: 0.
- External circuit breakers: 0.
- Successful pushes: 119, across 63 runs.
- Push paths: 48 early deals, 32 warming deals, 21 early strong signals, 9 balanced deals, and 9 high-discussion deals.
- Comment samples unavailable: 273, mostly under-representative samples; these remained diagnostic and did not block by default.
- Representative comment-level filters: 46 events across 10 unique titles. Several titles later passed as samples changed, so this check remains limited to representative responses.

## Ranking audit

Live 3, 12, and 24-hour ranking responses were fetched from the production JSON endpoint and recomputed with the current stage-one rules.

### 3-hour rank

- 50 rows: 7 had been pushed in the reviewed day, 28 failed current scoring, 3 were task titles, and 12 currently passed stage one.
- Five of the 12 were historical fingerprint/SKU duplicates.
- One, the Lion toothpaste article `179675308`, gained worthy votes and was pushed in the next run.
- The remaining six were deliberately waiting because comments or collections increased without a new worthy vote. Their current interaction levels did not meet the mature bypass. There was no evidence to justify weakening trend confirmation.

### 12-hour rank

- 50 rows: 22 had been pushed in the reviewed day, 17 were task titles, 7 failed current scoring, and 4 currently passed.
- All four passing rows were already in successful-push history and were correctly deduplicated.

### 24-hour rank

- The endpoint returned 50 rows successfully.
- 29 were task/reward titles, 7 failed current scoring, 5 had been pushed in the reviewed day, and 9 currently passed.
- All nine passing rows were already in successful-push history, including the Avene mask, CUKTECH power products, Mengniu milk, and fresh-food entries.
- Adding 24 hours to the production rotation would therefore have found no new qualifying deal in this sample, while reducing the polling frequency of the higher-value 1/3/12-hour windows. It remains disabled.

## Defects found

At least 16 of 119 pushes were not purchasable product offers. Real examples included JD livestream rooms, mobile-hall Jingdou, membership lucky draws, reward-day/E-card sequences, points-to-Jingdou pages, store-only titles, and coupon roundups. This was about 13% of the reviewed pushes.

The late-recheck summary selected 1,081 rows but made only 841 requests. Blocked legacy task titles consumed roughly 22% of selected queue slots before being retired, reducing useful late-detail coverage even though they did not generate external requests.

## Changes made

- Added narrow rules for the concrete task-title families observed above, with allowed product-title counterexamples.
- Moved title rejection before trend snapshot persistence.
- Made late rechecks overfetch local rows, retire blocked titles, and backfill the unchanged 4-16 external-request budget with valid products.
- Updated GitHub actions to Node 24-based major versions to remove runner deprecation warnings.
- Kept quality and trend thresholds unchanged because the live ranking audit did not show a qualifying non-duplicate being lost by those thresholds.
