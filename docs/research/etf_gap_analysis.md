# A-ETF-OPEN 项目差距分析报告

审计日期：2026-05-13

审计约束：本报告只基于当前项目文件、当前输出快照和公开项目能力边界做架构审计，不复制任何开源项目代码，不建议把本项目定位为自动交易机器人。无法从项目文件确认的内容均标注为“无法判断”。

## 1. 当前项目现状摘要

### 项目定位

当前项目最适合定位为：ETF/基金量化筛选与投研辅助系统。

项目自身反复声明“只用于研究、复盘、人工观察，不自动下单、不连接券商”。`AI_HANDOFF.md` 明确写到本项目是 A 股 ETF 低频轮动信号系统，且不能视为自动交易系统；`app.py`、`main.py`、`README.md` 也多处强调只生成观察信号和人工执行参考。

### 当前核心功能

- ETF 池与全市场 ETF universe：`config/etf_universe.yaml`、`data/universe.py`、`data/universe/etf_universe.csv`。
- ETF 日线行情下载、缓存和覆盖报告：`data/downloader.py`、`data/storage.py`、`output/data_coverage_report.csv`。
- 数据质量检查和数据闸门：`data/quality.py`、`output/data_quality_report.csv`、`main.py qa-check`。
- 低频策略：`strategy/etf_rotation.py`、`strategy/equal_weight.py`、`strategy/reduced_equal_weight.py`。
- 月度动量轮动候选策略：`config/strategy_momentum_rotation_monthly.yaml`、`MomentumRotationMonthlyStrategy`。
- 回测、基准、样本外、walk-forward、参数实验：`backtest/`、`benchmark/`、`analysis/`、`main.py`。
- 人工交易计划与风险提示：`signal/weekly_signal.py`、`signal/trade_policy.py`。
- Streamlit 本地工作台：`app.py`、`ui/`。

### 数据层能力

已具备场内 ETF 日线行情、全市场 ETF 列表、缓存、覆盖报告、基础质量校验、交易日历辅助能力。当前主要数据源是 AKShare，见 `data/downloader.py` 中 `fund_etf_hist_sina`、`fund_etf_hist_em`，以及 `data/universe.py` 中 `fund_etf_spot_em`。

当前输出快照显示：`output/qa_report.json` 中数据层未通过，原因是 `data quality failed for 244 ETF(s)` 和 `ETF end-date coverage gap is 12 days`。这说明代码层已有数据闸门，但全市场扩展后的数据可信性仍是第一短板。

场外基金净值、指数成分、指数点位、ETF 规模、费率、折溢价、分红/复权核验等能力，在当前项目文件中无法确认。

### 策略层能力

已具备固定篮子等权、全池等权、传统动量趋势轮动、月度动量轮动候选。`MomentumRotationMonthlyStrategy` 已计算 20/60/120 日动量、20 日波动、60 日最大回撤、成交额、数据完整性、趋势过滤，并生成排名、目标 ETF、买入/卖出/继续持有原因。

但策略插件化仍偏手工：策略类型在 `BacktestEngine` 中通过条件分支绑定类，不是成熟量化框架常见的 registry/plugin/job 配置体系。因子体系也还偏内置公式，不是通用因子库。

### 回测层能力

已具备低频组合回测、下一交易日执行、开盘价/收盘价执行配置、手续费、滑点、最低手续费、100 份取整、现金约束、权益曲线、交易记录、最大回撤、夏普、卡玛、换手率等能力。

不足是回测真实性仍是简化模型：未看到停牌、涨跌停、盘口成交量约束、冲击成本、跨境 ETF 时区/节假日、申赎机制、ETF 折溢价、分红处理、场外基金 T+N 申赎等制度级处理。

### 交易安全逻辑

已具备明确安全边界：不自动下单、不连接券商；信号日和执行日分离；人工限价单说明；当前持仓只影响交易计划，不影响选 ETF；现金不足和小于一手时跳过；缺少执行价时跳过交易；数据质量差时降额或禁止买入。

这更像“交易建议安全护栏”，不是“实盘交易风控系统”。成熟实盘风控还需要账户、订单、撤单、成交回报、资金校验、异常断路器、权限和审计日志，当前项目没有这些边界内证据。

### UI/报告能力

已具备 Streamlit 本地工作台、策略选择、ETF 池展示、数据质量页、当前持仓编辑、信号总览、人工执行计划、运行日志、高级诊断。CLI 输出也包含 `compare_signal.txt/csv`、`qa_report`、`performance.json`、曲线图片和报告 CSV。

