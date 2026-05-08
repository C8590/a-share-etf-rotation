# a-share-etf-rotation

当前版本：`v0.1-core`

这是一个面向中国 A 股 ETF 的低频量化研究与信号系统。`v0.1-core` 是数据层和策略层的一期稳定版，目标是把数据下载、数据质量、策略接口、回测真实性、样本外验证和 QA 闸门整理成可维护、可验证、可复盘的核心内核。

本项目目前不做网页界面、不做 Streamlit、不做 Excel 总报表、不做自动交易、不接券商 API，也不开发盘中执行助手。

## v0.1-core 状态

数据层已完成一期整理：

- 支持从 `config/etf_pool.yaml` 读取 ETF 池。
- 支持逐只 ETF 下载日线行情，并保留本地缓存。
- 支持主数据源失败后的备用数据源兜底。
- 支持 `update-data` 默认复用缓存，`update-data --refresh` 强制刷新，`retry-failed-data` 只重试失败 ETF。
- 本地 CSV 已统一为标准字段：`date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `symbol`, `name`, `source`。
- 已生成并维护 `output/data_coverage_report.csv`。
- 已新增数据质量检查和数据闸门，输出 `output/data_quality_report.csv`。
- 有效 ETF 少于 5 只、数据明显落后或严重质量失败时，不允许输出正式策略结论，只允许 `test_only` 流程测试。

策略层已完成一期整理：

- 已统一策略目标仓位接口，包括目标持仓、目标权重、买入列表、卖出列表、持有列表和决策原因。
- 已整理策略配置文件，策略参数集中在 `config/strategy_*.yaml`。
- 已支持 `original`, `conservative`, `balanced`, `equal_weight_monthly`, `reduced_equal_weight_monthly`。
- 已检查并强化 `signal_date` 和 `execute_date` 分离，避免当天收盘信号当天收盘成交。
- 回测已计入手续费、滑点、最低手续费、100 份整数交易单位和剩余现金。
- 已修复等权月度策略只在目标名单变化时成交的问题，现在会按月执行真实再平衡。
- 已统一输出绩效指标、基准对比、样本外测试、walk-forward 和参数实验。
- 已新增 `strategy/review.py` 生成策略状态，不把状态硬写在 README 里。

## 当前推荐观察策略

当前 `qa-check` 结果允许进入 `1000-3000` 元小额人工观察阶段。推荐观察策略为：

- `equal_weight_monthly`
- `reduced_equal_weight_monthly`

策略状态由 `strategy/review.py` 生成，当前默认标记为：

- `equal_weight_monthly`: `recommended_for_observation`
- `reduced_equal_weight_monthly`: `recommended_for_observation`
- `balanced`: `research_only`
- `conservative`: `defensive_only`
- `original`: `rejected`

`balanced` 全样本表现较好，但样本外稳定性不足，不建议继续追 `balanced`，也不建议把它标为主推荐策略。它只能保留为研究对照。

## 明确不建议

- 不建议自动交易。
- 不建议接券商 API。
- 不建议开发盘中执行层。
- 不建议为了提高回测收益继续追参数。
- 不建议继续追 `balanced` 作为主策略。
- 不建议跳过 `qa-check` 直接看回测收益。

## Windows 推荐使用虚拟环境运行

Windows 下建议在项目目录创建并使用 `.venv`，不要直接使用全局 `pip`。如果全局 `pip` 损坏，项目 `.venv` 内部环境仍可独立安装和运行依赖。

创建虚拟环境：

```powershell
py -3.12 -m venv .venv
# 或
py -3.11 -m venv .venv
```

安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

运行项目：

```powershell
.\.venv\Scripts\python.exe main.py qa-check
.\.venv\Scripts\python.exe main.py update-data
.\.venv\Scripts\python.exe main.py compare-signal
```

## 常用命令

数据层：

```powershell
python main.py update-data
python main.py update-data --refresh
python main.py retry-failed-data
python main.py data-report
python main.py qa-data
```

策略与验证：

```powershell
python main.py backtest --config config/strategy_equal_weight_monthly.yaml
python main.py backtest --config config/strategy_reduced_equal_weight_monthly.yaml
python main.py benchmark
python main.py oos-test
python main.py walk-forward
python main.py experiment
python main.py compare-signal
python main.py qa-check
```

## 关键输出

- `output/data_coverage_report.csv`
- `output/data_quality_report.csv`
- `output/performance.json`
- `output/benchmark_report.csv`
- `output/benchmark_report.json`
- `output/oos_results.csv`
- `output/walk_forward_results.csv`
- `output/experiment_results.csv`
- `output/compare_signal.txt`
- `output/qa_report.txt`
- `output/qa_report.json`
- `output/strategy_review.csv`

## 当前 QA 结论

最近一次 `python main.py qa-check` 结果：

- 数据层：通过
- 策略层：通过
- 输出层：通过
- 是否允许进入 `1000-3000` 元小额观察：是
- 推荐观察策略：`equal_weight_monthly`, `reduced_equal_weight_monthly`
- 不建议使用策略：`original`, `balanced`
- 防守策略：`conservative`

详见 [docs/core_status.md](docs/core_status.md)。

## 风险提示

本项目只用于量化研究、人工观察和复盘，不构成投资建议。回测结果不代表未来收益。数据源可能发生字段变化、限流、网络失败或停更。任何真实交易都应人工确认价格、份额、手续费、现金余额和交易单位。
