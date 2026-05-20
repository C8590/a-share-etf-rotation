# V2.1 Action API Contract

本合同定义 V2.1 总控操作台调用的 Action API。第一阶段先提供 Python 函数接口，未来可以由 HTTP server 或 Streamlit 按相同口径包一层。

## 安全边界

- 不修改 `pre_selection`、`entry`、`exit`、`learning`、`historical_ml`、`risk_warning`、`qmt_execution` 的核心策略逻辑。
- 不修改 entry 阈值。
- 不修改动量、加速度、趋势成熟度、买入、退出、学习模型公式。
- 不修改 `contracts/signal_schema.py`。
- 不修改 `contracts/v21_schema.py` 字段含义。
- 不接入实盘自动下单。
- QMT 仅允许 mock broker、只读同步、订单草稿、模拟盘、人工确认。

## 统一返回

所有 Action 返回：

```python
{
    "success": bool,
    "message": "中文说明",
    "task_id": "长任务返回，短任务为空字符串",
    "data": {},
    "error": "失败原因，成功为空字符串",
    "timestamp": "ISO 后台时间"
}
```

后台时间保留 ISO；前端显示必须使用 `format_datetime_shanghai(value)` 转成 `YYYY-MM-DD HH:mm:ss`，交易日期使用 `format_trade_date(value)` 转成 `YYYY-MM-DD`。

## 任务队列

本地轻量任务队列位于：

- `output/tasks/task_index.json`
- `output/tasks/{task_id}.json`
- `output/tasks/results/{task_id}.json`
- `output/logs/recent_actions.log`

任务字段：

- `task_id`
- `action_name`
- `status`: `pending` / `running` / `success` / `failed` / `cancelled`
- `progress`
- `message`
- `start_time`
- `end_time`
- `error`
- `result_file`
- `created_by`
- `parameters`

查询口径：

- `GET /api/tasks` -> `get_tasks()`
- `GET /api/tasks/{task_id}` -> `get_task(task_id)`
- `GET /api/logs/recent` -> `get_recent_logs()`

## Control Actions

- `GET /api/control/snapshot` -> `get_control_snapshot()`
- `POST /api/control/refresh-market-data` -> `refresh_market_data()`，长任务
- `POST /api/control/run-daily-signal` -> `run_daily_signal()`，长任务
- `POST /api/control/recalculate-market-state` -> `recalculate_market_state()`
- `POST /api/control/recalculate-risk-gate` -> `recalculate_risk_gate()`
- `POST /api/control/run-pre-selection` -> `run_pre_selection()`
- `POST /api/control/run-entry` -> `run_entry()`
- `POST /api/control/run-exit` -> `run_exit()`
- `POST /api/control/run-data-health-check` -> `run_data_health_check()`，长任务
- `POST /api/control/rebuild-snapshot` -> `rebuild_v21_snapshot()`，长任务
- `GET /api/control/download-daily-report` -> `download_daily_report()`

## Historical ML Actions

- `POST /api/historical-ml/run-replay` -> `run_historical_replay(start_date, end_date)`，长任务
- `POST /api/historical-ml/generate-daily-samples` -> `generate_daily_samples(start_date, end_date)`，长任务
- `POST /api/historical-ml/generate-entry-samples` -> `generate_entry_samples(start_date, end_date)`，长任务
- `POST /api/historical-ml/auto-label` -> `auto_label_samples()`，长任务
- `POST /api/historical-ml/generate-failure-samples` -> `generate_failure_samples()`，长任务
- `POST /api/historical-ml/generate-missed-samples` -> `generate_missed_opportunity_samples()`，长任务
- `POST /api/historical-ml/generate-review-queue` -> `generate_manual_review_queue()`，长任务
- `GET /api/historical-ml/export-review-file` -> `export_manual_review_file()`
- `POST /api/historical-ml/import-manual-labels` -> `import_manual_labels(file_path)`
- `POST /api/historical-ml/generate-calibration-report` -> `generate_entry_calibration_report()`，长任务
- `POST /api/historical-ml/generate-parameter-suggestions` -> `generate_parameter_suggestions()`，长任务，只生成建议，不自动修改交易参数
- `POST /api/historical-ml/run-overfit-check` -> `run_overfit_check()`，长任务
- `GET /api/historical-ml/task-logs` -> `get_historical_ml_task_logs()`

## Risk Warning Actions

- `POST /api/risk-events/create` -> `create_risk_event()`
- `POST /api/risk-events/update` -> `update_risk_event()`
- `POST /api/risk-events/expire` -> `expire_risk_event()`
- `POST /api/risk-events/recalculate` -> `recalculate_risk_gate()`
- `POST /api/risk-events/manual-takeover` -> `trigger_manual_takeover()`
- `POST /api/risk-events/release-takeover` -> `release_manual_takeover()`
- `GET /api/risk-events/affected-sectors` -> `get_affected_sectors()`
- `GET /api/risk-events/export-risk-log` -> `export_risk_log()`
- `GET /api/risk-events/risk-level-explain` -> `get_risk_level_explain()`

R3/R4/P0 风险状态必须冻结 entry 和 QMT 下单意图。

## QMT Execution Actions

- `POST /api/qmt/connect` -> `connect_qmt()`
- `POST /api/qmt/disconnect` -> `disconnect_qmt()`
- `POST /api/qmt/sync-account` -> `sync_qmt_account()`，长任务
- `POST /api/qmt/sync-positions` -> `sync_qmt_positions()`，长任务
- `POST /api/qmt/sync-orders` -> `sync_qmt_orders()`，长任务
- `POST /api/qmt/sync-trades` -> `sync_qmt_trades()`，长任务
- `POST /api/qmt/generate-order-intents` -> `generate_order_intents()`
- `POST /api/qmt/run-risk-check` -> `run_pre_order_risk_check()`
- `POST /api/qmt/submit-mock-order` -> `submit_mock_order()`
- `POST /api/qmt/cancel-order` -> `cancel_mock_order()`
- `GET /api/qmt/execution-logs` -> `get_execution_logs()`

所有订单必须保持在 `DRAFT`、`SIMULATION`、`MANUAL_CONFIRM` 之一，并且默认 `requires_manual_confirm=True`。

## Data Quality Actions

- `POST /api/data-quality/run-health-check` -> `run_data_health_check()`，长任务
- `POST /api/data-quality/check-sample-count` -> `check_etf_sample_count()`
- `POST /api/data-quality/check-missing-data` -> `check_missing_data()`
- `POST /api/data-quality/check-abnormal-prices` -> `check_abnormal_prices()`
- `POST /api/data-quality/check-trading-calendar` -> `check_trading_calendar()`
- `POST /api/data-quality/clear-cache` -> `clear_cache()`
- `POST /api/data-quality/rebuild-snapshot` -> `rebuild_control_snapshot()`，长任务
- `GET /api/data-quality/failed-tasks` -> `get_failed_tasks()`
- `GET /api/logs/recent` -> `get_recent_logs()`