不足是报告还不是稳定的研究报告产品：字段 schema 存在变动痕迹，`output/compare_signal.csv` 当前更像排名明细；缺少统一的 PDF/HTML 研究报告、可复现实验摘要、数据版本说明、图表模板和策略解释卡。

### 测试覆盖情况

当前 `tests/` 包含再平衡日期、动量轮动、交易逻辑安全测试，覆盖信号日/执行日、现金不影响选 ETF、缺字段不替代、100 份一手、持仓计划、卖出原因等关键逻辑。覆盖方向正确，但测试规模仍偏核心路径，尚未系统覆盖全市场数据、报告 schema、异常数据回放、回测真实性、UI 端到端稳定性。

### 目前最像什么系统

排序判断：

1. 投研辅助系统：最像。已有数据质量、策略对照、解释、UI、人工观察边界。
2. ETF 量化筛选系统：很像。全市场 ETF universe、流动性/完整性过滤、多指标排名已经出现。
3. ETF 轮动系统：部分像。已有月度动量轮动候选，但还处于 research_observation_candidate。
4. 回测系统：具备基础能力，但不是通用回测平台。
5. 自动交易系统：不是。没有券商 API、订单管理、成交回报或自动下单。

## 2. 当前项目结构审计

| 路径 | 作用 | 审计备注 |
| --- | --- | --- |
| `AI_HANDOFF.md` | 当前交接状态、能力边界、验证记录 | 明确“研究、复盘、人工观察，不自动交易”；也记录动量轮动候选与交易计划已完成。 |
| `TRADING_LOGIC_REVIEW.md` | 交易逻辑安全审查 | 重点覆盖字段真实性、未来函数、信号/执行日分离、现金不影响选 ETF。 |
| `app.py` | Streamlit 本地工作台 | 包含操作侧边栏、信号总览、ETF池、数据质量、当前持仓、策略对照、风险提示。 |
| `main.py` | CLI 调度入口 | 包含 update-data、qa-check、backtest、benchmark、experiment、walk-forward、compare-signal 等命令。 |
| `backtest/engine.py` | 回测执行引擎 | 组合、调仓、执行价、手续费滑点、一手取整、权益曲线和交易记录。 |
| `backtest/metrics.py` | 绩效指标 | 总收益、年化、最大回撤、夏普、卡玛、胜率、换手率。 |
| `backtest/portfolio.py` | 组合与交易费用 | FeeConfig、Position、Portfolio、交易成本、可买金额。 |
| `data/downloader.py` | ETF 行情下载与覆盖报告 | 使用 AKShare 场内 ETF 日线源；支持重试、fallback、缓存复用、覆盖报告。 |
| `data/storage.py` | 本地 CSV 标准化与读取 | 标准字段为 date/open/high/low/close/volume/amount/symbol/name/source。 |
| `data/quality.py` | 数据质量检查与闸门 | OHLC 合法性、缺失、重复、未来日期、异常涨跌幅、最新日期滞后。 |
| `data/universe.py` | 全市场 ETF universe | 通过 spot 数据生成 ETF 基础列表和粗分类。 |
| `signal/weekly_signal.py` | 信号文本与人工交易计划 | 生成买入、卖出、继续持有、不操作原因、现金和一手约束。 |
| `signal/trade_policy.py` | 交易计划解释与质量降额 | 数据质量动作、盘中参考价、分档买入、失效条件。 |
| `strategy/base.py` | 策略基础接口 | 统一目标持仓、买卖持有列表和解释。 |
| `strategy/etf_rotation.py` | 动量/趋势/轮动策略 | 包含传统轮动与月度动量轮动候选。 |
| `strategy/equal_weight.py` | 全池等权月度策略 | 每月对可交易 ETF 做等权配置。 |
| `strategy/reduced_equal_weight.py` | 固定篮子等权基准 | 默认 5 只 ETF 固定篮子，适合作为基准。 |
| `strategy/review.py` | 策略状态评审 | 标注 recommended、research_only、defensive、rejected 等状态。 |
| `ui/components.py` | UI 字段本地化与组件 | 中文列名和状态展示。 |
| `ui/signal_parser.py` | 输出解析与 dashboard 数据 | 解析 compare/qa/coverage 等输出到前端数据结构。 |
| `config/etf_universe.yaml` | ETF universe 与过滤配置 | 包含 core_11、full_universe、流动性/完整性过滤参数。 |
| `config/strategy_*.yaml` | 策略配置 | 包含频率、动量、均线、执行价、lot_size、手续费滑点等。 |
| `config/fee.yaml` | 默认交易费用 | 佣金、最低佣金、印花税、滑点。 |
| `tests/` | 回归测试 | 覆盖再平衡日期、月度动量、交易安全逻辑。 |
| `requirements.txt` | 依赖 | pandas/numpy/matplotlib/pyyaml/akshare/streamlit/tqdm，依赖栈轻量。 |

