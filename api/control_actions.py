from __future__ import annotations

import json
import shutil
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .action_schema import action_response, format_datetime_shanghai, format_trade_date
from .task_queue import TaskQueue, get_default_queue


OUTPUT_DIR = Path("output")
SAFE_QMT_MODES = {"DRAFT", "SIMULATION", "MANUAL_CONFIRM"}
RISK_BLOCK_LEVELS = {"R3", "R4", "P0"}


def get_control_snapshot(output_dir: str | Path = OUTPUT_DIR) -> dict[str, Any]:
    out_dir = Path(output_dir)
    data = {
        "daily_decision": _read_json(out_dir / "daily_decision_snapshot.json", {}),
        "risk_gate": _read_json(out_dir / "risk_gate_snapshot.json", {}),
        "portfolio_snapshot": _read_json(out_dir / "portfolio_snapshot.json", []),
        "order_intent": _read_json(out_dir / "order_intent.json", []),
        "learning_summary": _read_json(out_dir / "learning_summary.json", []),
        "historical_ml_summary": _read_json(out_dir / "historical_ml_summary.json", []),
        "backend_status": _read_json(out_dir / "v21_backend_status.json", {}),
    }
    return action_response(success=True, message="已读取 V2.1 总控快照。", data=data)


def refresh_market_data(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("refresh_market_data", "刷新行情数据任务已提交。", parameters, runner=_record_only_runner)


def run_daily_signal(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("run_daily_signal", "重新生成今日信号任务已提交。", parameters, runner=_record_only_runner)


def recalculate_market_state(**parameters: Any) -> dict[str, Any]:
    return _instant_action("recalculate_market_state", "重新计算市场状态动作已接收。", parameters)


def recalculate_risk_gate(**parameters: Any) -> dict[str, Any]:
    risk_level = str(parameters.get("risk_level") or "").upper()
    data = {"freeze_entry": risk_level in RISK_BLOCK_LEVELS, "manual_takeover_required": risk_level in {"R4", "P0"}}
    return _instant_action("recalculate_risk_gate", "重新计算风险门控动作已接收。", parameters, data=data)


def run_pre_selection(**parameters: Any) -> dict[str, Any]:
    return _instant_action("run_pre_selection", "pre_selection 动作已接收，未修改策略逻辑。", parameters)


def run_entry(**parameters: Any) -> dict[str, Any]:
    return _instant_action("run_entry", "entry 动作已接收，未修改 entry 阈值。", parameters)


def run_exit(**parameters: Any) -> dict[str, Any]:
    return _instant_action("run_exit", "exit 动作已接收，未修改退出公式。", parameters)


def run_data_health_check(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("run_data_health_check", "数据健康检查任务已提交。", parameters, runner=_record_only_runner)


def rebuild_v21_snapshot(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("rebuild_v21_snapshot", "重建 V2.1 总控快照任务已提交。", parameters, runner=_rebuild_snapshot_runner)


def download_daily_report(output_dir: str | Path = OUTPUT_DIR) -> dict[str, Any]:
    out_dir = Path(output_dir)
    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    report_path = reports_dir / f"v21_daily_report_{today}.md"
    snapshot = _read_json(out_dir / "daily_decision_snapshot.json", {})
    risk_gate = _read_json(out_dir / "risk_gate_snapshot.json", {})
    lines = [
        "# V2.1 今日日报",
        "",
        f"- 生成时间：{format_datetime_shanghai(datetime.now(ZoneInfo('Asia/Shanghai')))}",
        f"- 交易日期：{format_trade_date(snapshot.get('trade_date') or today)}",
        f"- 市场状态：{snapshot.get('market_state', '')}",
        f"- 风险等级：{risk_gate.get('risk_level') or snapshot.get('risk_level', '')}",
        f"- 是否冻结 entry：{risk_gate.get('freeze_entry') or snapshot.get('freeze_entry', False)}",
        f"- 是否人工接管：{risk_gate.get('manual_takeover_required') or snapshot.get('manual_takeover_required', False)}",
        "",
        "说明：日报来自总控快照，不修改策略参数，不触发实盘自动下单。",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return action_response(success=True, message="今日日报已生成。", data={"report_path": str(report_path)})


def run_historical_replay(start_date: str, end_date: str, **parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("run_historical_replay", "historical_ml 历史回放任务已提交。", {"start_date": start_date, "end_date": end_date, **parameters}, runner=_record_only_runner)


def generate_daily_samples(start_date: str, end_date: str, **parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_daily_samples", "生成每日样本任务已提交。", {"start_date": start_date, "end_date": end_date, **parameters}, runner=_record_only_runner)


def generate_entry_samples(start_date: str, end_date: str, **parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_entry_samples", "生成 entry 候选样本任务已提交。", {"start_date": start_date, "end_date": end_date, **parameters}, runner=_record_only_runner)


def auto_label_samples(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("auto_label_samples", "自动打标签任务已提交。", parameters, runner=_record_only_runner)


def generate_failure_samples(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_failure_samples", "生成失败样本任务已提交。", parameters, runner=_record_only_runner)


def generate_missed_opportunity_samples(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_missed_opportunity_samples", "生成错过样本任务已提交。", parameters, runner=_record_only_runner)


def generate_manual_review_queue(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_manual_review_queue", "生成手工复核队列任务已提交。", parameters, runner=_record_only_runner)


def export_manual_review_file(output_dir: str | Path = OUTPUT_DIR) -> dict[str, Any]:
    out_path = Path(output_dir) / "historical_ml_manual_review.csv"
    if not out_path.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("sample_id,review_label,review_note\n", encoding="utf-8-sig")
    return action_response(success=True, message="人工标注表已准备导出。", data={"file_path": str(out_path)})


def import_manual_labels(file_path: str, **parameters: Any) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return action_response(success=False, message="人工标注表不存在。", error=f"找不到文件：{file_path}")
    target = OUTPUT_DIR / "historical_ml_manual_labels_imported.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    return action_response(success=True, message="人工标注表已导入。", data={"file_path": str(target), "parameters": parameters})


def generate_entry_calibration_report(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_entry_calibration_report", "生成 entry 校准报告任务已提交。", parameters, runner=_record_only_runner)


def generate_parameter_suggestions(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_parameter_suggestions", "生成参数建议任务已提交；不会自动修改交易参数。", parameters, runner=_record_only_runner)


def run_overfit_check(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("run_overfit_check", "过拟合检查任务已提交。", parameters, runner=_record_only_runner)


def get_historical_ml_task_logs(limit: int = 50) -> dict[str, Any]:
    logs = [item for item in get_default_queue().recent_logs(limit=limit) if str(item.get("action_name", "")).startswith(("run_historical", "generate_", "auto_label"))]
    return action_response(success=True, message="已读取 historical_ml 任务日志。", data={"logs": logs})


def create_risk_event(**payload: Any) -> dict[str, Any]:
    try:
        store_cls = import_module("risk_warning.event_store").RiskEventStore
        event = store_cls().add_event(payload)
        return action_response(success=True, message="风险事件已创建。", data={"event": event.to_dict()})
    except Exception as exc:  # noqa: BLE001
        return action_response(success=False, message="风险事件创建失败。", error=f"创建风险事件失败：{exc}")


def update_risk_event(**payload: Any) -> dict[str, Any]:
    return _instant_action("update_risk_event", "风险事件更新动作已接收；请以前端表单提交完整事件内容。", payload)


def expire_risk_event(risk_date: str | None = None, **parameters: Any) -> dict[str, Any]:
    try:
        store_cls = import_module("risk_warning.event_store").RiskEventStore
        date_text = risk_date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        changed = store_cls().expire_events(date_text)
        return action_response(success=True, message="风险事件过期检查已完成。", data={"expired_count": changed, "risk_date": date_text, "parameters": parameters})
    except Exception as exc:  # noqa: BLE001
        return action_response(success=False, message="风险事件过期失败。", error=f"风险事件过期失败：{exc}")


def trigger_manual_takeover(**parameters: Any) -> dict[str, Any]:
    return action_response(success=True, message="人工接管已触发；entry 与 QMT 下单意图应冻结。", data={"manual_takeover_required": True, "freeze_entry": True, "parameters": parameters})


def release_manual_takeover(**parameters: Any) -> dict[str, Any]:
    return action_response(success=True, message="人工接管解除动作已接收；仍需重新计算风险门控。", data={"manual_takeover_required": False, "parameters": parameters})


def get_affected_sectors(output_dir: str | Path = OUTPUT_DIR) -> dict[str, Any]:
    risk_gate = _read_json(Path(output_dir) / "risk_gate_snapshot.json", {})
    return action_response(success=True, message="已读取受影响板块。", data={"affected_sectors": risk_gate.get("affected_sectors", [])})


def export_risk_log(output_dir: str | Path = OUTPUT_DIR) -> dict[str, Any]:
    path = Path(output_dir) / "risk_log.json"
    payload = {"risk_gate": _read_json(Path(output_dir) / "risk_gate_snapshot.json", {}), "exported_at": format_datetime_shanghai(datetime.now(ZoneInfo("Asia/Shanghai")))}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return action_response(success=True, message="风险日志已导出。", data={"file_path": str(path)})


def get_risk_level_explain(risk_level: str = "R0") -> dict[str, Any]:
    explains = {
        "R0": "常规风险，允许按策略流程生成草稿。",
        "R1": "轻度风险，需要关注但不冻结。",
        "R2": "中度风险，应降低权益仓位上限。",
        "R3": "高风险，冻结 entry 买入意图。",
        "R4": "极高风险，冻结 entry 并要求人工接管。",
        "P0": "P0 风险，冻结 entry 与 QMT 下单意图。",
    }
    level = str(risk_level or "R0").upper()
    return action_response(success=True, message="已读取风险等级说明。", data={"risk_level": level, "explain": explains.get(level, "未知风险等级。")})


def connect_qmt(**parameters: Any) -> dict[str, Any]:
    return action_response(success=True, message="QMT mock 连接已建立；未接入实盘自动下单。", data={"mode": "SIMULATION", "requires_manual_confirm": True, "parameters": parameters})


def disconnect_qmt(**parameters: Any) -> dict[str, Any]:
    return action_response(success=True, message="QMT mock 连接已断开。", data={"mode": "SIMULATION", "parameters": parameters})


def sync_qmt_account(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("sync_qmt_account", "同步 QMT 资金任务已提交。", parameters, runner=_record_only_runner)


def sync_qmt_positions(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("sync_qmt_positions", "同步 QMT 持仓任务已提交。", parameters, runner=_record_only_runner)


def sync_qmt_orders(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("sync_qmt_orders", "同步 QMT 委托任务已提交。", parameters, runner=_record_only_runner)


def sync_qmt_trades(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("sync_qmt_trades", "同步 QMT 成交任务已提交。", parameters, runner=_record_only_runner)


def generate_order_intents(output_dir: str | Path = OUTPUT_DIR, **parameters: Any) -> dict[str, Any]:
    intents = _read_json(Path(output_dir) / "order_intent.json", [])
    safe_intents = [_safe_order_intent(item) for item in intents if isinstance(item, dict)]
    return action_response(success=True, message="订单草稿已读取；仅限 DRAFT/SIMULATION/MANUAL_CONFIRM。", data={"order_intents": safe_intents, "parameters": parameters})


def run_pre_order_risk_check(risk_level: str | None = None, output_dir: str | Path = OUTPUT_DIR, **parameters: Any) -> dict[str, Any]:
    risk = _read_json(Path(output_dir) / "risk_gate_snapshot.json", {})
    level = str(risk_level or risk.get("risk_level") or "R0").upper()
    blocked = level in RISK_BLOCK_LEVELS or bool(risk.get("freeze_entry")) or bool(risk.get("manual_takeover_required"))
    return action_response(
        success=not blocked,
        message="下单前风控通过。" if not blocked else "下单前风控已阻断：R3/R4/P0 或人工接管状态。",
        data={"risk_level": level, "passed": not blocked, "live_order_submitted": False, "parameters": parameters},
        error="" if not blocked else "风险状态禁止提交 QMT 下单意图。",
    )


def submit_mock_order(risk_level: str | None = None, output_dir: str | Path = OUTPUT_DIR, **order: Any) -> dict[str, Any]:
    risk_check = run_pre_order_risk_check(risk_level=risk_level, output_dir=output_dir)
    if not risk_check["success"]:
        return action_response(
            success=False,
            message="模拟盘订单已被风险门控阻断。",
            data={"execution_mode": "SIMULATION", "requires_manual_confirm": True, "live_order_submitted": False, "order": order},
            error=risk_check["error"],
        )
    return action_response(
        success=True,
        message="模拟盘订单已提交到 mock broker 边界；未接入实盘自动下单。",
        data={"mock_order_id": f"MOCK-{datetime.now().strftime('%H%M%S')}", "execution_mode": "SIMULATION", "requires_manual_confirm": True, "live_order_submitted": False, "order": order},
    )


def cancel_mock_order(order_id: str = "", **parameters: Any) -> dict[str, Any]:
    return action_response(success=True, message="模拟盘撤单动作已接收。", data={"order_id": order_id, "execution_mode": "SIMULATION", "live_order_submitted": False, "parameters": parameters})


def get_execution_logs(limit: int = 50) -> dict[str, Any]:
    logs = [item for item in get_default_queue().recent_logs(limit=limit) if str(item.get("action_name", "")).startswith(("sync_qmt", "submit_mock", "generate_order"))]
    return action_response(success=True, message="已读取执行日志。", data={"logs": logs})


def check_etf_sample_count(**parameters: Any) -> dict[str, Any]:
    return _instant_action("check_etf_sample_count", "ETF 样本数量检查动作已接收。", parameters)


def check_missing_data(**parameters: Any) -> dict[str, Any]:
    return _instant_action("check_missing_data", "缺失数据检查动作已接收。", parameters)


def check_abnormal_prices(**parameters: Any) -> dict[str, Any]:
    return _instant_action("check_abnormal_prices", "异常价格检查动作已接收。", parameters)


def check_trading_calendar(**parameters: Any) -> dict[str, Any]:
    return _instant_action("check_trading_calendar", "交易日检查动作已接收。", parameters)


def clear_cache(cache_dir: str | Path = Path(".tmp"), **parameters: Any) -> dict[str, Any]:
    path = Path(cache_dir)
    removed = 0
    if path.exists() and path.is_dir():
        for item in path.glob("*"):
            if item.is_file():
                item.unlink()
                removed += 1
    return action_response(success=True, message="缓存清理完成。", data={"removed_files": removed, "cache_dir": str(path), "parameters": parameters})


def rebuild_control_snapshot(**parameters: Any) -> dict[str, Any]:
    return rebuild_v21_snapshot(**parameters)


def get_failed_tasks(limit: int = 50) -> dict[str, Any]:
    return action_response(success=True, message="已读取失败任务。", data={"tasks": get_default_queue().failed_tasks(limit=limit)})


def get_recent_logs(limit: int = 50) -> dict[str, Any]:
    return action_response(success=True, message="已读取最近日志。", data={"logs": get_default_queue().recent_logs(limit=limit)})


def get_tasks(limit: int | None = None) -> dict[str, Any]:
    return action_response(success=True, message="已读取任务队列。", data={"tasks": get_default_queue().list_tasks(limit=limit)})


def get_task(task_id: str) -> dict[str, Any]:
    task = get_default_queue().get_task(task_id)
    return action_response(success=bool(task), message="已读取任务详情。" if task else "任务不存在。", data={"task": task}, error="" if task else f"找不到任务：{task_id}")


def _enqueue_long_action(
    action_name: str,
    message: str,
    parameters: dict[str, Any],
    *,
    runner: Any,
    queue: TaskQueue | None = None,
) -> dict[str, Any]:
    task_queue = queue or get_default_queue()
    record = task_queue.enqueue(action_name, parameters, runner=runner, message=message)
    return action_response(success=True, message=message, task_id=record["task_id"], data={"task": record})


def _instant_action(action_name: str, message: str, parameters: dict[str, Any] | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
    get_default_queue().append_log(action_name, "success", message)
    payload = {"parameters": parameters or {}}
    if data:
        payload.update(data)
    return action_response(success=True, message=message, data=payload)


def _record_only_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    queue.update_task(record["task_id"], status="running", progress=30, message="任务已进入后台执行阶段。")
    result_dir = OUTPUT_DIR / "tasks" / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_file = result_dir / f"{record['task_id']}.json"
    result_file.write_text(
        json.dumps(
            {
                "task_id": record["task_id"],
                "action_name": record["action_name"],
                "parameters": record.get("parameters", {}),
                "strategy_logic_modified": False,
                "entry_threshold_modified": False,
                "live_auto_order_enabled": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    queue.update_task(record["task_id"], progress=80, message="任务结果已写入本地结果文件。")
    return {"message": "任务已完成，结果已写入本地任务结果文件。", "result_file": str(result_file)}


def _rebuild_snapshot_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    queue.update_task(record["task_id"], status="running", progress=20, message="正在调用 V2.1 总控快照编排器。")
    orchestrator = import_module("signal.v21_orchestrator")
    result = orchestrator.run_v21_backend_pipeline(output_dir=OUTPUT_DIR)
    result_file = OUTPUT_DIR / "v21_backend_status.json"
    queue.update_task(record["task_id"], progress=90, message="总控快照已重建，正在收尾。")
    return {"message": "V2.1 总控快照已重建。", "result_file": str(result_file), "result": result}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def _safe_order_intent(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    mode = str(result.get("execution_mode") or "DRAFT").upper()
    result["execution_mode"] = mode if mode in SAFE_QMT_MODES else "DRAFT"
    result["requires_manual_confirm"] = True
    result["live_order_submitted"] = False
    return result
