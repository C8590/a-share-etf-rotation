# ETF-GAP-DQ-001 Data Quality Diagnosis Policy

This policy defines the second-pass diagnosis layer for ETF data-quality failures. It does not change the existing QA hard gates, strategy scoring, backtest outputs, UI outputs, or cached price data.

## Why 244 Failures Cannot Be Fixed In One Batch

The 244 `data_quality_report.csv` failures share the same hard-gate outcome, but they do not share the same root cause. A newly listed ETF with 15 rows, an old ETF with a truncated local cache, a missing cache, an abnormal return jump, and a stale end date all require different remediation. Treating them as one batch would either refresh too broadly, accept unsafe data, or turn structural ineligibility into a misleading strategy score.

The diagnosis layer therefore writes one row per failed ETF to `output/data_quality_diagnosis.csv` and separates:

- short or very short history
- old ETF cache incompleteness
- missing cache
- stale cache
- price-field and OHLC anomalies
- missing values and duplicate dates
- abnormal returns
- low liquidity
- metadata enrichment gaps
- candidate-pool eligibility

## Insufficient Rows: New ETF vs Old Incomplete Cache

`insufficient_rows` is not automatically a data-source failure. The diagnosis compares `row_count`, `first_date`, `latest_expected_date`, and cache existence.

If the first available date is recent and `row_count < min_required_rows`, the ETF is classified as `new_etf_short_history` with `history_status=short_history` or `very_short_history`. The remediation is observation, not refresh.

If the ETF has an old first date but too few rows, it is classified as `old_etf_cache_incomplete`. That is a targeted refresh candidate because the local cache is likely incomplete.

Missing cache is classified separately as `cache_status=missing` and `strategy_eligibility=blocked_missing_cache`.

## Why Short History Pollutes Factor Scores

Short history reduces or removes the lookback windows used by momentum, drawdown, volatility, liquidity stability, and completeness factors. If short-history ETFs are scored anyway, the score can become a proxy for data availability rather than ETF quality.

For that reason, `blocked_short_history` is a candidate-pool gate. It is not a low score, and it must not be converted into a penalty that still allows ranking.

## Stale Cache vs Source Lag

Stale cache means the local cache `last_date` is behind `latest_expected_date` beyond the configured tolerance. It can be a targeted refresh signal.

Source lag means the upstream source itself has not published or returned the expected date. This cannot be proven from the cache alone. When source lag is suspected, the ETF remains blocked until a targeted source check or manual confirmation explains the gap. The diagnosis report records the reason and keeps QA hard gates unchanged.

## Observation Only

ETF rows should remain `observation_only` when the data exists but is not ready for candidate use, especially:

- short or very short history
- low liquidity without structural price corruption
- metadata gaps that do not invalidate price data

Observation-only rows may be watched, but they must not enter candidate construction.

## Candidate Pool Gate

The candidate pool must exclude rows with:

- `blocked_short_history`
- `blocked_quality_failed`
- `blocked_missing_cache`
- `blocked_manual_review`
- `observation_only` low-liquidity status when the strategy requires tradability

This gate is deliberately upstream of strategy scoring so that quality problems do not become scores.

## Refresh Candidates

Refresh is recommended only when the diagnosis points to a targeted data-cache problem:

- `cache_status=missing`
- `cache_status=stale`
- `cache_status=severely_stale`
- `primary_failure_type=old_etf_cache_incomplete`

A refresh recommendation is not a QA pass. Refreshed data must still be compared and rechecked before any strategy use.

## Manual Confirmation

Manual review is required for:

- abnormal returns
- invalid OHLC relationships
- missing required price fields
- missing values in required fields
- duplicate dates
- unknown recurring failure reasons

These may indicate source adjustment problems, schema drift, bad merges, or real market events. The diagnosis report blocks candidate use until they are confirmed.

## No QA Relaxation

This task adds attribution and priority only. It does not:

- loosen `qa-check`
- treat `insufficient_rows` as passed
- delete ETFs
- refresh full-market data
- modify price data
- modify strategy scoring
- generate candidates
- replace `compare_signal`
- modify backtest returns
- change UI behavior
- add a new external source

Existing data-layer hard gates continue to fail when ETF quality failures or end-date coverage gaps remain.
