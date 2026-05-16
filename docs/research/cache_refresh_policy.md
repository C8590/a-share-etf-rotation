# ETF-GAP-003C Legacy Cache Refresh Policy

Version: 1.0

This policy defines how to plan legacy cache refreshes safely. It does not authorize a full-market refresh, and it does not change strategy scoring, backtest returns, or QA gates.

## Why Not Refresh The Whole Market Directly

The current cache base is useful but not fully traceable:

- most legacy cache files do not have `data/cache_meta/*.json`;
- many symbols have `adjust=unknown`;
- some symbols have possible adjustment-related return jumps;
- a few symbols are missing local cache entirely;
- the data layer still fails QA because of quality failures and end-date coverage gap.

A full-market refresh would mix too many effects at once: source changes, adjustment changes, row-count changes, late-date coverage changes, and strategy ranking changes. If backtest or signal output moves afterward, it would be hard to explain which data change caused it. The project needs a dry-run plan, backup discipline, and before/after comparison first.

## Risks In Legacy Cache

Legacy cache without metadata means the CSV can be read, but its provenance is incomplete. The system may know the latest dates and prices, yet not know the exact endpoint, adjustment setting, fallback chain, or download timestamp.

`unknown_adjustment` is a traceability risk. It does not prove that the price is wrong, but it prevents a clean answer to whether the series is `qfq`, `hfq`, `none`, or source-dependent.

`possible_adjustment_issue` is a higher-risk signal. Large return jumps may be caused by dividends, splits, share conversion, real market moves, source changes, or bad cache merges. These symbols require human review before accepting refreshed data.

## Required Backup Before Refresh

Before any real refresh that overwrites an existing cache:

1. Copy `data/cache/{symbol}.csv` to a timestamped backup location.
2. Copy `data/cache_meta/{symbol}.json` if it exists.
3. Record file size, row count, start date, end date, source, adjustment, and checksum if available.
4. Keep the pre-refresh audit rows from `cache_metadata_audit.csv`, `adjustment_audit.csv`, `data_quality_report.csv`, and `data_failure_summary.csv`.

Missing cache has no old CSV to back up, but the plan must still record that it is a targeted download rather than an overwrite.

## Before/After Comparison

After a pilot refresh, compare old and new cache files before accepting the result:

- row-count delta;
- start/end date changes;
- overlapping date count;
- max absolute close-price difference;
- max percentage close-price difference;
- newly created metadata sidecar fields;
- adjustment audit status before/after;
- quality status before/after;
- failure summary before/after;
- strategy ranking movement for affected symbols.

Refresh is acceptable only when the data improvement is explainable. A newer end date alone is not enough if historical overlap changed unexpectedly.

## How To Judge Improvement

Metadata improves when:

- `metadata_exists` changes from false to true;
- `adjust` becomes explicit (`qfq`, `hfq`, or `none`) through a real sidecar, not a manual label rewrite;
- `cache_file`, `row_count`, `start_date`, and `end_date` match the cache;
- `fallback_chain` records the actual source path.

Adjustment audit improves when:

- `audit_status` moves toward `ok`;
- `unknown_adjustment_count` declines;
- possible adjustment issues are explained or isolated;
- abnormal returns do not worsen without explanation.

Quality improves when:

- `status` moves from `failed` to `warning` or `passed`;
- `primary_failure_type` is removed or becomes lower-risk;
- `end_date_gap_days` declines for stale symbols;
- no new missing OHLC, duplicate date, invalid OHLC, or abnormal return issue is introduced.

## Missing Cache

Missing cache symbols are `P0_missing_cache`, but they should be handled as a small targeted batch. They do not need backup of a missing CSV, but they do need:

- source/fallback traceability;
- metadata sidecar creation;
- quality audit after download;
- manual review if the first successful source is a fallback or if data starts too late.

## Abnormal Return

Abnormal return is not automatically bad data. It is a review trigger. If a symbol has `possible_adjustment_issue=True`, do not auto-refresh-and-accept it. Refresh can be used to gather better metadata, but acceptance requires comparing overlap and checking whether the jump is source/adjustment related.

## End-Date Coverage Gap

The target date must come from the local A-share trading calendar, not a cached max date or weekday guess. A stale cache can be refreshed in a pilot batch, but the resulting cache must be compared and audited. The goal is to reduce the true trading-calendar gap, not to hide the gap from QA.

## Batch Order

Recommended order:

1. `core_11`: refresh the original core basket first because it is small and strategy-relevant.
2. Small pilot: 10-20 mixed symbols covering legacy unknown, stale, quality failed, abnormal return, and missing cache.
3. Failed samples: symbols with missing cache, stale end date, and severe quality failures.
4. Broader legacy batch: legacy cache without metadata but no quality or adjustment red flags.
5. Full market: only after backup, comparison, and rollback workflows are proven.

