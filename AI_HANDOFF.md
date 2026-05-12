# AI Handoff

## Project Summary

This repository is an A-share ETF low-frequency rotation signal system. It is for research, review, and manual observation only. It does not place trades, does not connect to a broker, and must not be treated as an automatic trading system.

Local runtime is working with Python 3.12.10 in the project virtual environment:

- Project venv: `D:\HUB\a-share-etf-rotation\.venv`
- Use `.\.venv\Scripts\python.exe` from the repo root for validation.

## Latest Validation

Validation rerun on 2026-05-12 after adding CLI support for manual `compare-signal --signal-date`.

- `.\.venv\Scripts\python.exe -m py_compile app.py main.py strategy\etf_rotation.py backtest\engine.py signal\weekly_signal.py ui\signal_parser.py`
  - Passed.
- `.\.venv\Scripts\python.exe -m unittest`
  - Passed: 9 tests ran, OK.
  - Added `tests/__init__.py` so the exact default unittest command discovers `tests/test_rebalance_dates.py`.
- `.\.venv\Scripts\python.exe main.py qa-check`
  - Passed.
  - Data layer: PASSED.
  - Strategy layer: PASSED.
  - Output layer: PASSED.
  - Allow 1000-3000 CNY small observation: YES.
  - Blocking reasons: None.
- `.\.venv\Scripts\python.exe main.py compare-signal`
  - Passed.
  - Generated `output/compare_signal.txt` and `output/compare_signal.csv`.
  - Default run used generated strategy signal dates; `requested_signal_date` is blank in the CSV output.
- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08`
  - Passed.
  - Generated `output/compare_signal.txt` and `output/compare_signal.csv`.
  - Output shows `requested_signal_date=2026-05-08`, `effective_signal_date=2026-05-08`, and `execute_date=2026-05-11`.

## Manual Signal Date Semantics

The intended feature is manual selection of a concrete `signal_date`.

- `requested_signal_date`: the date selected by the user.
- `effective_signal_date`: the actual trading day used for signal calculation.
- `execute_date`: the trading day immediately after `effective_signal_date`.

For a non-trading selected date, the implementation should resolve to the most recent previous trading day. It must not roll forward to future market data.

## Current Implementation Notes

- `main.py` now exposes `compare-signal --signal-date YYYY-MM-DD` and passes it into `_latest_strategy_signal(..., requested_signal_date=...)`.
- `_resolve_effective_signal_date` resolves the requested date against available market dates using `<= requested`, so non-trading days resolve backward.
- `compare_signal.csv` includes `requested_signal_date`, `effective_signal_date`, and `execute_date`.
- `generate_weekly_signal_text` can accept an explicit `signal_date`.
- The Streamlit UI still contains transitional rule-based monthly controls (`month_end`, `month_start`, `nth_trading_day`, `day_of_month`). That is not the final desired UX for manual concrete date selection.

## Core File Check

The following files are not modified in the current worktree or staging area:

- `data/downloader.py`
- `data/quality.py`
- `strategy/base.py`

No rollback was needed for those files because there is no current diff for them. Any earlier data freshness or base strategy edits are no longer present in the active diff.

## Files Modified In This Worktree

Tracked files currently modified:

- `app.py`
- `backtest/engine.py`
- `config/strategy_equal_weight_monthly.yaml`
- `config/strategy_reduced_equal_weight_monthly.yaml`
- `main.py`
- `signal/weekly_signal.py`
- `strategy/etf_rotation.py`
- `ui/signal_parser.py`

Untracked files/directories currently present:

- `AI_HANDOFF.md`
- `tests/`

## Key Behavior Changes

- Monthly rebalance rule plumbing was added to strategy config, engine, signal generation, compare signal output, and Web display.
- Manual CLI date selection is now available for `compare-signal`.
- `compare-signal --signal-date 2026-05-08` generates manual-date output with separate requested, effective, and execute dates.
- No ETF pool changes were intended.
- No strategy selection logic changes were intended.
- No trading cost model changes were intended.

## Recommended Next Tasks

1. Replace the Streamlit sidebar schedule form with a concrete date picker for manual `signal_date`.
2. Add UI display for `requested_signal_date`, `effective_signal_date`, and `execute_date`.
3. Add explicit unit tests for `_resolve_effective_signal_date`:
   - selected trading day
   - selected weekend/non-trading day
   - selected date before available data
   - selected date at latest available data with no next execute date
4. Decide whether the older `signal_date` CSV field should remain as an alias for `effective_signal_date` or be renamed/removed in a later cleanup.