## 3. 与开源量化项目的能力对比

外部项目仅作能力边界参照，不复制代码。

| 维度 | 当前项目 | Qlib | vn.py | Sequoia-X | TradePy | qstock | KHunter/OSkhQuant |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 数据源接入 | AKShare ETF 日线和 ETF spot universe | 面向 AI 量化投研的数据、模型、工作流平台 | 多市场/多接口交易网关生态 | 本轮未确认可靠公开项目边界，无法判断 | 偏交易/策略执行工具，详细边界需以其文档为准 | 偏 A 股/基金等数据获取和轻量分析 | 偏本地化量化平台/数据与可视化，具体边界需以项目文档为准 |
| 数据质量校验 | 已有 OHLC、缺失、滞后、覆盖报告 | 更偏统一数据集和实验流程 | 交易数据管理和行情接入成熟 | 无法判断 | 无法判断 | 以数据获取为主，质量闸门需另建 | 无法判断 |
| 标的池管理 | YAML + spot universe + 过滤 | 可组织数据集/股票池 | 交易品种依赖网关/合约管理 | 无法判断 | 无法判断 | 可取市场列表，工程化池管理较轻 | 无法判断 |
| 策略插件化 | 类分支绑定，部分统一接口 | 完整研究工作流和模型配置 | CTA/Portfolio 等策略 App 插件化成熟 | 无法判断 | 无法判断 | 脚本式/函数式更常见 | 无法判断 |
| 因子计算 | 动量、均线、波动、回撤、成交额 | 强项，面向 Alpha/模型/因子研究 | 不是主要因子平台 | 无法判断 | 无法判断 | 有指标/选股工具 | 无法判断 |
| 回测真实性 | 低频简化，含费用滑点一手 | 研究回测能力较强 | CTA/组合等交易回测成熟 | 无法判断 | 可能偏交易策略回测 | 轻量回测 | 通常偏可视化回测 |
| 交易制度处理 | T+1 近似为下一交易日执行；100 份一手 | 需按市场扩展 | 更接近实盘交易制度和订单事件 | 无法判断 | 无法判断 | 较轻 | 无法判断 |
| 风险控制 | 数据闸门、现金、一手、手动边界 | 研究评估维度强 | 实盘风控和交易事件链更成熟 | 无法判断 | 无法判断 | 较轻 | 无法判断 |
| 报告生成 | CSV/TXT/JSON/PNG，报告产品化不足 | 实验记录和分析流程强 | 交易日志/回测报表 | 无法判断 | 无法判断 | 图表分析较便利 | 可视化能力较突出 |
| 可视化 | Streamlit 工作台 | 研究流程工具为主 | GUI/监控界面成熟 | 无法判断 | 无法判断 | 数据图表便利 | 本地可视化是边界之一 |
| 自动化更新 | CLI + 脚本 + 部署示例 | 工作流可自动化 | 实盘事件和行情自动化强 | 无法判断 | 无法判断 | 需自建 | 无法判断 |
| 实盘交易 | 明确不做 | 不是券商交易主框架 | 强项，交易网关和实盘事件驱动 | 无法判断 | 可能支持交易接口 | 不是主边界 | 无法判断 |

结论：当前项目不要向 vn.py 式实盘框架靠拢，更适合借鉴 Qlib 的“可复现实验/数据集/工作流”、qstock 的“轻量数据获取便利性”和 KHunter 类工具的“本地可视化体验”。实盘自动交易方向应保持克制。

## 4. 当前项目差距矩阵

