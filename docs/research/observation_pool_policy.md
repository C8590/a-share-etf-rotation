# ETF-GAP-DQ-003 Short-History Observation Pool Policy

This policy defines the short-history ETF observation pool. It is a follow-up tracking report only. It does not change the formal strategy, replace `compare_signal`, generate formal candidates, modify backtest returns, change UI behavior, refresh ETF or index caches, relax QA gates, or delete ETFs.

## Why Short-History ETFs Cannot Enter Candidates

Short-history ETFs do not yet have enough rows for stable momentum, volatility, drawdown, liquidity, benchmark-overlap, or factor-completeness evidence. Scoring them would make the score depend on data availability rather than ETF quality.

Therefore a short-history ETF stays outside candidate construction until it has enough rows and passes the existing candidate gate again. `short_history` is a temporary eligibility blocker, not a low score.

## Observation Pool Versus Candidate Pool

`output/short_history_observation_pool.csv` is a watchlist for future tracking. It answers: how many rows are missing, when the ETF might first have enough trading days, and whether low liquidity or manual review risk is present.

`output/candidate_gate.csv` is the eligibility gate for future candidate research. Observation-pool rows must not be promoted directly into a candidate strategy. They can only be reconsidered by rerunning the candidate gate after their data changes.

## Rows Needed

`rows_needed` is calculated per ETF as:

```text
max(min_required_rows - row_count, 0)
```

`estimated_trading_days_until_eligible` is the same value. It is not a QA pass. It only says how many additional trading rows would be needed before the minimum-history blocker can be revisited.

## Estimated Eligible Date

`estimated_calendar_date_until_eligible` is derived from `data/calendar/a_share_trading_calendar.csv` using future open trading days after the latest expected date. If the calendar snapshot does not contain enough future open dates, the field is `unknown`.

The estimate is intentionally conservative:

- it does not fabricate dates beyond the calendar snapshot
- it does not assume cache refresh or source repair
- it does not override manual-review or liquidity warnings
- it must be recomputed after new data arrives

## Low Liquidity Handling

Low liquidity is marked as `low_liquidity_flag=True` and usually maps to `P2_low_liquidity_watch` unless a stronger priority applies. It is not a permanent exclusion by itself and must not be converted into a low factor score.

Low-liquidity rows need a separate tradability check before candidate use, even after minimum history is reached.

## Manual Review Priority

`requires_manual_review=True` is always `P0_manual_review`. It takes precedence over ordinary waiting-for-history status because abnormal returns, price-field anomalies, duplicate dates, missing required fields, or unknown source issues may invalidate the history itself.

Manual-review rows cannot be promoted by waiting for more rows alone. They require confirmation before any candidate-gate reconsideration.

## Re-Entry Into Candidate Gate

An observation-pool ETF can be reconsidered only when all of the following are true:

- `row_count >= min_required_rows`
- data-quality checks no longer report short-history failure
- `requires_manual_review` is false or has been resolved by manual confirmation
- liquidity is acceptable for the intended strategy or explicitly handled by a strategy liquidity gate
- existing QA hard gates remain unchanged and are not relaxed
- `candidate_gate` is rerun from current reports

Until those conditions hold, the ETF remains observation-only.
