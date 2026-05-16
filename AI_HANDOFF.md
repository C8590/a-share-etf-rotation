# AI Handoff

## Project Summary

This repository is an A-share ETF low-frequency rotation signal system. It is for research, review, and manual observation only. It does not place trades, does not connect to a broker, and must not be treated as an automatic trading system.

Local runtime:

- Project venv: `D:\HUB\a-share-etf-rotation\.venv`
- Use `.\.venv\Scripts\python.exe` from the repo root for validation.

## Latest Status

Dynamic rotation product direction correction is complete.

What changed in this follow-up:

- `reduced_equal_weight_monthly` is now explicitly positioned as `固定篮子基准策略 / 精选等权配置策略`.
  - It remains a useful fixed selected-basket equal-weight benchmark.
  - It is no longer described as the main dynamic quantitative rotation strategy.
- `momentum_rotation_monthly` is now the next main observation candidate for real dynamic rotation.
  - It recalculates close-momentum, moving-average trend state, ETF ranking, and selected target ETFs for each `signal_date`.
  - `observation_cash` and current cash affect only manual trade sizing, not ETF selection.
- Updated `config/strategy_momentum_rotation_monthly.yaml` to the v1 product parameters:
  - Monthly / `month_end`.
  - 60-day close momentum.
  - 60-day moving average trend filter.
  - `max_positions: 3`.
  - `sell_rank_threshold: 5`.
  - Positive-momentum filter enabled.
  - Cash ETF fallback disabled.
- `strategy_type: rotation` in that config is accepted, but the top-level `strategy_name: momentum_rotation_monthly` routes it to the dedicated `MomentumRotationMonthlyStrategy` class.
- Current holding input is available in the Web page and persists to `config/current_position.yaml` with `cash`, `holdings`, and optional `current_empty`.
- Dynamic rotation trade planning is now usable:
  - buy plan,
  - sell plan,
  - continue-hold plan,
  - skipped-buy / no-action reasons,
  - sell reasons for not in target, rank threshold, moving-average break, momentum threshold, or missing data.
- `compare_signal.csv` now includes or confirms:
  - `strategy_name`
  - `strategy_display_name`
  - `strategy_type_description`
  - `requested_signal_date`
  - `effective_signal_date`
  - `execute_date`
  - `execution_window`
  - `execution_price_rule`
  - `observation_cash`
  - `current_holdings`
  - `target_symbols`
  - `target_weights`
  - `buy_plan`
  - `sell_plan`
  - `hold_plan`
  - `no_action_reason`
  - `rank_table_summary`
- `compare_signal.txt` now includes each strategy's Chinese display name, dynamic/static positioning, signal information, ranking summary, target portfolio, buy/sell/hold sections, no-action reasons, and risk warning.
- Added `compare-signal --strategy`, for example:
  - `.\.venv\Scripts\python.exe main.py compare-signal --strategy momentum_rotation_monthly --signal-date 2026-05-08`
  - `.\.venv\Scripts\python.exe main.py compare-signal --strategy momentum_rotation_monthly --signal-date 2025-05-08`
- Web strategy selector includes:
  - `动态量化轮动策略`
  - `固定篮子基准策略 / 精选等权配置策略`
  - `全池等权配置策略`
- Web strategy explanations now state:
  - Fixed basket: `该策略是固定篮子等权配置，目标 ETF 通常不会因日期变化而变化。`
  - Dynamic rotation: `该策略会根据所选信号日重新计算动量排名和趋势状态，目标 ETF 可能随日期变化。`

Validation rerun on 2026-05-12:

- `.\.venv\Scripts\python.exe -m py_compile app.py main.py data\downloader.py data\quality.py data\storage.py signal\weekly_signal.py ui\signal_parser.py strategy\etf_rotation.py strategy\indicators.py backtest\engine.py`
  - Passed.
- `.\.venv\Scripts\python.exe -m unittest`
  - Passed: 32 tests ran, OK.
- `.\.venv\Scripts\python.exe main.py qa-check`
  - Passed: Data layer, strategy layer, and output layer all PASSED.
- `.\.venv\Scripts\python.exe main.py compare-signal`
  - Passed.
- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08`
  - Passed.
- `.\.venv\Scripts\python.exe main.py compare-signal --strategy momentum_rotation_monthly --signal-date 2026-05-08`
  - Passed. Target ETFs: `159915,512480,588000`.
- `.\.venv\Scripts\python.exe main.py compare-signal --strategy momentum_rotation_monthly --signal-date 2025-05-08`
  - Passed. Target ETFs: `518880,159928,511880`.

Current completion status:

- 精选等权重新定位为基准策略: done.
- `momentum_rotation_monthly` dynamic ranking and target generation: done for v1.
- Current holding input: done.
- Dynamic rotation sell plan: done for target exclusion, rank threshold, moving-average break, momentum threshold, and missing-data cases.
- Buy / sell / hold / no-action plans: usable.

Remaining half-finished / product risks:

- `momentum_rotation_monthly` is still marked `research_observation_candidate`, not fully promoted to recommended observation.
- QA recommendations still list `equal_weight_monthly` and `reduced_equal_weight_monthly`; dynamic rotation needs more live review before recommendation status changes.
- The system uses local cached行情 only. If local data is stale, signals must be treated as stale; the Web page shows latest local data date.
- Execution date prices are not used for target selection. Actual成交价 still requires manual intraday confirmation.
- No broker API or automatic order placement exists or should be added.

## Previous Latest Status

Date rollover fix is complete.

What changed in this follow-up:

- Fixed `main.py compare-signal --signal-date YYYY-MM-DD` so a manual signal date on the latest local data day no longer fails or falls back to an older month just because the next trading day's local行情 is not available yet.
- `compare_signal.csv` now writes explicit `signal_date_source` values:
  - `manual` when `--signal-date` is provided.
  - `auto` when using latest available data.
- If the effective signal date is the latest local data date and there is no later local execute-date行情, `execute_date` is now `下一交易日，待数据确认`.
- Automatic compare-signal now uses the latest scheduled signal date even when execution data is still pending, instead of selecting only dates that already have a later execution row.
- Added Web-side date validation in `app.py`:
  - Manual button state records the requested date and requires manual output.
  - Page selected date must match `requested_signal_date` for manual output.
  - `effective_signal_date` cannot be later than `requested_signal_date`.
  - If local trading dates exist between effective and requested dates, the page blocks the result as an invalid old fallback.
  - `execute_date` must be later than `effective_signal_date`, unless it is the explicit pending next-trading-day text.
  - On validation failure, the page shows a red error and stops rendering signal details.
- The page continues to reload fresh `output/compare_signal.csv` and `output/compare_signal.txt` after command completion, and shows `信号文件更新时间`.

Validation rerun on 2026-05-12:

- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-11`
  - Passed.
  - Output: `requested_signal_date=2026-05-11`, `effective_signal_date=2026-05-11`, `signal_date_source=manual`, `execute_date=下一交易日，待数据确认`.
- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08`
  - Passed.
  - Output: `requested_signal_date=2026-05-08`, `effective_signal_date=2026-05-08`, `signal_date_source=manual`, `execute_date=2026-05-11`.
- `.\.venv\Scripts\python.exe main.py compare-signal`
  - Passed.
  - Main observation strategy output uses `effective_signal_date=2026-05-11`, `signal_date_source=auto`, `execute_date=下一交易日，待数据确认`.
- `.\.venv\Scripts\python.exe -m unittest`
  - Passed: 25 tests ran, OK.
- `.\.venv\Scripts\python.exe main.py qa-check`
  - Passed: Data layer, strategy layer, and output layer all PASSED.
- Web manual verification:
  - Started Streamlit with `.\.venv\Scripts\python.exe -m streamlit run app.py` on `http://localhost:8501/`.
  - Selected `2026-05-11` and clicked `按所选日期生成信号`.
  - Page showed `你选择的信号日: 2026-05-11`, `实际计算信号日: 2026-05-11`, `日期来源: 手动选择`, `预计执行日: 下一交易日，待数据确认`.
  - Page no longer showed `实际计算信号日: 2026-04-13` or `预计执行日: 2026-04-14`.

## Previous Latest Status

Current holdings input/edit/save is complete.

What changed in this follow-up:

- Added a Streamlit `当前持仓` module with editable `可用现金`, `ETF 代码`, `ETF 名称`, and `持有份额`.
- Users can add rows, delete rows, save current holdings, or choose `当前空仓`.
- Saved format is now:

```yaml
cash: 3000
current_empty: false
holdings:
  - symbol: "510300"
    shares: 100
```

- Legacy `positions: {}` files are still readable for compatibility, but new saves use `holdings`.
- If no usable holdings are configured and `当前空仓` is not selected, the UI and CLI show:
  `你还没有填写当前持仓。系统只能展示目标组合，无法生成完整买入/卖出计划。请先填写当前持仓和可用现金。`
- If `当前空仓` is selected, the UI and CLI show:
  `当前按空仓处理，本次只生成买入计划，不生成卖出计划。`
- `compare_signal.txt` now contains Chinese sections: `【当前持仓】`, `【买入计划】`, `【卖出计划】`, `【继续持有】`, `【不操作原因】`.
- `compare_signal.csv` now includes `current_cash`, `current_holdings`, `buy_plan`, `sell_plan`, `hold_plan`, and `no_action_reason`.
- Current holdings and cash are used only for manual trade planning: buy plan, sell plan, continue-hold plan, cash estimate, and share sizing. Target ETF selection is intentionally computed without current holdings or observation cash.
- The main UI avoids backend field names in normal sections. Technical outputs remain only in the collapsed `高级诊断信息` section.

Validation notes:

- Py_compile passed for `app.py main.py signal\weekly_signal.py ui\signal_parser.py strategy\etf_rotation.py backtest\engine.py`.
- Unit tests passed with 22 tests OK.
- CLI scenario checks passed:
  - Missing/unconfigured holdings produces the explicit incomplete-plan message.
  - `当前空仓` produces buy plans and no sell plan.
  - A holding outside the target basket produces a sell plan.
- Playwright verified the page can fill and save `当前持仓`, persist `config/current_position.yaml`, show recognized ETF names, and generate a sell plan when holding `511880` against the reduced equal-weight target basket.

## Previous Latest Status

Trading logic safety review v1 is complete.

What changed in this review:

- Added strict source-field checks in `data/downloader.py`: `date/open/high/low/close/volume/amount` must exist and parse as non-null values. Missing `close`, `high`, `low`, or `open` fails instead of falling back to another price field.
- Added strict cache normalization in `data/storage.py`: cached rows must preserve a real `source`, and `date/open/high/low/close/volume/amount/source` must be valid before saving or loading.
- Expanded `data/quality.py` OHLC checks: `high >= open/close`, `low <= open/close`, `high >= low`, positive OHLC, duplicate/order/future-date checks, abnormal close-equals-high/low/open warnings, and >20% daily return warnings.
- Tightened signal-date safety in `main.py` and `signal/weekly_signal.py`: manual signal dates later than latest data now fail; manual last-data-day signals fail if no next execute date exists; automatic signals choose the latest signal date that already has a later execution date.
- Removed the raw `compare_signal.csv` diagnostic table from the Web UI so normal pages do not expose backend field names.
- Added `tests/test_trading_logic_safety.py` with 10 trading-logic safety tests.

Review report:

- `TRADING_LOGIC_REVIEW.md`
- Current conclusion: no `high`/`low`/`open` as `close` substitution found, no current future-function issue found, no cash-affecting-selection issue found.
- Current data warning: `512100`, `512480`, and `159928` have at least one daily close return above 20%; QA still passes but these are marked for manual review.

Validation for this review:

- `.\.venv\Scripts\python.exe -m py_compile app.py main.py data\downloader.py data\quality.py data\storage.py signal\weekly_signal.py ui\signal_parser.py strategy\etf_rotation.py strategy\indicators.py backtest\engine.py` passed.
- `.\.venv\Scripts\python.exe -m unittest` passed, 19 tests OK.
- `.\.venv\Scripts\python.exe main.py qa-check` passed.
- `.\.venv\Scripts\python.exe main.py compare-signal` passed.
- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08` passed with execute date `2026-05-11`.

## Previous Status

Web observation-cash input is complete.

The Streamlit sidebar now has:

- `选择信号日`
- `观察资金金额（元）`
- `按所选日期生成信号`
- `更新本地行情数据`
- `检查数据和策略状态`
- `使用最新可用数据生成信号`

The observation cash defaults to `config/current_position.yaml` field `cash` when present, otherwise `10000`. The minimum is `1000`, step is `100`. The user-entered value takes priority for signal generation.

There is an optional checkbox `同时更新本地持仓现金`. It is off by default. The app does not force-overwrite `config/current_position.yaml`.

## Cash Semantics

- `backtest.initial_cash` remains the historical backtest initial capital.
- Web `观察资金金额（元）` is only the user's current observation/manual-trading reference capital.
- These two are not mixed.
- The Web and CLI observation cash changes the sizing advice only:
  - target amount
  - target weight display
  - buyable shares
  - 100-share lot rounding
  - estimated trade costs through the existing fee model
  - estimated remaining cash
- It does not change strategy selection logic, ETF pool, backtest initial cash, fee model, or any trading automation.

## Backend / CLI

`compare-signal` now supports:

```powershell
.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08 --cash 3000
.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08 --cash 10000
```

`signal/weekly_signal.py` accepts `observation_cash`. If omitted, it falls back to `config/current_position.yaml` cash.

`compare_signal.csv` now includes:

- `observation_cash`
- `target_amounts`
- existing buy advice with suggested shares and estimated buy notional
- existing skipped-buy advice with insufficient cash / target amount reasons
- `estimated_remaining_cash`

`compare_signal.txt` now includes the observation cash and target amount in each strategy section.

## Web Display

The main page now shows `本次观察资金` instead of `当前真实现金 10000.00 元`.

The main observation strategy section shows:

- target ETF
- target weight
- target amount
- suggested buy shares
- estimated buy amount
- estimated remaining cash
- whether the portfolio changed

If the amount is too small to buy target ETFs under the 100-share lot constraint, the page displays:

`当前资金可能不足以按 100 份整数交易单位买入目标 ETF，请增加观察资金或仅作为信号参考。`

Technical names such as `initial_cash`, `cash_amount`, and `portfolio_value` are not shown in the main UI.

## Date / Output Consistency

The Web app records the selected date and observation cash before running the backend command. After generation, Streamlit reruns and reloads `output/compare_signal.csv` / `output/compare_signal.txt` from disk. Dashboard data is not cached.

If output date or output observation cash does not match the current UI input, the page shows a red consistency error.

If an existing manual output is present, the date picker defaults to that requested date so a normal refresh does not create a false mismatch.

## Latest Validation

Validation rerun on 2026-05-12 after observation-cash support:

- `.\.venv\Scripts\python.exe -m py_compile app.py main.py signal\weekly_signal.py ui\signal_parser.py strategy\etf_rotation.py backtest\engine.py`
  - Passed.
- `.\.venv\Scripts\python.exe -m unittest`
  - Passed: 9 tests ran, OK.
- `.\.venv\Scripts\python.exe main.py qa-check`
  - Passed.
- `.\.venv\Scripts\python.exe main.py compare-signal`
  - Passed.
- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08`
  - Passed.
- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08 --cash 3000`
  - Passed. Output target amounts and buy shares changed for 3000 CNY sizing.
- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08 --cash 10000`
  - Passed. Output returned to 10000 CNY sizing.

Web checks:

- `streamlit.testing.v1.AppTest` rendered `app.py` with 0 exceptions.
- AppTest confirmed the `观察资金金额（元）` input exists and defaults to `10000` from `config/current_position.yaml`.
- AppTest generated with selected date `2026-05-08` and observation cash `3000`; the refreshed page showed `本次观察资金: 3000.00 元`, selected/effective `2026-05-08`, and execute date `2026-05-11`.
- AppTest confirmed the main UI does not expose `initial_cash`, `cash_amount`, or `portfolio_value`.

## Files Changed In This Follow-Up

- `signal/weekly_signal.py`
  - Added `observation_cash` override for signal sizing.
  - Signal text now includes observation cash and target amount per ETF.
- `main.py`
  - Added `compare-signal --cash`.
  - Added `observation_cash` and `target_amounts` to `compare_signal.csv`.
- `app.py`
  - Added sidebar cash input and optional write-back checkbox.
  - Web generation passes `--cash` to the backend.
  - Main overview displays `本次观察资金`.
  - Added output cash consistency check.
- `ui/signal_parser.py`
  - Parses observation cash and target amounts for display.
  - Target and buy tables include target amount.
- `ui/components.py`
  - Chinese empty/status text from prior Web cleanup remains.

