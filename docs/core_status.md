# v0.1-core Core Status

日期：2026-05-08

`v0.1-core` 是 `a-share-etf-rotation` 的数据层和策略层一期稳定版。本版本只整理核心内核，不包含界面、自动交易、券商 API 或盘中执行层。

## 数据层状态

数据层已完成一期整理。

已完成能力：

- 从 `config/etf_pool.yaml` 读取 ETF 池。
- 支持逐只 ETF 下载日线行情。
- 支持主数据源失败后的备用数据源兜底。
- 支持本地缓存复用，避免无意义重复下载。
- 支持 `update-data --refresh` 强制刷新。
- 支持 `retry-failed-data` 只重试失败 ETF。
- 统一本地 CSV 字段：`date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `symbol`, `name`, `source`。
- 生成 `output/data_coverage_report.csv`。
- 生成 `output/data_quality_report.csv`。
- 数据闸门已启用。

当前覆盖情况：

- ETF 总数：11
- 成功覆盖：11
- 失败：0
- 最新日期：2026-05-07
- 数据质量：通过

当前覆盖 ETF：

| symbol | name | start_date | end_date | rows | status |
| --- | --- | --- | --- | ---: | --- |
| 510300 | 华泰柏瑞沪深300ETF | 2019-01-02 | 2026-05-07 | 1778 | passed |
| 510500 | 南方中证500ETF | 2019-01-02 | 2026-05-06 | 1777 | passed |
| 512100 | 南方中证1000ETF | 2019-01-02 | 2026-05-06 | 1776 | passed |
| 159915 | 易方达创业板ETF | 2019-01-02 | 2026-05-06 | 1776 | passed |
| 588000 | 华夏科创50ETF | 2020-11-16 | 2026-05-06 | 1324 | passed |
| 510880 | 华泰柏瑞红利ETF | 2019-01-02 | 2026-05-06 | 1777 | passed |
| 512880 | 国泰中证全指证券公司ETF | 2019-01-02 | 2026-05-06 | 1777 | passed |
| 512480 | 国联安中证全指半导体ETF | 2019-06-12 | 2026-05-06 | 1671 | passed |
| 159928 | 汇添富中证主要消费ETF | 2019-01-02 | 2026-05-06 | 1777 | passed |
| 518880 | 华安黄金ETF | 2019-01-02 | 2026-05-06 | 1777 | passed |
| 511880 | 银华日利货币ETF | 2019-01-02 | 2026-05-06 | 1777 | passed |

## 策略层状态

策略层已完成一期整理。

已完成能力：

- 统一目标仓位接口。
- 统一策略配置文件。
- 保留并验证多策略：
  - `original`
  - `conservative`
  - `balanced`
  - `equal_weight_monthly`
  - `reduced_equal_weight_monthly`
- `signal_date` 与 `execute_date` 分离。
- 信号使用信号日前已知数据。
- 执行默认使用下一交易日开盘价。
- 手续费、滑点、最低手续费、100 份整数交易单位已计入。
- 现金不能为负，剩余现金保留。
- 等权月度策略已修复为真实月度再平衡。
- benchmark、OOS、walk-forward、experiment 已可运行。

当前策略状态：

| strategy | status | note |
| --- | --- | --- |
| equal_weight_monthly | recommended_for_observation | 推荐进入人工小额观察 |
| reduced_equal_weight_monthly | recommended_for_observation | 推荐进入人工小额观察 |
| balanced | research_only | 全样本表现好，但样本外稳定性不足，不建议继续追 |
| conservative | defensive_only | 仅作为防守型观察和对照 |
| original | rejected | 保留作历史对照，不建议使用 |

## qa-check 结果

最近一次 `python main.py qa-check` 结果：

```text
Data layer: PASSED
Strategy layer: PASSED
Output layer: PASSED
Allow 1000-3000 CNY small observation: YES
Blocking reasons:
- None
Recommended observation strategies:
- equal_weight_monthly, reduced_equal_weight_monthly
Not recommended strategies:
- original, balanced
Defensive-only strategies:
- conservative
```

结论：

- 当前允许进入 `1000-3000` 元小额人工观察阶段。
- 观察对象应以 `equal_weight_monthly` 和 `reduced_equal_weight_monthly` 为主。
- `balanced` 只保留为研究对照，不建议继续追，也不应作为主推荐。
- 当前仍不建议自动交易、接券商 API 或开发盘中执行层。

## 风险提示

本项目只用于研究、人工观察和复盘，不构成投资建议。回测和样本外结果不代表未来收益。任何真实交易都应人工确认数据、价格、份额、手续费、现金余额和交易单位。