| 能力 | 当前状态 | 项目内证据 | 差距说明 | 优先级 |
| --- | --- | --- | --- | --- |
| ETF 基础信息 | 部分具备 | `config/etf_universe.yaml`, `data/universe.py`, `data/universe/etf_universe.csv` | 有代码、名称、交易所、资产类别、粗分类、跟踪指数推断；缺基金公司、规模、费率、成立日、指数代码等强字段。 | P1 |
| ETF 池配置 | 已具备 | `config/etf_universe.yaml`, `config/etf_pool.yaml`, `load_etf_pool` | 支持 core_11/full_universe 和过滤参数；仍缺版本化池快照和池变更审计。 | P1 |
| 场内 ETF 行情 | 已具备 | `data/downloader.py`, `data/cache/*.csv` | 已有 OHLCV/amount/source；当前全市场快照数据层未通过，需要提高稳定性。 | P0 |
| 场外基金净值 | 缺失 | 未见场外基金 NAV 下载、申赎规则模块 | 无法支持场外基金筛选、定投、赎回到账等研究。 | P2 |
| 指数数据 | 部分具备 | `tracking_index` 字段、名称推断 | 未见指数行情、指数成分、指数估值、基准指数数据源接入。 | P1 |
| 复权/分红处理 | 部分具备 | `fund_etf_hist_em.qfq` fallback | 下载源尝试 qfq，但缺复权口径记录、分红校验和异常收益归因。 | P0 |
| 交易日历 | 部分具备 | `main.py` `_a_share_trade_calendar`, `_next_a_share_trade_date` | 使用 AKShare 交易日历并有 fallback；缺本地固化日历、跨境 ETF 日历和测试夹具。 | P0 |
| 数据完整性校验 | 已具备 | `data/quality.py`, `output/data_quality_report.csv` | 质量规则存在；当前输出显示 244 只失败和端点 gap，说明规则落地后仍需修数据链。 | P0 |
| 停牌/涨跌停/流动性处理 | 部分具备 | `avg_amount_20`, `zero_amount_days_20`, `filter_passed` | 有成交额和零成交过滤；未见停牌日、涨跌停不可成交、成交量容量约束。 | P2 |
| 手续费/滑点 | 已具备 | `backtest/portfolio.py`, `config/fee.yaml` | 有固定比例模型；缺分层费率、冲击成本、盘口价差模型。 | P2 |
| T+1 | 部分具备 | `execute_date` 为下一交易日，`BacktestEngine` pending signal | 对 ETF 买卖下一交易日执行有近似；未显式建模 A 股/ETF 持仓 T+1 可卖约束。 | P2 |
| 100 股一手 | 已具备 | `lot_size: 100`, `_calculate_buy_order`, `_round_buy_shares` | 已对买入取整和现金不足处理；卖出是否强制一手/零股规则仍需细化。 | P2 |
| 最大回撤 | 已具备 | `backtest/metrics.py`, `strategy/etf_rotation.py` | 组合绩效和单 ETF 60 日回撤都有；需统一报告展示。 | P2 |
| 夏普比率 | 已具备 | `backtest/metrics.py` | 基于日收益年化；需补无风险利率参数和口径说明。 | P2 |
| 波动率 | 部分具备 | `volatility_20` | 策略评分中有 20 日波动；报告和风控未形成统一指标。 | P1 |
| 卡玛比率 | 已具备 | `backtest/metrics.py` | 已计算；需进入统一报告。 | P2 |
| 跟踪误差 | 缺失 | 未见 tracking error 计算 | 缺指数净值/基准收益序列，无法评估 ETF 跟踪质量。 | P1 |
| 折溢价 | 缺失 | 未见 IOPV/NAV/二级市场价格对比 | ETF 筛选重要项缺失，尤其跨境和商品 ETF。 | P1 |
| 规模/成交额/费率 | 部分具备 | `spot_amount`, `avg_amount_20` | 有成交额；规模和费率缺失。 | P1 |
| 多因子评分 | 部分具备 | `score = momentum - volatility - drawdown` | 已有硬编码评分；缺因子注册、标准化、权重实验、解释和稳定性评估。 | P1 |
| ETF 轮动策略 | 部分具备 | `MomentumRotationMonthlyStrategy` | 月度动量轮动候选可用，但状态仍是 research_observation_candidate。 | P1 |
| 定投回测 | 缺失 | 未见 DCA 策略或现金流注入模块 | 不支持按周期申购/扣款/份额累积评估。 | P2 |
| 网格回测 | 缺失 | 未见 grid strategy | 不支持网格价位、成交条件和资金分层。 | P3 |
| 组合构建 | 部分具备 | 等权、目标权重、max_positions | 支持等权和 Top N；缺风险预算、行业约束、多资产约束、优化器。 | P2 |
| 仓位管理 | 部分具备 | `current_position.yaml`, buy/sell/hold plan | 支持当前持仓输入和人工计划；缺组合层风险限额和历史持仓账本。 | P2 |
| 交易安全检查 | 已具备 | `TRADING_LOGIC_REVIEW.md`, `tests/test_trading_logic_safety.py` | 对当前非自动交易边界足够；不等价于实盘风控。 | P0 |
| 结果解释 | 部分具备 | `selection_reason`, `buy_reasons`, `sell_reasons`, UI 文案 | 有基础解释；缺面向投研的因子贡献、失败原因统计、同类对比。 | P1 |
| 报告输出 | 部分具备 | `output/*.csv/json/txt/png`, `analysis/reports.py` | 输出丰富但分散；缺单一、可审计、可复现的策略报告。 | P2 |
| UI 展示 | 部分具备 | `app.py`, `ui/` | 工作台较完整；需提升 schema 稳定、空状态、数据版本、报告导出。 | P2 |
| 测试覆盖 | 部分具备 | `tests/` | 覆盖关键交易安全路径；缺数据源 mock、全市场快照、报告 schema、UI 回归。 | P0 |
| 文档完整性 | 部分具备 | `README.md`, `docs/core_status.md`, `AI_HANDOFF.md` | 文档多但状态有漂移，且部分中文在终端显示存在编码问题；需统一“当前状态”源。 | P1 |
| 自动交易风险 | 已具备边界声明，缺实盘风控 | `README.md`, `app.py`, `main.py` 均声明不自动下单 | 当前不应建设自动交易；若未来做，也必须另立项目级风控与权限体系。 | P3 |