## Manual Confirmation Required

Manual confirmation is required when:

- `possible_adjustment_issue=True`;
- `data_quality_failed=True`;
- `primary_failure_type=download_failed`;
- refreshed overlap prices differ materially from old overlap prices;
- metadata/cache row counts mismatch;
- adjustment changes from `unknown` to an explicit value but large historical differences appear;
- a strategy-held or top-ranked ETF changes materially;
- fallback source was used for a symbol that previously drove strategy output.

## Rollback Triggers

Rollback should be used when:

- refreshed cache has fewer usable rows without a clear reason;
- new cache loses recent coverage;
- overlap price differences are large and unexplained;
- quality status worsens;
- metadata is missing, unreadable, or mismatched after refresh;
- adjustment audit introduces a new `error_*` status;
- strategy output changes sharply and the data cause cannot be explained.

## Refresh Is Not Return Optimization

Refreshing cache is a data-governance action. It is meant to improve provenance, coverage, and auditability. It must not be treated as a way to improve strategy returns, tune backtest performance, or choose the most favorable data source. If refreshed data reduces historical performance but is more traceable and correct, the correct action is to keep the better data and update research conclusions.

## Dry-Run Framework

`python main.py plan-cache-refresh` generates `output/cache_refresh_plan.csv` without downloading data and without modifying `data/cache/*.csv` or `data/cache_meta/*.json`.

The plan classifies symbols by:

- `missing_cache`
- `legacy_cache_without_metadata`
- `unknown_adjustment`
- `possible_adjustment_issue`
- `stale_end_date`
- `data_quality_failed`
- `download_failed`

Priorities:

- `P0_missing_cache`
- `P0_stale_end_date`
- `P0_quality_failed`
- `P1_legacy_unknown_adjustment`
- `P1_possible_adjustment_issue`
- `P2_optional_refresh`

`safe_to_auto_refresh=True` means the symbol may be suitable for a future controlled batch refresh after backup support exists. It does not mean the current command will refresh it, and it does not mean refreshed data can be accepted without comparison.

## Pilot Refresh

`python main.py pilot-refresh --pool core_11 --max-count 11` is the first real-refresh workflow. Its purpose is to validate the mechanics of backup, download, metadata writing, comparison, and audit reporting on a small, strategy-relevant sample.

The pilot starts with `core_11` because:

- it is the original fixed research basket;
- it is small enough to inspect manually;
- it covers broad equity, style, sector, commodity, and cash-like ETF examples;
- changes in these symbols are easier to reason about before touching the full market.

The hard default maximum is 11 symbols. A larger batch is not a pilot; it is a production refresh and requires a separate task with stronger rollback controls.

`requires_manual_review=True` is skipped by default. Passing `--include-manual-review` is an explicit override for planning or a controlled run, but the resulting data still needs manual comparison before acceptance.

Each real pilot run creates a backup directory under `data/cache_backup/pilot_YYYYMMDD_HHMMSS/` with:

- old cache CSV files under `cache/`;
- old metadata JSON files under `cache_meta/` when present;
- `refresh_manifest.json` recording run id, time, symbols, copied files, command, and notes.

Rollback is manual and explicit:

1. Stop running refresh or QA commands.
2. Copy the backed-up `cache/{symbol}.csv` over `data/cache/{symbol}.csv`.
3. Copy the backed-up `cache_meta/{symbol}.json` over `data/cache_meta/{symbol}.json`, or remove the new metadata file only if the pre-refresh manifest shows no old metadata existed.
4. Re-run `main.py plan-cache-refresh` and `main.py qa-check`.
5. Confirm audit counts returned to the expected pre-refresh state.

A pilot is successful when:

- every attempted symbol has a backup record;
- refreshed symbols write metadata sidecars;
- `pilot_refresh_report.csv` records old/new date ranges and row deltas;
- overlap price differences are explainable;
- adjustment and metadata audits improve or remain explainable;
- data quality does not worsen;
- QA failure reasons remain real and are not masked.

Pilot success is a prerequisite for `ETF-GAP-003E` missing-cache targeted repair or any larger P1 legacy refresh batch. If download failures dominate, pause and diagnose AKShare/network stability before increasing scope.

## Missing Cache Targeted Repair

`python main.py repair-missing-cache` is a narrow repair workflow for `P0_missing_cache` rows in `output/cache_refresh_plan.csv`. It is not a market refresh command. By default it only attempts symbols currently classified as missing cache, and the command refuses batches larger than 10 symbols.

Missing cache is repaired separately because it is a different risk from stale or legacy cache:

- there is no existing price history to compare against;
- a successful repair changes ETF availability in downstream coverage checks;
- a failed repair should remain visible as `download_failed`, not be hidden by placeholder data;
- the result needs a compact report that explains whether a real cache was written.

