# CHANGELOG

## v0.1-core - 2026-05-08

数据层和策略层一期稳定版。

### 数据层

- 整理 ETF 池读取、逐只下载、日志输出、缓存复用和失败重试流程。
- 保留主数据源优先、备用数据源兜底的下载路径，单只 ETF 失败不会导致整体流程崩溃。
- 统一本地 ETF CSV 字段：`date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `symbol`, `name`, `source`。
- 完善 `output/data_coverage_report.csv`，使用可机器读取的覆盖字段。
- 新增数据质量检查，覆盖重复日期、日期升序、空收盘价、非正价格、`high < low`、行数过少、最新日期滞后、ETF 覆盖差异等检查。
- 新增数据闸门：有效 ETF 少于 5 只、数据明显落后或严重质量失败时，只允许 `test_only` 流程测试。
- 新增并验证命令：`qa-data`。

### 策略层

- 新增统一策略接口，规范输出 `signal_date`, `execute_date`, `target_positions`, `target_weights`, `buy_list`, `sell_list`, `hold_list`, `reason`。
- 整理策略配置文件：
  - `config/strategy_original.yaml`
  - `config/strategy_conservative.yaml`
  - `config/strategy_balanced.yaml`
  - `config/strategy_equal_weight_monthly.yaml`
  - `config/strategy_reduced_equal_weight_monthly.yaml`
- 新增 `reduced_equal_weight_monthly` 策略配置和策略实现。
- 修复等权月度策略没有在每月真实再平衡的问题。
- 明确并检查 `signal_date` 与 `execute_date` 分离，避免当天收盘信号当天收盘成交。
- 统一绩效指标字段，补充 `start_date`, `end_date`, `is_complete_backtest`, `warning`, `yearly_turnover` 等字段。
- 完善 benchmark、OOS、walk-forward、experiment 输出。
- 新增 `strategy/review.py`，集中生成策略状态：
  - `equal_weight_monthly`: `recommended_for_observation`
  - `reduced_equal_weight_monthly`: `recommended_for_observation`
  - `balanced`: `research_only`
  - `conservative`: `defensive_only`
  - `original`: `rejected`

### QA

- 新增 `qa-check` 总质量检查命令。
- 生成 `output/qa_report.txt` 和 `output/qa_report.json`。
- 当前 `qa-check` 结论：数据层通过、策略层通过、输出层通过，允许进入 `1000-3000` 元小额人工观察阶段。

### 明确不做

- 不开发网页、App、Streamlit 或 Excel 总报表。
- 不开发自动交易。
- 不接券商 API。
- 不开发盘中执行助手。
- 不为了回测收益继续追参数。
- 不把 `balanced` 标为主推荐策略。