## 5. 后续路线图

### P0：数据可信性

目标：先确保数据可靠、可验证、可复现。

- 固化数据 schema：行情、universe、覆盖报告、质量报告、信号报告都需要 schema 文档和测试。
- 建立数据版本快照：记录数据源、下载时间、复权口径、失败原因、覆盖范围。
- 修复当前全市场数据闸门失败：优先处理 244 只失败、端点日期 gap、失败分类和重试策略。
- 建立可复现数据集：避免每次 AKShare 字段变化导致结果不可解释。
- 增加复权/异常涨跌幅复核报告。

### P1：ETF/基金筛选能力

目标：形成稳定的筛选、评分和解释体系。

- 扩展 ETF 基础信息：规模、费率、成立日、基金公司、跟踪指数代码。
- 接入指数行情和基准序列，用于跟踪误差、相对收益、指数分类。
- 建立多因子评分配置：动量、波动、回撤、成交额、规模、费率、折溢价、跟踪误差。
- 输出筛选解释：为什么入选、为什么剔除、同类 ETF 如何比较。
- 将 `momentum_rotation_monthly` 从候选策略继续观察，不急于推荐。

### P2：回测与报告能力

目标：让系统可以输出可信的策略评估报告。

- 增强回测真实性：停牌、涨跌停、流动性容量、成交额约束、分红/复权口径。
- 增加定投回测和组合评估，但不引入复杂实盘执行。
- 建立统一策略报告：数据版本、参数、标的池、交易假设、指标、图表、风险解释、不可用数据。
- 增加报告 schema 回归测试。
- 增加样本外和 walk-forward 结果解释，而不只输出表格。

### P3：自动化与实盘辅助

目标：只做提醒、报告、模拟组合，不急着自动下单。

- 自动定时刷新数据、生成报告、提醒人工查看。
- 模拟组合账本和人工成交录入。
- 只做观察、提醒、复盘，不接券商 API、不自动下单。
- 若未来评估实盘连接，应另起安全设计评审，不在当前项目直接加。

## 6. 可执行任务卡

### ETF-GAP-001：数据 schema 合同

- 编号：ETF-GAP-001
- 任务名称：建立行情与输出 schema 合同
- 阶段：P0
- 背景：当前输出文件较多，`compare_signal.csv` 存在用途漂移风险。
- 目标：定义行情缓存、coverage、quality、rankings、compare_signal、qa_report 的字段、类型、含义和兼容策略。
- 涉及文件：`data/storage.py`, `data/downloader.py`, `ui/signal_parser.py`, `main.py`
- 建议新增文件：`docs/research/data_schema.md`, `tests/test_output_schema.py`
- 验收标准：核心输出字段有文档；schema 变更会被测试捕获；UI 解析字段有兼容说明。
- 测试建议：构造最小 CSV/JSON 快照，测试解析成功与缺字段报错。
- 不做什么：不重构下载器，不改策略逻辑。
- 风险点：历史输出文件可能字段不一致，需要兼容层。

