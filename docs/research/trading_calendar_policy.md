# A 股交易日历快照与 QA 使用说明

## 为什么必须使用交易日历

A 股 ETF 的行情覆盖、信号日、执行日和调仓日都依赖真实交易日。普通工作日只排除了周末，但不能识别春节、国庆、临时休市、节前调休、节后补班等情况。把普通工作日当成交易日，会导致：

- QA 误判行情是否 stale。
- ETF end-date coverage gap 被错误放大或缩小。
- 下一交易日执行日落到休市日。
- 手工观察信号和回测日期解释不一致。

因此系统必须优先使用本地交易日历快照，而不是在 AKShare 失败时静默 fallback 到 `bdate_range`。

## 本地快照

生产快照路径：

`data/calendar/a_share_trading_calendar.csv`

字段：

- `date`：日期。
- `is_open`：是否开市。
- `exchange`：交易所或市场标识，当前为 `A_SHARE`。
- `source`：来源，例如 `akshare.tool_trade_date_hist_sina`。
- `calendar_version`：日历 schema 版本。
- `generated_at`：快照生成时间。
- `note`：备注或 warning。

系统读取交易日时优先使用该文件。快照不存在时，可以尝试通过 AKShare 生成并落地；如果 AKShare 不可用，不会静默把普通工作日当作交易日。

## AKShare 失败时如何处理

默认行为是报错并在 QA 中暴露 `trading_calendar` 问题。只有调用方显式允许 `allow_weekday_fallback=True` 时，才会构造普通工作日 fallback；这种情况会在 `output/trading_calendar_audit.csv` 中标记为 `warning_weekday_fallback`，并设置 `used_fallback=True`。

weekday fallback 只用于诊断或临时降级，不代表真实 A 股交易日历，不能作为正式 QA 放行依据。

## coverage gap 如何计算

QA 的 latest expected date 优先来自：

`latest_trading_day_on_or_before(today)`

也就是本地交易日历中今天或今天之前最近的开市日。ETF end-date coverage gap 使用该 expected date 与各 ETF 缓存结束日期比较，不再用简单日历日或普通工作日作为目标日期。

这不会放宽 QA 标准；它只让“应该更新到哪一天”的判断可复现。

## 信号日和执行日

信号日仍然来自策略已有的调仓日期规则和行情数据索引，不改变选股逻辑。

执行日统一通过 `data/trading_calendar.py` 的 `next_trading_day(date)` 计算。若本地日历缺失且 AKShare 无法生成，系统应显式报错或在审计中暴露 fallback 状态，不能静默使用普通工作日。

## 回测再平衡日期

回测内部的再平衡日期仍基于传入的行情日期索引计算。该索引本身来自本地 ETF 行情缓存，因此不会额外引入未来交易日。ETF-GAP-004 不改变回测收益逻辑和策略评分，只统一 QA 与信号执行日所需的自然日到交易日映射。

## trading_calendar_audit.csv 如何读

`output/trading_calendar_audit.csv` 一行描述当前交易日历状态：

- `ok`：本地快照存在且覆盖到当前需要的日期范围。
- `warning_calendar_stale`：快照存在但最新开市日距离 today 过久。
- `warning_using_akshare_runtime`：本地快照缺失，本次运行通过 AKShare 生成。
- `warning_weekday_fallback`：显式启用了普通工作日 fallback。
- `error_missing_calendar`：快照缺失且未能生成。
- `error_invalid_calendar`：快照不可读、字段不完整或内容无效。
- `unknown`：无法归类的状态。

重点关注 `source`、`latest_open_day`、`coverage_gap_days`、`used_fallback` 和 `reason`。

## 需要人工确认的情况

- `warning_weekday_fallback` 出现。
- `warning_calendar_stale` 出现，尤其跨越节假日。
- `warning_using_akshare_runtime` 首次出现，需要确认新快照是否合理。
- `latest_open_day` 与交易所公告不一致。
- QA 的 ETF coverage gap 与实际行情更新时间不一致。

## 后续如何刷新

建议定期刷新交易日历快照：

1. 每季度或每次跨年后刷新一次。
2. 长假前后手工确认交易所节假日安排。
3. 刷新后运行 `python main.py qa-check`。
4. 检查 `output/trading_calendar_audit.csv` 和 `qa_report.json` 中的 `data_layer.trading_calendar`。
5. 只有在 `used_fallback=False` 且快照覆盖范围合理时，才把 QA 结果作为正式观察依据。
