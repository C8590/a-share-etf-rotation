# V2.1 Backend Integration Contract

V2.1 后端集成层只做总控编排、优先级裁决和稳定输出，不重写 7 个项目部内部策略逻辑，不调整 entry 阈值，不接入实盘自动下单。未来前端只读取 `output/` 下的 V2.1 总控输出，不直接读取各项目部临时文件。

## 合并顺序

1. `pre_selection`：决定样本池和候选 ETF。
2. `risk_warning`：生成 RiskGate，决定能不能交易。
3. `entry`：只在 RiskGate 允许时形成买入建议。
4. `exit`：持仓风险退出优先于新增买入。
5. `learning`：输出复盘归因和建议。
6. `historical_ml`：输出历史回放、样本和校准建议。
7. `qmt_execution`：最后消费 OrderIntent，只生成模拟盘/草稿/人工确认/只读同步相关结果。

## 优先级裁决

最高优先级从高到低为：

1. RiskGate / P0 / R4 / R3 风险
2. 持仓真实风险
3. 市场状态
4. pre_selection 候选池
5. entry / exit
6. QMT 执行
7. learning / historical_ml 建议

总控规则：

- 风险门控冻结买入时，entry 不得生成实际买入。
- R3/R4/P0 风险必须触发 `freeze_entry=True` 或 `manual_takeover_required=True`。
- 风险门控要求人工接管时，OrderIntent 必须标记 `requires_manual_confirm=True`。
- exit 清仓、止损或风险退出优先于 entry 新买入。
- learning / historical_ml 只提供复盘、校准和阈值建议，不直接改变当日交易动作。
- qmt_execution 只执行总控给出的 OrderIntent，不反向改变策略判断。

## 输出文件

总控入口 `signal/v21_orchestrator.py::run_v21_backend_pipeline(...)` 写出：

- `output/daily_decision_snapshot.csv`
- `output/daily_decision_snapshot.json`
- `output/risk_gate_snapshot.csv`
- `output/risk_gate_snapshot.json`
- `output/portfolio_snapshot.csv`
- `output/portfolio_snapshot.json`
- `output/order_intent.csv`
- `output/order_intent.json`
- `output/learning_summary.csv`
- `output/learning_summary.json`
- `output/historical_ml_summary.csv`
- `output/historical_ml_summary.json`
- `output/v21_backend_status.json`

如果 `historical_ml`、`risk_warning` 或 `qmt_execution` 缺失，总控必须降级写出空摘要或草稿说明，并在 `fallback_reason`、`warnings` 中用中文说明，不中断主流程。

## DailyDecision

`DailyDecision` 是未来前端最重要的数据源，字段固定在 `contracts/v21_schema.py::DAILY_DECISION_FIELDS`。

核心含义：

- `trade_date`：总控决策日期。
- `signal_version`：固定为 `V2.1_BACKEND_INTEGRATION`。
- `market_state`：来自 pre_selection/entry 的市场状态。
- `risk_level`、`risk_score`：来自 RiskGate。
- `allow_entry`：总控裁决后是否允许实际买入。
- `freeze_entry`：风险门控是否冻结买入。
- `manual_takeover_required`：是否需要人工接管。
- `selected_sectors`：候选池涉及板块。
- `candidate_etfs`：pre_selection 入选 ETF。
- `actual_buy_etfs`：经过 RiskGate 和 exit 优先级裁决后仍可买入的 ETF。
- `entry_actions`、`exit_actions`、`portfolio_actions`：买入、退出和持仓动作解释。
- `learning_summary`、`historical_ml_summary`：复盘和历史机器学习建议。
- `order_intent_summary`：QMT 阶段消费的订单意图草稿。
- `explain`、`warnings`、`fallback_reason`：中文自然语言解释。
- `generated_at`：生成时间。

## RiskGate

`RiskGate` 来自 `risk_warning`，字段固定在 `RISK_GATE_FIELDS`。

字段：

- `trade_date`
- `risk_level`
- `risk_score`
- `freeze_entry`
- `equity_cap_override`
- `manual_takeover_required`
- `affected_sectors`
- `affected_etfs`
- `risk_events`
- `explain`
- `source`

RiskGate 优先级高于 entry、pre_selection、historical_ml 和 qmt_execution，不能被绕过。

## TrainingSample / CalibrationSample

`TrainingSample` 汇总 learning 和 historical_ml 建议，字段固定在 `TRAINING_SAMPLE_FIELDS`。它只用于 entry 校准、失败归因、阈值建议、历史回放和样本复盘，不允许自动修改交易参数。

## OrderIntent

`OrderIntent` 字段固定在 `ORDER_INTENT_FIELDS`。

字段：

- `trade_date`
- `etf_code`
- `etf_name`
- `action`
- `side`
- `target_weight`
- `current_weight`
- `delta_weight`
- `estimated_price`
- `estimated_amount`
- `order_type`
- `execution_mode`
- `requires_manual_confirm`
- `risk_check_passed`
- `risk_block_reason`
- `source_signal`
- `explain`

第一阶段 `execution_mode` 只能是 `SIMULATION`、`DRAFT`、`MANUAL_CONFIRM` 之一。默认 `requires_manual_confirm=True`，不允许默认全自动实盘下单。风险检查不通过时，只生成阻断说明，不生成可执行订单。

## PortfolioSnapshot

`PortfolioSnapshot` 字段固定在 `PORTFOLIO_SNAPSHOT_FIELDS`，用于持仓页、退出模型、QMT 执行意图和 DailyDecision。

字段：

- `trade_date`
- `etf_code`
- `etf_name`
- `current_weight`
- `target_weight`
- `cost_price`
- `current_price`
- `pnl`
- `pnl_pct`
- `holding_days`
- `sector`
- `risk_status`
- `exit_action`
- `explain`