### ETF-GAP-002：数据质量失败分层

- 编号：ETF-GAP-002
- 任务名称：拆解全市场数据质量失败原因
- 阶段：P0
- 背景：当前 `qa_report.json` 显示 244 只 ETF 数据质量失败。
- 目标：将失败分为下载失败、字段失败、行数不足、端点滞后、质量异常、低流动性过滤。
- 涉及文件：`data/quality.py`, `data/downloader.py`, `output/data_quality_report.csv`
- 建议新增文件：`output/data_failure_summary.csv`, `tests/test_data_failure_summary.py`
- 验收标准：QA 报告能输出失败分类计数和 Top 示例；不再只有一条聚合原因。
- 测试建议：用小样本覆盖每类失败原因。
- 不做什么：不降低质量闸门以换取通过。
- 风险点：过滤失败和质量失败的定义容易混淆。

### ETF-GAP-003：复权口径审计

- 编号：ETF-GAP-003
- 任务名称：记录并校验复权口径
- 阶段：P0
- 背景：下载器尝试 qfq 和 none，但报告中没有稳定口径解释。
- 目标：为每只 ETF 记录使用的 source 和 adjust 口径，并标记异常涨跌幅是否可能由分红/拆分导致。
- 涉及文件：`data/downloader.py`, `data/storage.py`, `data/quality.py`
- 建议新增文件：`docs/research/adjustment_policy.md`, `output/adjustment_audit.csv`
- 验收标准：每只 ETF 的复权口径可追踪；异常日有待复核原因字段。
- 测试建议：构造异常跳变样本，确认报告给出 warning 而不是静默通过。
- 不做什么：不手工改价格数据。
- 风险点：AKShare 不同接口字段和复权定义可能变化。

### ETF-GAP-004：本地交易日历快照

- 编号：ETF-GAP-004
- 任务名称：固化 A 股交易日历
- 阶段：P0
- 背景：当前交易日历依赖 AKShare，失败时 fallback 到工作日。
- 目标：生成并版本化本地交易日历快照，QA 中检查最新日历覆盖。
- 涉及文件：`main.py`, `tests/test_rebalance_dates.py`
- 建议新增文件：`data/calendar/a_share_trading_calendar.csv`, `tests/test_trade_calendar.py`
- 验收标准：无网络时仍能可靠计算下一交易日；节假日测试通过。
- 测试建议：春节、国庆、周末、临时非交易日样例。
- 不做什么：不处理全球所有市场日历。
- 风险点：跨境 ETF 使用 A 股日历交易但底层资产休市，需要后续单独处理。

### ETF-GAP-005：ETF 基础信息扩展

- 编号：ETF-GAP-005
- 任务名称：扩展 ETF 主数据
- 阶段：P1
- 背景：当前 ETF 基础信息以名称推断为主。
- 目标：增加规模、费率、成立日、基金公司、指数代码、资产类别的可信字段。
- 涉及文件：`data/universe.py`, `config/etf_universe.yaml`
- 建议新增文件：`data/fund_metadata.py`, `output/etf_metadata.csv`, `tests/test_etf_metadata.py`
- 验收标准：核心 ETF 有完整 metadata；缺失字段标注“无法判断”。
- 测试建议：用 mock 数据测试字段标准化和缺失处理。
- 不做什么：不把名称推断当作最终真实分类。
- 风险点：多源字段冲突和基金名称变更。

### ETF-GAP-006：指数行情与基准接入

- 编号：ETF-GAP-006
- 任务名称：接入指数行情用于相对评估
- 阶段：P1
- 背景：当前跟踪指数多为名称推断，缺指数收益序列。
- 目标：为主要 ETF 建立指数代码映射、指数行情缓存和基准收益。
- 涉及文件：`data/universe.py`, `benchmark/report.py`, `backtest/metrics.py`
- 建议新增文件：`data/index_downloader.py`, `config/index_map.yaml`, `tests/test_index_data.py`
- 验收标准：至少 core_11 能输出 ETF 相对指数收益和基准曲线。
- 测试建议：mock 指数行情缺失、不同起始日、非交易日对齐。
- 不做什么：不做指数成分级股票研究。
- 风险点：指数代码映射质量决定后续跟踪误差可信度。

