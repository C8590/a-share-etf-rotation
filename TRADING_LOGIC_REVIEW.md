# 量化交易逻辑安全审查 v1

审查日期：2026-05-12

## 1. 审查范围

- 数据源字段归一化：`data/downloader.py`
- 本地缓存保存与读取：`data/storage.py`
- 数据质量门禁：`data/quality.py`
- 信号日期、执行日期与对照信号：`main.py`, `signal/weekly_signal.py`
- 策略选股与指标：`strategy/etf_rotation.py`, `strategy/equal_weight.py`, `strategy/reduced_equal_weight.py`, `strategy/indicators.py`
- 回测执行与交易费用：`backtest/engine.py`, `backtest/portfolio.py`
- Web 信号展示：`app.py`, `ui/signal_parser.py`
- 回归测试：`tests/test_trading_logic_safety.py`

本次审查聚焦交易逻辑、算法规则、数据真实性、未来函数、字段错用、信号安全；不是普通网络安全审查。

## 2. 已确认安全项

- `normalize_source_frame` 只接受明确字段别名，不猜测未知字段；`close/open/high/low/volume/amount` 缺失或解析为空会直接失败。
- `normalize_for_storage` 保存缓存前要求 `date/open/high/low/close/volume/amount/source` 均有效，不再静默丢弃只有 `close` 缺失的行。
- `data/cache/*.csv` 保留 `source` 字段；缺少真实 `source` 且调用方未显式提供来源时会报错。
- 未发现把 `high`、`low` 或 `open` 当作 `close` 使用的逻辑。
- 动量计算为 `close / close.shift(period) - 1.0`，均线为 `rolling(window=period)`，未发现未来收盘价参与信号日选股。
- `generate_target(signal_date)` 使用策略 `close` 矩阵在 `signal_date` 当日及以前已计算出的指标。
- 回测成交价使用 `execute_date` 的 `open` 或 `close`，未参与 `signal_date` 选股。
- 手动选择非交易日时，只回退到此前最近交易日；选择晚于最新数据日期会报错。
- 若有效信号日之后没有下一交易日，手动信号会报错；自动信号只选择已有下一交易日的最近可执行信号日，避免伪造执行日。
- `observation_cash` 只影响目标金额、买入份额、100 份取整、手续费和预计剩余现金；不参与目标 ETF 选择。
- `reduced_equal_weight_monthly` 是精选 ETF 篮子等权月度策略；`equal_weight_monthly` 是配置池等权月度策略。
- 买入按 100 份整数单位取整，手续费、最低手续费和滑点均计入；现金不足时输出跳过原因，不强行生成不现实份额。
- 卖出份额使用 `min(requested, holding)`，不会超过当前持仓。
- 缺少成交价格时回测记录 `SKIP_BUY` / `SKIP_SELL`，不使用其他价格替代。
- Web 主界面使用中文解释信号日、实际计算信号日、预计执行日、观察资金、组合变化、最新数据日期；目标组合未变化时显示低频策略说明。

## 3. 发现的问题

### 高风险

- 未发现当前代码存在 `high` 冒充 `close`、`low` 冒充 `close` 或 `open` 冒充 `close`。

### 中风险

- 修复前，`normalize_source_frame` 会对 `date/open/close` 做 `dropna`，但 `high/low/volume/amount` 转换失败后可能以 NaN 继续流入。已修复为任何必需字段为空都失败。
- 修复前，`normalize_for_storage` 只按 `date/close` 清洗，且可能在缺少 `source` 时由默认参数补出来源。已修复为 OHLCV、amount、source 全部必须有效。
- 自动信号若最新月度信号日正好是缓存最后一个交易日，可能没有可用下一交易日执行价。已改为只输出最近“已有下一交易日”的正式可执行信号；手动选择最后数据日会报错。

### 低风险

- 当前真实缓存中有 3 只 ETF 出现过 `abs(close / prev_close - 1) > 20%` 的 warning：`512100`, `512480`, `159928`。这不会阻塞正式门禁，但建议人工复核是否为复权、拆分或数据源异常。
- `backtest.engine` 中的 `valuation_close.ffill()` 仅用于持仓估值曲线，不参与信号选股；后续维护时应继续保持这个边界。
- `benchmark.report` 中的 `close.ffill()` 仅用于基准净值序列，不参与交易信号。

## 4. 风险结论

- 是否发现 high 冒充 close：未发现。
- 是否发现未来函数风险：未发现当前选股逻辑使用未来数据；已补强执行日不可伪造规则。
- 是否发现资金影响选股风险：未发现；已新增回归测试覆盖。
- 是否发现数据字段伪造风险：发现并修复了字段空值放行、缓存来源可能被补写的风险。

## 5. 修复建议

- 保留本次新增的数据字段真实性测试，后续新增数据源时必须先通过 `normalize_source_frame` 缺字段测试。
- 对 `daily close return exceeds 20%` 的 ETF 做人工抽样复核，必要时在数据质量报告中增加“复权/异常波动备注”。
- 后续如果新增成交模拟字段，必须明确区分 `signal_date` 可见字段与 `execute_date` 成交字段。
- Web 高级诊断区不再展示原始 `compare_signal.csv` 字段表，避免把代码字段名暴露给普通页面。

## 6. 已新增测试

新增 `tests/test_trading_logic_safety.py`，覆盖：

1. `close` 缺失不能通过 `normalize_source_frame`。
2. `high` 缺失不能通过 `normalize_source_frame`。
3. `high < close` 触发数据质量错误。
4. `low > close` 触发数据质量错误。
5. `close` 全部等于 `high` 触发 warning。
6. 周末 `selected_signal_date` 回退到此前最近交易日。
7. `selected_signal_date` 晚于最新数据日期时报错。
8. `observation_cash` 改变不影响目标 ETF。
9. 小资金不足以买入 100 份时输出明确跳过原因。
10. `execute_date` 必须晚于 `effective_signal_date`；最后数据日无下一交易日时报错。

## 7. 验证命令和结果

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py main.py data\downloader.py data\quality.py data\storage.py signal\weekly_signal.py ui\signal_parser.py strategy\etf_rotation.py strategy\indicators.py backtest\engine.py
```

结果：通过。

```powershell
.\.venv\Scripts\python.exe -m unittest
```

结果：通过，19 tests OK。

```powershell
.\.venv\Scripts\python.exe main.py qa-check
```

结果：通过。Data layer / Strategy layer / Output layer 均 PASSED，Allow 1000-3000 CNY small observation: YES。

```powershell
.\.venv\Scripts\python.exe main.py compare-signal
```

结果：通过，生成 `output/compare_signal.txt` 和 `output/compare_signal.csv`。当最新月度信号日缺少下一交易日执行价时，自动输出最近可执行信号日。

```powershell
.\.venv\Scripts\python.exe main.py compare-signal --signal-date 2026-05-08
```

结果：通过，`effective_signal_date=2026-05-08`，`execute_date=2026-05-11`。