## Guardrails Preserved

- Backtest `initial_cash` semantics are unchanged.
- ETF pool was not changed.
- Stock/ETF selection logic was not changed.
- Trading cost model was not changed.
- No broker API integration was added.
- No automatic trading behavior was added.

## 2026-05-12 Momentum Rotation Addition

`reduced_equal_weight_monthly` has been repositioned as the fixed-basket benchmark strategy. Its historical meaning is unchanged: it keeps a configured selected ETF basket and rebalances it to equal weights, so target ETFs usually do not change just because the signal date changes.

Added `momentum_rotation_monthly` as the dynamic quantitative rotation strategy:

- Config file: `config/strategy_momentum_rotation_monthly.yaml`.
- Strategy class: `MomentumRotationMonthlyStrategy` in `strategy/etf_rotation.py`.
- ETF pool comes from `config/etf_pool.yaml`.
- Ranking is recalculated for each `signal_date` using only close data available on or before that date.
- Default signal uses 20-day close momentum and a 60-day moving average filter.
- Filters require valid close/momentum/MA data, `close > ma`, and positive momentum when `enable_min_momentum_filter` is enabled.
- Selection takes the top `max_positions` ETFs by momentum after filters.
- `observation_cash` affects only target amount, share sizing, skipped-buy messages, and remaining cash; it does not affect target ETF selection.
- `compare_signal.csv` now includes `rank_table` JSON for each strategy, with symbol, name, close, momentum, MA, above-MA flag, rank, and selected flag.

Web page update:

- Sidebar now has strategy selection:
  - `稳健基准：精选等权配置`
  - `动态轮动：动量轮动`
- Fixed-basket strategies show the explanation: `该策略是固定篮子等权配置，目标 ETF 通常不会因日期变化而变化。`
- `momentum_rotation_monthly` shows the explanation: `该策略会根据所选信号日重新计算动量排名，目标 ETF 可能随日期变化。`
- The selected strategy page renders the momentum ranking table and buy/sell/hold reason fields from structured plan JSON.

Recommended next-stage observation candidate:

- Keep `reduced_equal_weight_monthly` as the stable benchmark.
- Use `momentum_rotation_monthly` as the next main observation candidate, but treat it as a research/observation candidate until it has enough live signal history and manual review.

## 2026-05-12 Streamlit Stability And Position Entry Fix

Streamlit DOM stability work was completed after the browser-side error:

`NotFoundError: 无法对“Node”执行“removeChild”：要删除的节点不是此节点的子节点。`

What changed:

- Reworked `app.py` into stable page sections: sidebar actions, overview, current position, selected strategy, strategy comparison, and advanced diagnostics.
- Removed forced `st.rerun()` calls from normal button flows.
- Added stable widget keys for sidebar inputs, current-position rows, action buttons, and diagnostics controls.
- Replaced dynamic technical tables with static `st.table` rendering to reduce frontend DOM churn and avoid English dataframe toolbar labels in the normal page.
- Normal page no longer shows JavaScript/DOM stack details; generic page failures show `页面渲染异常，请刷新页面或重启本地面板。`
- Detailed paths, raw output, and technical logs remain only under collapsed `高级诊断信息`.
- Cleaned UI labels in `ui/signal_parser.py` so ordinary pages use Chinese labels instead of internal fields such as `effective_signal_date`, `execute_date`, `day_of_month`, `manual`, or `auto`.

Current position entry is now available in the page:

- Module title: `当前持仓`.
- Supports available cash input.
- Supports ETF code and share input.
- Supports adding and deleting rows.
- Supports saving current holdings to `config/current_position.yaml`.
- Supports `当前空仓`, saved as `current_empty: true` with empty holdings.
- If neither holdings nor empty-position state is configured, the page shows:
  `你还没有填写当前持仓。系统只能展示目标组合，无法生成完整买入/卖出计划。`

Strategy messaging status:

- Fixed-basket strategies are explicitly labeled as fixed basket / equal-weight benchmarks.
- The page states: `当前策略为固定篮子等权配置。日期变化通常不会改变目标 ETF，只会影响估算价格、执行日期和再平衡金额。`
- `momentum_rotation_monthly` is the only strategy described as dynamic quantitative rotation.
- No remaining normal-page wording should imply that `reduced_equal_weight_monthly` is a dynamic momentum strategy.