### ETF-GAP-007：跟踪误差与折溢价指标

- 编号：ETF-GAP-007
- 任务名称：补充 ETF 专属筛选指标
- 阶段：P1
- 背景：ETF 筛选不能只看价格动量和成交额。
- 目标：增加跟踪误差、折溢价、规模、费率等 ETF 专属指标。
- 涉及文件：`strategy/etf_rotation.py`, `data/universe.py`, `ui/signal_parser.py`
- 建议新增文件：`data/etf_metrics.py`, `tests/test_etf_metrics.py`
- 验收标准：缺数据时明确“无法判断”；有数据时进入排名解释。
- 测试建议：构造 ETF 和指数收益，验证 tracking error 计算。
- 不做什么：不因缺指标直接剔除所有 ETF。
- 风险点：NAV/IOPV 数据源稳定性可能弱。

### ETF-GAP-008：多因子评分配置化

- 编号：ETF-GAP-008
- 任务名称：将评分公式配置化
- 阶段：P1
- 背景：当前评分在 `MomentumRotationMonthlyStrategy` 内硬编码。
- 目标：因子权重、方向、缺失值处理、标准化方式可配置，并输出因子贡献。
- 涉及文件：`strategy/etf_rotation.py`, `config/strategy_momentum_rotation_monthly.yaml`
- 建议新增文件：`strategy/factors.py`, `config/factor_score.yaml`, `tests/test_factor_score.py`
- 验收标准：同一输入下评分可复现；每个入选 ETF 有因子贡献解释。
- 测试建议：固定小样本验证排名、缺失值、权重调整。
- 不做什么：不做大规模机器学习选基。
- 风险点：过度参数化会诱发过拟合。

### ETF-GAP-009：统一研究报告

- 编号：ETF-GAP-009
- 任务名称：生成单一策略评估报告
- 阶段：P2
- 背景：当前输出分散在 CSV/TXT/JSON/PNG。
- 目标：生成一份 HTML 或 Markdown 报告，包含数据版本、策略参数、池、指标、图表、交易假设和风险说明。
- 涉及文件：`analysis/reports.py`, `backtest/engine.py`, `main.py`
- 建议新增文件：`analysis/research_report.py`, `docs/research/report_template.md`, `tests/test_research_report.py`
- 验收标准：一次命令生成完整报告；报告中所有指标能追溯到输出文件。
- 测试建议：snapshot 测试标题、关键字段和缺数据提示。
- 不做什么：不做营销式页面。
- 风险点：报告字段依赖多个输出，schema 漂移会破坏报告。

### ETF-GAP-010：回测成交真实性增强

- 编号：ETF-GAP-010
- 任务名称：加入停牌/涨跌停/成交额容量约束
- 阶段：P2
- 背景：当前回测主要按下一交易日价格成交。
- 目标：当成交额过低、零成交、缺价格或不可成交时，明确跳过或部分成交。
- 涉及文件：`backtest/engine.py`, `data/quality.py`
- 建议新增文件：`backtest/execution.py`, `tests/test_execution_constraints.py`
- 验收标准：交易记录说明跳过/部分成交原因；绩效不再假设无限流动性。
- 测试建议：构造零成交、缺开盘价、低成交额样本。
- 不做什么：不模拟逐笔盘口。
- 风险点：过度复杂会超出低频投研系统边界。

### ETF-GAP-011：定投回测

- 编号：ETF-GAP-011
- 任务名称：增加定投回测模式
- 阶段：P2
- 背景：用户可能需要场内 ETF 或场外基金的定投评估。
- 目标：支持按月/周投入固定现金、买入目标 ETF 或组合，并输出定投收益、回撤和现金流。
- 涉及文件：`backtest/engine.py`, `backtest/portfolio.py`
- 建议新增文件：`backtest/dca.py`, `config/strategy_dca_example.yaml`, `tests/test_dca_backtest.py`
- 验收标准：现金流进入权益曲线；收益率口径区分资金加权和时间加权。
- 测试建议：固定价格、上涨、下跌三类样本。
- 不做什么：不先做场外基金申赎到账复杂规则。
- 风险点：收益率口径容易误导。

### ETF-GAP-012：模拟组合账本