Fake data is never acceptable for missing-cache repair. A zero-filled CSV, copied neighbor ETF, partial hand-entered series, or synthetic history would pollute data quality, universe filters, and backtests. If the existing downloader cannot fetch a symbol, the correct output is `download_failed` in `output/missing_cache_repair_report.csv`.

Repair behavior:

- symbols are selected from `refresh_priority=P0_missing_cache` unless `--symbols` is passed;
- explicit symbols are still capped by `--max-count`;
- symbols that already have `data/cache/{symbol}.csv` are skipped by default;
- old metadata is backed up when present;
- successful downloads write both `data/cache/{symbol}.csv` and `data/cache_meta/{symbol}.json`;
- failed downloads do not write cache files.

After a repair run:

1. Run `python main.py plan-cache-refresh`.
2. Run `python main.py qa-check`.
3. Inspect `output/missing_cache_repair_report.csv`, `output/cache_refresh_plan.csv`, `output/data_failure_summary.csv`, and `output/qa_report.json`.

A repair can still end as `quality_failed_after_repair`. That means a real cache was downloaded and metadata was written, but the resulting data failed the normal quality checks, for example because the ETF is newly listed, illiquid, has too few rows, or contains invalid source values. This is not a reason to loosen QA gates.

Failure triage:

- repeated connection/proxy/API errors point to data-source or network instability;
- incompatible fields or storage normalization errors point to downloader/schema code;
- empty data for a valid API call may mean the symbol is too new, inactive, delisted, or unavailable in the current source;
- quality failure after a valid download should be reviewed as ETF eligibility, not automatically treated as a downloader bug.

Missing-cache repair differs from full-market refresh: it only fills absent cache files for a tiny P0 set, while full refresh overwrites existing histories and requires broader comparison and rollback controls. A successful missing-cache repair does not authorize batch refresh of P1 legacy unknown adjustment symbols.

## Source Preference Evaluation

`python main.py eval-source-preference --max-count 12` evaluates a small source sample without refreshing formal caches. It writes temporary per-candidate CSV files under `data/source_eval/source_eval_YYYYMMDD_HHMMSS/` and writes the audit result to `output/source_preference_audit.csv`.

This workflow exists because pilot refresh and missing-cache repair showed that Sina can return data and metadata can be written, but `adjust=unknown` remains unresolved. The source preference evaluation asks whether `akshare.fund_etf_hist_em(adjust="qfq")` is trustworthy enough to become a future preferred source.

It is not a refresh command:

- it must not overwrite `data/cache/*.csv`;
- it must not overwrite `data/cache_meta/*.json`;
- it must not change downloader source priority;
- it must not change strategy scores, backtest returns, UI output, or QA gate thresholds.

Promotion rules are conservative. EM qfq is safe to promote only when it succeeds, has sufficient rows, passes schema and quality checks, is not less recent than Sina, is not materially shorter than Sina, and has no unexplained overlap differences. If EM qfq fails or has materially fewer rows, keep Sina fallback. If EM qfq succeeds but overlap close or return behavior differs materially from Sina, require manual review before any source-priority change.

`em_none` is kept as a diagnostic comparator. It can explain whether EM endpoint availability is broader than qfq, but it should not replace qfq when qfq is healthy.

## Source Connectivity Diagnostics

`python main.py diagnose-source --symbols 510300,159915,560320` is the inserted ETF-GAP-003F-A workflow for diagnosing EastMoney connectivity before any source-priority change. It is deliberately narrower than refresh or source promotion:

- it does not overwrite `data/cache/*.csv`;
- it does not overwrite `data/cache_meta/*.json`;
- it does not refresh the full market;
- it does not change downloader source priority;
- it does not change strategy scoring, backtest returns, UI output, or QA gate thresholds.

The command writes `output/source_diagnostics_report.csv`. Read one run by grouping on `run_id`, then compare each `symbol` across `akshare_sina`, `akshare_em_qfq`, `akshare_em_none`, and `raw_endpoint_probe`. The `proxy_env` row applies to the whole run. `error_type`, `error_message`, `diagnosis`, and `suggested_action` are intentionally preserved so a failed EM run can be separated into proxy/network failure, endpoint/HTTP failure, timeout/rate failure, AKShare parameter failure, or response-schema/empty-data failure.

If EM qfq and EM none still fail while Sina succeeds, keep Sina as the current primary path and do not enter ETF-GAP-003G. If diagnostics show proxy or endpoint issues were fixed, rerun `python main.py eval-source-preference --max-count 12`. ETF-GAP-003G is allowed only after EM qfq success and safe-to-promote counts improve enough to make a small controlled source-priority switch explainable.

This diagnostic step does not replace cache refresh planning. It simply answers whether the explicit EM qfq source is reachable enough to be evaluated. Cache refresh, missing-cache repair, and legacy metadata governance remain separate workflows with their own backup and comparison requirements.
