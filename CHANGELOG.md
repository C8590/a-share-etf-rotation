# CHANGELOG

## V2 main-chain cleanup - 2026-05-20

The project has been consolidated around one current main strategy:

日频右侧确认型 ETF 动量轮动策略。

Key changes:

- Added ETF data, quote cache, universe, portfolio store, and A-share trading-calendar foundation.
- Added ETF symbol normalization as a shared storage utility.
- Removed old research strategy scaffolding from the active path.
- Added P0 `risk_warning` gate with R0-R4 scoring, manual risk events, next-day risk output, entry freeze, learning context, and frontend risk warning panel.
- Added trade safety policy controls so entry caps and freezes affect buy-side intent while sell, reduce, stop-loss, and exit actions remain available.
- Improved ETF data refresh, cache metadata, incremental updates, and data quality checks.
- Added ETF sector/theme/risk-group mapping and daily export support.
- Clarified UI around the single daily strategy, moved old output to historical reference, and fixed sidebar collapse/expand behavior.

## Current Safety Notes

- The project does not automatically place orders.
- The project does not guarantee returns.
- The project does not treat news or research reports as buy signals.
- `risk_warning` is an upper-level entry brake, not a news recommendation module.
- QMT-related work must remain behind explicit validation and must not be described as an automatic live-trading loop.

## Legacy v0.1 Notes

Earlier versions contained broader research scaffolding. Those records are retained only as historical context. They are not the current strategy entry point and should not be used as current trading guidance.