- 编号：ETF-GAP-012
- 任务名称：建立人工成交录入和模拟组合账本
- 阶段：P3
- 背景：当前 `current_position.yaml` 只保存当前持仓，不是历史账本。
- 目标：记录人工买卖、成交价、费用、备注，并生成模拟组合净值。
- 涉及文件：`app.py`, `signal/weekly_signal.py`, `config/current_position.example.yaml`
- 建议新增文件：`data/portfolio_ledger.py`, `config/portfolio_ledger.example.csv`, `tests/test_portfolio_ledger.py`
- 验收标准：用户手工录入成交后可复盘仓位变化；不连接券商。
- 测试建议：买入、卖出、费用、剩余现金和持仓一致性测试。
- 不做什么：不自动下单，不读取券商账户。
- 风险点：手工录入错误需要校验和撤销机制。

### ETF-GAP-013：报告与提醒自动化

- 编号：ETF-GAP-013
- 任务名称：自动刷新并生成观察报告
- 阶段：P3
- 背景：项目已有脚本和部署示例，但报告链路还需稳定。
- 目标：定时运行数据更新、QA、信号、研究报告，并只发出提醒。
- 涉及文件：`scripts/update_signal.sh`, `deploy/`, `main.py`
- 建议新增文件：`scripts/generate_daily_report.sh`, `docs/research/automation_runbook.md`
- 验收标准：自动任务失败可见；报告含 QA 状态；不触发交易。
- 测试建议：模拟命令失败和数据源失败。
- 不做什么：不做订单推送。
- 风险点：用户可能误把提醒当作交易指令，文案必须明确边界。

### ETF-GAP-014：UI 回归测试

- 编号：ETF-GAP-014
- 任务名称：补充 Streamlit 输出解析和关键页面测试
- 阶段：P2
- 背景：UI 依赖多种输出文件，字段漂移会造成展示异常。
- 目标：覆盖总览、ETF池、数据质量、当前持仓、策略信号解析。
- 涉及文件：`app.py`, `ui/signal_parser.py`, `ui/components.py`
- 建议新增文件：`tests/test_signal_parser_schema.py`, `tests/fixtures/output_snapshots/`
- 验收标准：使用 fixture 时 UI 数据解析稳定；缺字段有明确错误。
- 测试建议：最小输出、完整输出、异常输出三类 fixture。
- 不做什么：不重写前端框架。
- 风险点：Streamlit 端到端测试成本较高，可先测解析层。

## 7. 最终结论

### 当前项目最大的优势是什么？

最大优势是“边界清醒 + 低频 ETF 投研闭环已经成形”。它不是盲目追求自动交易，而是已经有 ETF 池、行情缓存、数据闸门、策略对照、回测、人工交易计划、UI 展示和风险提示，适合作为个人或小团队的 ETF 量化筛选与投研辅助内核。

### 当前项目最短板是什么？

最短板是数据可信性和 ETF 专属指标不足。当前全市场数据快照已经触发 QA 失败，且尚缺规模、费率、折溢价、跟踪误差、指数行情、复权/分红审计这些成熟 ETF 研究必需项。策略和 UI 已经跑起来，但数据底座还没到“可信报告”级别。

### 下一步最值得做的 3 件事是什么？

1. 先修 P0 数据可信性：schema、失败分层、复权口径、交易日历、本地快照。
2. 再补 P1 ETF 筛选核心指标：规模、费率、指数、跟踪误差、折溢价、多因子解释。
3. 然后做 P2 统一研究报告：把数据版本、策略参数、回测假设、指标和风险解释合成一份可信报告。

### 哪些功能现在不该做？

- 不该接券商 API。
- 不该做自动下单。
- 不该做盘中高频执行。
- 不该为了提高回测收益继续追参数。
- 不该在数据闸门失败时把策略升级为推荐。
- 不该先做复杂网格或自动交易风控；当前阶段应继续定位为 ETF/基金量化筛选与投研辅助系统。

## 参考边界

- Qlib: <https://qlib.readthedocs.io/en/stable/introduction/introduction.html>
- vn.py: <https://www.vnpy.com/docs/cn/index.html>
- qstock: <https://github.com/tkfy920/qstock>
- TradePy: <https://docs.trade-py.com/trading.html>
- KHunter/OSkhQuant: <https://github.com/khscience/OSkhQuant>
- Sequoia-X: 本轮公开检索未确认可靠项目主页，能力边界标注为无法判断；如后续有指定链接，应单独补充审计。
