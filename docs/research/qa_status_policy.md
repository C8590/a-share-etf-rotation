# ETF-GAP-QA-001 QA Status Policy

This policy explains why `qa-check` can remain failed while the data-governance phase is still considered covered and reproducible. It adds interpretation and next actions only. It does not relax QA, refresh cache, clear blockers, change strategy scoring, modify backtest returns, change UI behavior, or replace `compare_signal`.

## Why QA Still Fails

The current hard failures are:

- `data quality failed for 244 ETF(s)`
- `ETF end-date coverage gap is 15 days`

The first failure is governed by `data_quality_diagnosis`, `candidate_gate`, and `short_history_observation_pool`. It remains a hard QA failure because full-market QA requires all included ETF data to be usable, but the failure is not a full-market refresh task when the root cause is short history.

The second failure is a coverage freshness issue. It may require a controlled `update-data` run or source-lag diagnosis, but this policy does not execute that refresh. If `source_lag_report.csv` identifies a single-symbol provider-stale blocker, QA status should use `actionability=source_diagnosis` instead of treating the item as an ordinary full-market refresh task.

## Source Lag Is Not A Full-Market Refresh Task

`source_diagnosis` means the stale symbol trails the broader market cache and should stay blocked while provider/source evidence is diagnosed. For example, if `560000` remains at `2026-04-30` while the market max cache date is `2026-05-13`, the right conclusion is source lag/provider stale, not "refresh every ETF again."

Source-lag rows must remain excluded from the candidate pool and from ETF-GAP-008B until the provider path recovers or a controlled targeted refresh proves the cache can advance.

## Short History Is Not A Refresh Task

`short_history` and `very_short_history` mean local data exists but the ETF has not accumulated the minimum required rows. Refreshing the same source cannot manufacture trading history. These ETFs must stay excluded from the candidate pool and be observed until enough trading days accumulate, then governance reports and `qa-check` should be rerun.

## Coverage Gap May Need Controlled Refresh Or Source Diagnosis

An end-date coverage gap means at least one local cache is behind the expected date or the upstream source is lagging/unreachable. This is the only current QA failure that may point to a refresh path, and it must be handled in a controlled environment with explicit `update-data`, source diagnostics, and post-refresh QA.

## Manual Review Cannot Be Auto-Cleared

Rows in `manual_review_list` remain blocked until a human checks the listed evidence. Abnormal returns, low liquidity, unknown data findings, and source/adjustment concerns can lead to different outcomes. No command in this phase should automatically clear `manual_review_required`.

## Factor Gate Blocks 008B

`factor_score_gate` blocks ETF-GAP-008B when factor coverage, computability, short-history bias, benchmark dependencies, or NAV/IOPV dependencies are not ready. Factor scores stay observation-only until the gate is clean.

## Benchmark Availability Blocks 007B

ETF-GAP-007B depends on usable benchmark/index evidence. When `usable_benchmark_count=0`, rerun `diagnose-index-source` and `update-index-data` in a network-enabled environment, then verify that `data/index_cache/*.csv` is schema-valid before considering 007B.

## When QA Can Move From Governed Failure To Usable

QA can move toward usable only when all of the following are true:

- short-history ETFs have enough rows or are removed from the candidate universe by policy
- manual-review rows have recorded human decisions and remain blocked unless explicitly cleared
- coverage gap is resolved by controlled refresh or confirmed source recovery
- candidate gate has eligible rows
- factor gate no longer blocks 008B
- usable benchmark count is positive before 007B
- `qa-check` passes without changing cache or formal strategy outputs implicitly

## Reports

- `output/qa_status_breakdown.csv`: one row per QA blocker or governance state, with actionability and next action.
- `output/qa_status_summary.csv`: aggregate counts by actionability and stage blockers.
- `output/source_lag_report.csv`: single-symbol coverage-gap driver classification for provider-stale/source-unavailable cases.
- `qa_report.json -> data_layer.qa_status`: machine-readable summary for top-level QA review.
- `data_governance_status.json -> qa_status`: governance-level summary used by the runbook.
