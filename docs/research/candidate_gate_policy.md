# ETF-GAP-DQ-002 Candidate Gate Policy

This policy defines the pre-candidate data gate. It converts `output/data_quality_diagnosis.csv` and factor-score gate evidence into a separate candidate eligibility report. It does not change the current formal strategy, backtest returns, UI, cache files, or `compare_signal`.

## Why A Pre-Candidate Gate Is Needed

Factor scoring and candidate construction need a clean input universe. Data-quality failures, short history, manual-review items, and unavailable factor evidence must be stopped before they become ranks or strategy candidates.

`output/candidate_gate.csv` is that stop line. It records one row per symbol present in diagnosis or factor-score evidence and explains whether the row is eligible, observation-only, or blocked.

## Short History Is Not A Low Score

Short history means the ETF does not yet have enough rows for stable momentum, volatility, drawdown, liquidity, or completeness evidence. A short-history ETF may look artificially strong or weak because the lookback window is truncated.

Therefore `blocked_short_history` is a gate status, not a bearish score. It must not be converted into a low factor score.

## Manual Review Must Block

Rows with `requires_manual_review=True` are blocked as `blocked_manual_review`. This includes abnormal returns, unknown recurring validation findings, invalid OHLC, missing required fields, duplicate dates, and other issues that may reflect source or adjustment problems.

Manual review is required before any candidate use or refresh acceptance.

## Low Liquidity Handling

Low liquidity is carried in `observation_reason=low_liquidity`. It is not automatically the only hard block because the row may already be blocked by short history or manual review. If the row has no stronger blocker, it should remain observation-only until tradability improves or a strategy explicitly defines a liquidity gate.

Low liquidity is not a score penalty.

## Factor Score Gate Relationship

`factor_score_gate.csv` is a global gate for whether factor scores may drive future candidate research. If it is `blocked_for_strategy_use`, otherwise clean rows are marked `blocked_factor_gate`.

More specific row-level blockers take precedence:

- `blocked_manual_review`
- `blocked_short_history`
- `blocked_quality_failed`
- `blocked_no_used_factors`
- `blocked_factor_gate`

`no_used_factors` means the ETF is unscoreable with current evidence. It is not a low score.

## Candidate Gate Is Not A Formal Strategy

Candidate gate output is an input constraint for future research. It does not:

- replace `compare_signal`
- create a formal candidate strategy
- modify current strategy holdings or rankings
- modify backtest returns
- relax QA
- refresh cache data

## Entry Criteria For ETF-GAP-008B

ETF-GAP-008B should not proceed until:

- QA hard gates are not failing on ETF data quality
- candidate gate has no unresolved manual-review blockers
- short-history rows are excluded or have accumulated enough rows
- factor score gate is no longer `blocked_for_strategy_use`
- `no_used_factors` rows are not treated as low scores
- benchmark/NAV/metadata-dependent factors have confirmed coverage if used

Until then, factor score and candidate gate remain observation and research-audit artifacts only.