Manual execution plan now displays:

- You selected signal date.
- Effective calculation signal date.
- Expected execution date.
- Suggested execution time: `09:35 - 10:00`.
- Price rule: manual limit order using live quotes, no automatic trading.
- Observation cash.
- Current holdings.
- Target portfolio.
- Buy, sell, hold, and no-action sections.
- Empty sell plans show: `无卖出计划：当前持仓为空，或当前持仓均在目标组合内。`

Validation rerun:

- `.\.venv\Scripts\python.exe -m py_compile app.py main.py signal\weekly_signal.py ui\signal_parser.py strategy\etf_rotation.py backtest\engine.py`
  - Passed.
- `.\.venv\Scripts\python.exe -m unittest`
  - Passed: 25 tests ran, OK.
- `.\.venv\Scripts\python.exe main.py qa-check`
  - Passed: Data layer, strategy layer, and output layer all PASSED.
- `.\.venv\Scripts\python.exe main.py compare-signal`
  - Passed.
- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08`
  - Passed.

Web verification:

- Started with `.\.venv\Scripts\python.exe -m streamlit run app.py` on `http://localhost:8501/`.
- Streamlit health endpoint returned `200 ok`.
- Playwright browser snapshot showed no `NotFoundError`, `removeChild`, `static/js`, or internal date-field names in the normal page.
- Verified current-position form is visible and can save `cash: 3000` plus `510300` / `100` shares.
- Verified `当前空仓` save writes empty holdings and displays the empty-position explanation.
- Verified a holding outside the dynamic target creates a sell plan after signal regeneration.
- Restored the original local `config/current_position.yaml` after web interaction testing and regenerated compare-signal output.

## 2026-05-12 Streamlit Readability And Density Pass

UI readability work was added in `app.py` without changing strategy logic.

What changed:

- Injected a broader Streamlit CSS pass to reduce page padding, title sizes, section heading sizes, card typography, input heights, button heights, table density, and alert spacing.
- Set the main content to a readable desktop max width around 1320px and tightened the sidebar to about 300px.
- Replaced the large `st.metric` signal overview with a compact responsive 2-row metric grid: 4 columns on desktop, 2 columns on narrower screens.
- Overview cards now allow wrapping instead of truncating important values such as `下一交易日，待数据确认`, `手动选择`, and `自动使用最新可用数据`.
- Shortened stale-data quality status to `今日数据待更新`; the longer explanation is now split between a short warning and a collapsed `查看说明` section.
- Replaced oversized current-position row inputs with a compact `st.data_editor` using Chinese columns: `ETF代码`, `ETF名称`, `持有份额`, and `操作`.
- The current-position empty state now uses the shorter message: `未填写当前持仓，暂只能展示目标组合。`
- Strategy-level summary metrics were also moved to the compact card grid so the page no longer reads like a presentation screen.

Validation rerun:

- `.\.venv\Scripts\python.exe -m py_compile app.py`
  - Passed.
- `.\.venv\Scripts\python.exe -m unittest`
  - Passed: 32 tests ran, OK.
- `.\.venv\Scripts\python.exe main.py qa-check`
  - Passed: Data layer, strategy layer, and output layer all PASSED.
- `.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-11`
  - Passed and regenerated `output/compare_signal.txt` / `output/compare_signal.csv`.
- Started `.\.venv\Scripts\python.exe -m streamlit run app.py` on `http://localhost:8501/`.
- Streamlit health endpoint returned `ok`.
- `streamlit.testing.v1.AppTest` rendered `app.py` with 0 exceptions.
- Playwright 1920x1080 screenshot saved at `output/playwright/ui-readability-1920x1080-v2.png`.
- Playwright 900x1080 screenshot saved at `output/playwright/ui-readability-900x1080.png`.
- Browser console check reported 0 errors and 0 warnings.
- Visual checks confirmed:
  - The main title is fully visible and no longer clipped.
  - Signal overview shows all 8 fields without truncating `下一交易日，待数据确认`, `手动选择`, or `今日数据待更新`.
  - The overview grid is 4 columns on desktop and 2 columns at narrower width.
  - The current-position module is compact and uses Chinese visible column labels.
  - No `NotFoundError` or `removeChild` appeared in the browser console.
