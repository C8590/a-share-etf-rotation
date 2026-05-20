# AI Handoff

## Current Positioning

This repository is now centered on one production candidate workflow:

日频右侧确认型 ETF 动量轮动策略。

The system is an observation, review, and manual decision-support tool. It does not guarantee returns, does not predict price moves, does not place broker orders, and does not connect QMT as an automatic trading loop.

## Main Chain

1. ETF data refresh and quality checks.
2. Trading calendar and execution-date alignment.
3. ETF symbol normalization.
4. Daily momentum rotation signal generation.
5. P0 `risk_warning` entry gate.
6. `trade_policy` safety controls.
7. ETF sector/theme/risk-group mapping.
8. Streamlit UI for current signal, holdings, data quality, risk warning, and historical reference.
9. learning context for attribution and review.

Risk priority:

```text
risk_warning > market_state > sector_rank > etf_rank > entry_signal
```

## Important Boundaries

- `risk_warning` is a brake, not a news strategy.
- R3/R4 risk freezes new entry and add-on buys, but must not block sell, reduce, stop-loss, or exit actions.
- `trade_policy` creates safety suggestions and does not send orders.
- QMT-related files, if present, are not an automatic live-trading loop unless a later task explicitly validates that boundary.
- Historical or legacy signal output is only for comparison and is not the current trading suggestion source.

## Validation Habit

Before committing a focused line:

1. Stage only the explicit whitelist.
2. Run the relevant tests.
3. Run `python -m pytest -q`.
4. If the worktree is dirty, validate with a temporary clean worktree using only the staged patch.
5. Recheck `git diff --cached --name-status` before committing.

This repository has had several independent lines landing close together, so commit isolation matters.
