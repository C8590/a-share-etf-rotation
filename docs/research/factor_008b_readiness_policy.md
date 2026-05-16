# ETF-GAP-008B Readiness Policy

`factor_008b_readiness.csv` is a precheck and remediation plan for ETF-GAP-008B.
It does not enter 008B, generate `factor_score_candidates.csv`, connect factor
scores to the current strategy, replace `compare_signal`, change backtest
returns, refresh cache, modify UI, or relax QA.

## Why 008B Is Currently Blocked

The current factor score output is useful as an audit artifact, but it is not
ready to drive an independent candidate strategy. Candidate gate has
`eligible=0`, factor gate is `blocked_for_strategy_use`, coverage is below the
minimum threshold, no-used-factor rows are too common, and all scoreable samples
are affected by short-history or insufficient-rows risk.

Benchmark-relative factors and premium/discount are also unavailable:

- `tracking_error` and `relative_return_60d` require schema-valid benchmark
  index cache.
- `discount_premium` requires NAV or IOPV data.
- `fund_size` and `management_fee` require confirmed metadata coverage before
  they can be enabled.

## 008B Entry Conditions

ETF-GAP-008B can be considered only when:

- QA hard gates pass.
- Candidate gate has eligible rows and no unresolved candidate blockers.
- Manual review blockers are clear.
- Factor gate status is `passed_for_candidate_research`.
- Computable ratio is at least `0.80`.
- Unable-to-score ratio is at most `0.20`.
- Short-history bias no longer dominates the scoreable sample.
- Benchmark, NAV/IOPV, and metadata dependencies are either available or safely
  excluded by documented config.

## Blocker Remediation

Short history is resolved by waiting for enough rows, then rerunning
`diagnose-data-quality`, `build-candidate-gate`, and factor scoring. It must not
be treated as a low score.

Manual review requires a human decision. The readiness report cannot auto-clear
abnormal-return, low-liquidity, or very-short-history evidence.

Benchmark dependencies require `diagnose-index-source`, `update-index-data`,
`compute-etf-metrics`, and factor scoring in a controlled network-enabled
environment.

Discount/premium requires NAV or IOPV. Exchange prices alone are not a
substitute, and missing premium/discount must not be filled with zero.

Metadata factors require reliable `fund_size` and `management_fee` coverage.
They should remain disabled until coverage is confirmed and config is reviewed.

`no_used_factors` means an ETF has no usable enabled evidence. It is not a
bearish signal and must not be converted into a low score.

## Candidate File Rule

`factor_score_candidates.csv` may be generated only after this readiness check
passes, QA remains clean, candidate gate has eligible rows, and a separate 008B
implementation step is explicitly approved.
