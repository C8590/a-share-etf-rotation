from __future__ import annotations

import json
import shutil
import subprocess
import time
import hashlib
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from .action_schema import action_response, format_datetime_shanghai, format_trade_date
from .task_queue import TaskQueue, get_default_queue


OUTPUT_DIR = Path("output")
SAFE_QMT_MODES = {"DRAFT", "SIMULATION", "MANUAL_CONFIRM"}
RISK_BLOCK_LEVELS = {"R3", "R4", "P0"}
DEFAULT_HISTORICAL_ML_ARTIFACTS = Path("artifacts") / "historical_ml_61"
HISTORICAL_GENERATED_DIR = "generated"
HISTORICAL_TO_REVIEW_DIR = "to_review"
HISTORICAL_REVIEW_RETURN_DIR = "review_return"
HISTORICAL_STATE_DIR = "state"
HISTORICAL_LOGS_DIR = "logs"
HISTORICAL_CACHE_FILES = {
    "run_historical_replay": "daily_decision_snapshot.csv",
    "generate_daily_samples": "daily_etf_samples.csv",
    "generate_entry_samples": "entry_candidate_samples_unlabeled.csv",
    "auto_label_samples": "entry_candidate_samples_labeled.csv",
    "generate_failure_samples": "failure_samples.csv",
    "generate_missed_opportunity_samples": "missed_opportunity_samples.csv",
    "generate_manual_review_queue": "manual_review_queue.csv",
    "generate_entry_calibration_report": "entry_calibration_report.md",
    "generate_parameter_suggestions": "entry_calibration_suggestions.csv",
    "run_overfit_check": "ml_stability_report.md",
    "prefill_manual_review_labels": "manual_review_prefilled.csv",
    "adopt_high_confidence_manual_labels": "manual_review_accepted.csv",
    "adopt_medium_confidence_manual_labels": "manual_review_accepted.csv",
    "export_low_confidence_review_file": "low_confidence_review.csv",
    "export_pending_manual_review_file": "pending_human_review.csv",
    "export_missed_winner_review_file": "missed_big_winner_review.csv",
}
HISTORICAL_TASK_ACTIONS = set(HISTORICAL_CACHE_FILES) | {
    "export_manual_review_file",
    "import_manual_labels",
    "import_manual_corrections",
    "open_manual_review_folder",
    "open_manual_review_return_folder",
    "scan_manual_review_return_files",
    "import_latest_manual_review_return",
}
HISTORICAL_FINGERPRINTED_ACTIONS = {"generate_entry_calibration_report", "generate_parameter_suggestions", "run_overfit_check"}
HISTORICAL_MATERIALIZED_SAMPLE_ACTIONS = {
    "generate_failure_samples",
    "generate_missed_opportunity_samples",
    "generate_manual_review_queue",
}
HISTORICAL_STALE_FLAG_FILE = "historical_ml_stale_flags.json"
HISTORICAL_REVIEW_RETURN_PATTERNS = (
    "manual_review_labeled.csv",
    "manual_corrections.csv",
    "low_confidence_review_labeled.csv",
    "missed_big_winner_review_labeled.csv",
    "pending_human_review_labeled.csv",
    "*_labeled.csv",
    "*_corrections.csv",
)


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


def run_historical_replay(start_date: str, end_date: str, **parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("run_historical_replay", "historical_ml 历史回放任务已提交。", {"start_date": start_date, "end_date": end_date, **parameters}, runner=_historical_ml_runner)


def generate_daily_samples(start_date: str, end_date: str, **parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_daily_samples", "生成每日样本任务已提交。", {"start_date": start_date, "end_date": end_date, **parameters}, runner=_historical_ml_runner)


def generate_entry_samples(start_date: str, end_date: str, **parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_entry_samples", "生成 entry 候选样本任务已提交。", {"start_date": start_date, "end_date": end_date, **parameters}, runner=_historical_ml_runner)


def auto_label_samples(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("auto_label_samples", "自动打标签任务已提交。", parameters, runner=_historical_ml_runner)


def generate_failure_samples(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_failure_samples", "生成失败样本任务已提交。", parameters, runner=_historical_ml_runner)


def generate_missed_opportunity_samples(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_missed_opportunity_samples", "生成错过样本任务已提交。", parameters, runner=_historical_ml_runner)


def generate_manual_review_queue(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_manual_review_queue", "生成手工复核队列任务已提交。", parameters, runner=_historical_ml_runner)


def generate_entry_calibration_report(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_entry_calibration_report", "生成 entry 校准报告任务已提交。", parameters, runner=_historical_ml_runner)


def generate_parameter_suggestions(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("generate_parameter_suggestions", "生成参数建议任务已提交；不会自动修改交易参数。", parameters, runner=_historical_ml_runner)


def run_overfit_check(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("run_overfit_check", "过拟合/稳定性检查任务已提交。", parameters, runner=_historical_ml_runner)


def export_manual_review_file(output_dir: str | Path = OUTPUT_DIR, **parameters: Any) -> dict[str, Any]:
    payload = {"output_dir": str(output_dir), **parameters}
    return _enqueue_long_action("export_manual_review_file", "导出人工标注表任务已提交。", payload, runner=_manual_review_export_runner)


def import_manual_labels(file_path: str, **parameters: Any) -> dict[str, Any]:
    payload = {"file_path": str(file_path), **parameters}
    return _enqueue_long_action("import_manual_labels", "导入人工标注表任务已提交。", payload, runner=_manual_review_import_runner)


def prefill_manual_review_labels(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("prefill_manual_review_labels", "自动预填人工复核建议任务已提交。", parameters, runner=_manual_review_prefill_runner)


def adopt_high_confidence_manual_labels(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("adopt_high_confidence_manual_labels", "一键采纳高置信复核建议任务已提交。", parameters, runner=_manual_review_adopt_runner)


def adopt_medium_confidence_manual_labels(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("adopt_medium_confidence_manual_labels", "一键采纳中置信复核建议任务已提交。", parameters, runner=_manual_review_adopt_runner)


def export_low_confidence_review_file(output_dir: str | Path = OUTPUT_DIR, **parameters: Any) -> dict[str, Any]:
    payload = {"output_dir": str(output_dir), **parameters}
    return _enqueue_long_action("export_low_confidence_review_file", "导出低置信复核表任务已提交。", payload, runner=_manual_review_low_confidence_runner)


def export_pending_manual_review_file(output_dir: str | Path = OUTPUT_DIR, **parameters: Any) -> dict[str, Any]:
    payload = {"output_dir": str(output_dir), **parameters}
    return _enqueue_long_action("export_pending_manual_review_file", "导出待人工复核表任务已提交。", payload, runner=_manual_review_pending_runner)


def export_missed_winner_review_file(output_dir: str | Path = OUTPUT_DIR, **parameters: Any) -> dict[str, Any]:
    payload = {"output_dir": str(output_dir), **parameters}
    return _enqueue_long_action("export_missed_winner_review_file", "导出 missed_big_winner 复核表任务已提交。", payload, runner=_manual_review_missed_winner_runner)


def import_manual_corrections(file_path: str, **parameters: Any) -> dict[str, Any]:
    payload = {"file_path": str(file_path), **parameters}
    return _enqueue_long_action("import_manual_corrections", "导入人工修正表任务已提交。", payload, runner=_manual_review_import_runner)


def open_manual_review_folder(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("open_manual_review_folder", "打开待复核文件夹任务已提交。", parameters, runner=_manual_review_open_folder_runner)


def open_manual_review_return_folder(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("open_manual_review_return_folder", "打开回传文件夹任务已提交。", parameters, runner=_manual_review_open_folder_runner)


def scan_manual_review_return_files(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("scan_manual_review_return_files", "扫描人工回传文件任务已提交。", parameters, runner=_manual_review_scan_return_runner)


def import_latest_manual_review_return(**parameters: Any) -> dict[str, Any]:
    return _enqueue_long_action("import_latest_manual_review_return", "导入最新人工回传文件任务已提交。", parameters, runner=_manual_review_import_latest_return_runner)


def get_historical_ml_task_logs(limit: int = 50) -> dict[str, Any]:
    logs = [item for item in get_default_queue().recent_logs(limit=limit) if str(item.get("action_name", "")) in HISTORICAL_TASK_ACTIONS]
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


def _historical_dirs(artifacts_dir: Path) -> dict[str, Path]:
    dirs = {
        "root": artifacts_dir,
        "generated": artifacts_dir / HISTORICAL_GENERATED_DIR,
        "to_review": artifacts_dir / HISTORICAL_TO_REVIEW_DIR,
        "review_return": artifacts_dir / HISTORICAL_REVIEW_RETURN_DIR,
        "state": artifacts_dir / HISTORICAL_STATE_DIR,
        "logs": artifacts_dir / HISTORICAL_LOGS_DIR,
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def _historical_generated_path(artifacts_dir: Path, filename: str) -> Path:
    return _historical_dirs(artifacts_dir)["generated"] / filename


def _historical_to_review_path(artifacts_dir: Path, filename: str) -> Path:
    return _historical_dirs(artifacts_dir)["to_review"] / filename


def _historical_state_path(artifacts_dir: Path, filename: str) -> Path:
    return _historical_dirs(artifacts_dir)["state"] / filename


def _historical_review_return_path(artifacts_dir: Path, filename: str) -> Path:
    return _historical_dirs(artifacts_dir)["review_return"] / filename


def _historical_output_path(artifacts_dir: Path, action_name: str) -> Path:
    filename = HISTORICAL_CACHE_FILES.get(action_name, "entry_candidate_samples_labeled.csv")
    if action_name in {
        "generate_manual_review_queue",
        "prefill_manual_review_labels",
        "adopt_high_confidence_manual_labels",
        "adopt_medium_confidence_manual_labels",
        "export_low_confidence_review_file",
        "export_pending_manual_review_file",
        "export_missed_winner_review_file",
    }:
        return _historical_to_review_path(artifacts_dir, filename)
    return _historical_generated_path(artifacts_dir, filename)


def _historical_existing_path(artifacts_dir: Path, filename: str, bucket: str = "generated") -> Path:
    primary = _historical_dirs(artifacts_dir)[bucket] / filename
    legacy = artifacts_dir / filename
    if primary.exists():
        return primary
    return legacy if legacy.exists() else primary


def _historical_existing_review_path(artifacts_dir: Path, filename: str) -> Path:
    return _historical_existing_path(artifacts_dir, filename, "to_review")


def _historical_existing_generated_path(artifacts_dir: Path, filename: str) -> Path:
    return _historical_existing_path(artifacts_dir, filename, "generated")


def _historical_existing_any_path(artifacts_dir: Path, filename: str, buckets: Sequence[str]) -> Path:
    dirs = _historical_dirs(artifacts_dir)
    for bucket in buckets:
        path = dirs[bucket] / filename
        if path.exists():
            return path
    legacy = artifacts_dir / filename
    if legacy.exists():
        return legacy
    return dirs[buckets[0]] / filename


def _mirror_legacy_historical_file(path: Path, artifacts_dir: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    legacy = artifacts_dir / path.name
    if legacy.resolve() == path.resolve():
        return
    legacy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, legacy)


def _historical_latest_return_file(artifacts_dir: Path) -> Path | None:
    review_return = _historical_dirs(artifacts_dir)["review_return"]
    candidates: dict[Path, None] = {}
    for pattern in HISTORICAL_REVIEW_RETURN_PATTERNS:
        for path in review_return.glob(pattern):
            if path.is_file():
                candidates[path] = None
    if not candidates:
        return None
    return max(candidates.keys(), key=lambda path: path.stat().st_mtime_ns)


def _historical_return_scan_summary(artifacts_dir: Path) -> dict[str, Any]:
    review_return = _historical_dirs(artifacts_dir)["review_return"]
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in HISTORICAL_REVIEW_RETURN_PATTERNS:
        for path in review_return.glob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                files.append(path)
    files.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    latest = files[0] if files else None
    return {
        "review_return_dir": str(review_return),
        "valid_return_file_count": len(files),
        "latest_return_file": str(latest or ""),
        "return_files": [str(path) for path in files],
        "result_count": len(files),
        "output_path": str(latest or review_return),
    }


def _manual_review_export_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    source = _historical_existing_review_path(artifacts_dir, "manual_review_queue.csv")
    out_path = _historical_to_review_path(artifacts_dir, "manual_review_queue.csv")
    queue.update_task(record["task_id"], status="running", progress=35, message=f"正在导出人工标注表：{out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if source.exists():
        df = _read_csv_if_exists(source)
    else:
        df = pd.DataFrame(columns=["sample_id", "review_label", "review_note"])
    for col in ["manual_label", "manual_failure_reason", "manual_action", "manual_confidence", "manual_review_note"]:
        if col not in df.columns:
            df[col] = ""
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    _mirror_legacy_historical_file(out_path, artifacts_dir)
    summary = {
        "output_path": str(out_path),
        "result_count": int(len(df)),
        "output_rows": int(len(df)),
        "exported_columns": list(df.columns),
        "to_review_dir": str(out_path.parent),
        "review_return_dir": str(_historical_dirs(artifacts_dir)["review_return"]),
        "suggested_next_step": "人工填写后请另存到 review_return 文件夹，再点击扫描回传文件或导入最新回传文件。",
        "used_cache": bool(source.exists()),
        "cache_path": str(source) if source.exists() else "",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    result_file = _write_task_result(record, summary)
    message = f"人工标注表已导出：{out_path}；导出行数：{len(df)}。下一步：{summary['suggested_next_step']}"
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail="exported")
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": "exported"}


def _manual_review_import_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    input_path = Path(str(parameters.get("file_path") or ""))
    queue.update_task(record["task_id"], status="running", progress=35, message=f"正在导入人工标注表：{input_path}")
    if not input_path.exists() or not input_path.is_file():
        raise FileNotFoundError(f"找不到人工标注表：{input_path}")
    df = pd.read_csv(input_path)
    stats = _manual_label_stats_from_frame(df)
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    target = _historical_to_review_path(artifacts_dir, "manual_review_labeled.csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_path, target)
    _mirror_legacy_historical_file(target, artifacts_dir)
    stale_flags = _mark_historical_downstream_stale(artifacts_dir, reason=str(record.get("action_name") or "manual_labels_imported"), input_path=target)
    summary = {
        "input_path": str(input_path),
        "total_rows": int(len(df)),
        **stats,
        "merged_output_path": str(target),
        "output_path": str(target),
        "result_count": int(stats["valid_manual_label_rows"]),
        "output_rows": int(len(df)),
        "used_manual_labels": bool(stats["valid_manual_label_rows"] > 0),
        "used_cache": False,
        "cache_path": "",
        **stale_flags,
        "suggested_next_step": "下一步：生成 entry 校准报告。",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    result_file = _write_task_result(record, summary)
    if summary["valid_manual_label_rows"] == 0:
        message = "导入成功，但有效人工标注为 0，本次不会改变校准报告。下一步：先填写人工标注，或生成 entry 校准报告查看自动标签结果。"
        status_detail = "imported_no_valid_manual_labels"
    else:
        message = f"人工标注表已导入：有效人工标注 {summary['valid_manual_label_rows']} / {summary['total_rows']}。下一步：生成 entry 校准报告。"
        status_detail = "imported_with_manual_labels"
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail=status_detail)
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": status_detail}


def _manual_review_open_folder_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    dirs = _historical_dirs(artifacts_dir)
    folder = dirs["review_return"] if record.get("action_name") == "open_manual_review_return_folder" else dirs["to_review"]
    queue.update_task(record["task_id"], status="running", progress=35, message=f"正在打开文件夹：{folder}")
    opened = False
    try:
        if subprocess.run(["cmd", "/c", "start", "", str(folder)], shell=False, check=False).returncode == 0:
            opened = True
    except Exception:
        opened = False
    summary = {
        "output_path": str(folder),
        "result_count": 1,
        "opened": opened,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "next_step": "从 to_review 取文件修改，完成后放入 review_return。" if folder == dirs["to_review"] else "把填写后的回传文件放入该目录，然后点击扫描回传文件。",
    }
    result_file = _write_task_result(record, summary)
    message = f"文件夹已准备：{folder}。下一步：{summary['next_step']}"
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail="folder_opened")
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": "folder_opened"}


def _manual_review_scan_return_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    queue.update_task(record["task_id"], status="running", progress=35, message="正在扫描 review_return 目录。")
    summary = _historical_return_scan_summary(artifacts_dir)
    summary.update(
        {
            "used_cache": False,
            "cache_path": "",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "next_step": "点击导入最新回传文件。" if summary["latest_return_file"] else "请将填写后的人工修正表放入 review_return 目录。",
        }
    )
    result_file = _write_task_result(record, summary)
    if summary["latest_return_file"]:
        message = f"扫描完成：发现 {summary['valid_return_file_count']} 个有效回传文件；即将导入的最新文件：{summary['latest_return_file']}。下一步：导入最新回传文件。"
        status_detail = "return_file_found"
    else:
        message = "未在 review_return 目录发现人工修正表，请将填写后的文件放入该目录。"
        status_detail = "no_return_file"
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail=status_detail)
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": status_detail}


def _manual_review_import_latest_return_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    latest = _historical_latest_return_file(artifacts_dir)
    if not latest:
        summary = {
            **_historical_return_scan_summary(artifacts_dir),
            "valid_manual_label_rows": 0,
            "empty_manual_label_rows": 0,
            "invalid_rows": 0,
            "affects_calibration_report": False,
            "next_step": "请将填写后的人工修正表放入 review_return 目录。",
        }
        result_file = _write_task_result(record, summary)
        message = "未在 review_return 目录发现人工修正表，请将填写后的文件放入该目录。"
        queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail="no_return_file")
        return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": "no_return_file"}
    record = dict(record)
    params = dict(parameters)
    params["file_path"] = str(latest)
    params["artifacts_dir"] = str(artifacts_dir)
    record["parameters"] = params
    result = _manual_review_import_runner(record, queue)
    summary = dict(result.get("result_summary") or {})
    summary["latest_return_file"] = str(latest)
    summary["affects_calibration_report"] = bool(summary.get("valid_manual_label_rows", 0))
    summary["next_step"] = "生成 entry 校准报告。" if summary["affects_calibration_report"] else "有效人工标注为 0，可先补充标注或继续查看自动标签报告。"
    result_file = _write_task_result(record, summary)
    message = str(result.get("message") or "")
    if int(summary.get("valid_manual_label_rows", 0) or 0) == 0:
        message = "导入成功，但有效人工标注为 0，本次不会改变校准报告。"
    else:
        message = f"已导入最新回传文件：{latest}；有效人工标注 {summary.get('valid_manual_label_rows', 0)} 行；会影响校准报告。下一步：生成 entry 校准报告。"
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": result.get("status_detail") or "imported_with_manual_labels"}


def _manual_review_prefill_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    from historical_ml.manual_label_suggester import generate_manual_label_suggestions_from_file

    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    source = _historical_existing_review_path(artifacts_dir, "manual_review_queue.csv")
    if not source.exists():
        raise FileNotFoundError(f"找不到 manual_review_queue：{source}")
    queue.update_task(record["task_id"], status="running", progress=35, message=f"正在自动预填人工复核建议：{source}")
    _, summary = generate_manual_label_suggestions_from_file(source, _historical_dirs(artifacts_dir)["to_review"], "manual_review_prefilled")
    _mirror_legacy_historical_file(_historical_to_review_path(artifacts_dir, "manual_review_prefilled.csv"), artifacts_dir)
    legacy_prefill = artifacts_dir / "manual_review_queue_prefilled.csv"
    shutil.copyfile(_historical_to_review_path(artifacts_dir, "manual_review_prefilled.csv"), legacy_prefill)
    stale_flags = _mark_historical_downstream_stale(artifacts_dir, reason=str(record.get("action_name") or "manual_review_prefilled"), input_path=_historical_to_review_path(artifacts_dir, "manual_review_prefilled.csv"))
    summary.update(
        {
            "input_path": str(source),
            "result_count": int(summary.get("auto_prefilled_rows", 0)),
            "used_cache": False,
            "cache_path": "",
            **stale_flags,
            "suggested_next_step": "下一步：一键采纳高置信标注，或导出低置信复核表。",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    )
    result_file = _write_task_result(record, summary)
    message = (
        f"自动预填完成：总行数 {summary['total_rows']}，自动预标 {summary['auto_prefilled_rows']}，"
        f"高置信 {summary['high_confidence_rows']}，中置信 {summary.get('medium_confidence_rows', 0)}，"
        f"低置信 {summary['low_confidence_rows']}，需人工复核 {summary['need_human_review_rows']}，"
        f"已采纳 {summary.get('accepted_rows', 0)}，待处理 {summary.get('pending_rows', 0)}；"
        f"missed_big_winner 总数 {summary.get('missed_big_winner_total', 0)}，"
        f"高/中/低={summary.get('missed_big_winner_high_confidence', 0)}/"
        f"{summary.get('missed_big_winner_medium_confidence', 0)}/"
        f"{summary.get('missed_big_winner_low_confidence', 0)}，"
        f"待复核 {summary.get('missed_big_winner_pending', 0)}。下一步：{summary['suggested_next_step']}"
    )
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail="prefilled")
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": "prefilled"}


def _manual_review_adopt_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    from historical_ml.io_utils import write_table
    from historical_ml.manual_label_suggester import adopt_high_confidence_suggestions

    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    min_confidence = "medium" if str(record.get("action_name")) == "adopt_medium_confidence_manual_labels" else "high"
    prefilled_path = _historical_existing_review_path(artifacts_dir, "manual_review_prefilled.csv")
    if not prefilled_path.exists():
        prefilled_path = _historical_existing_review_path(artifacts_dir, "manual_review_queue_prefilled.csv")
    if not prefilled_path.exists():
        raise FileNotFoundError(f"请先运行自动预填人工标注：{prefilled_path}")
    queue.update_task(record["task_id"], status="running", progress=35, message=f"正在采纳高置信预标：{prefilled_path}")
    prefilled = pd.read_csv(prefilled_path)
    adopted, summary = adopt_high_confidence_suggestions(prefilled, min_confidence=min_confidence)
    output_path = write_table(adopted, _historical_dirs(artifacts_dir)["to_review"], "manual_review_accepted", "csv")
    _mirror_legacy_historical_file(output_path, artifacts_dir)
    shutil.copyfile(output_path, artifacts_dir / "manual_review_queue_labeled.csv")
    stale_flags = _mark_historical_downstream_stale(artifacts_dir, reason=str(record.get("action_name") or "manual_label_adopted"), input_path=output_path)
    summary.update(
        {
            "input_path": str(prefilled_path),
            "output_path": str(output_path),
            "result_count": int(summary.get("adopted_rows", 0)),
            "used_cache": False,
            "cache_path": "",
            **stale_flags,
            "suggested_next_step": "下一步：导出低置信复核表，人工修正后导入；或生成使用人工标注的校准报告。",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    )
    result_file = _write_task_result(record, summary)
    label = "中置信及以上" if min_confidence == "medium" else "高置信"
    message = (
        f"{label}预标已采纳：采纳 {summary['adopted_rows']} 行，"
        f"adopted_failure_rows={summary.get('adopted_failure_rows', 0)}，"
        f"adopted_missed_winner_rows={summary.get('adopted_missed_winner_rows', 0)}，"
        f"pending_missed_winner_rows={summary.get('pending_missed_winner_rows', 0)}。"
        f"{summary.get('manual_label_balance_warning', '')} 下一步：{summary['suggested_next_step']}"
    )
    status_detail = "adopted_medium_confidence" if min_confidence == "medium" else "adopted_high_confidence"
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail=status_detail)
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": status_detail}


def _manual_review_low_confidence_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    from historical_ml.io_utils import write_table
    from historical_ml.manual_label_suggester import low_confidence_review_rows

    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    output_dir = _historical_dirs(artifacts_dir)["to_review"]
    prefilled_path = _historical_existing_review_path(artifacts_dir, "manual_review_prefilled.csv")
    if not prefilled_path.exists():
        prefilled_path = _historical_existing_review_path(artifacts_dir, "manual_review_queue_prefilled.csv")
    if not prefilled_path.exists():
        raise FileNotFoundError(f"请先运行自动预填人工标注：{prefilled_path}")
    queue.update_task(record["task_id"], status="running", progress=35, message=f"正在导出低置信复核表：{prefilled_path}")
    prefilled = pd.read_csv(prefilled_path)
    low, summary = low_confidence_review_rows(prefilled)
    output_path = write_table(low, output_dir, "low_confidence_review", "csv")
    _mirror_legacy_historical_file(output_path, artifacts_dir)
    summary.update(
        {
            "input_path": str(prefilled_path),
            "output_path": str(output_path),
            "result_count": int(summary.get("low_confidence_review_rows", len(low))),
            "used_cache": False,
            "cache_path": "",
            "suggested_next_step": "下一步：人工复核低置信样本，另存后导入人工修正表。",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    )
    result_file = _write_task_result(record, summary)
    message = (
        f"低置信复核表已导出：{output_path}；低置信/需复核 {summary.get('low_confidence_review_rows', len(low))} 行；"
        f"missed_big_winner pending={summary.get('pending_missed_winner_rows', 0)}。下一步：{summary['suggested_next_step']}"
    )
    if int(summary.get("low_confidence_review_rows", len(low)) or 0) == 0:
        message += " 无低置信样本需要人工复核，可直接继续生成校准报告。"
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail="exported_low_confidence")
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": "exported_low_confidence"}


def _manual_review_pending_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    from historical_ml.io_utils import write_table
    from historical_ml.manual_label_suggester import pending_review_rows

    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    output_dir = _historical_dirs(artifacts_dir)["to_review"]
    source_path = _preferred_manual_review_source(artifacts_dir)
    if not source_path.exists():
        raise FileNotFoundError(f"请先运行自动预填或采纳标注：{source_path}")
    queue.update_task(record["task_id"], status="running", progress=35, message=f"正在导出待人工复核表：{source_path}")
    source = pd.read_csv(source_path)
    pending, summary = pending_review_rows(source)
    output_path = write_table(pending, output_dir, "pending_human_review", "csv")
    _mirror_legacy_historical_file(output_path, artifacts_dir)
    summary.update(
        {
            "input_path": str(source_path),
            "output_path": str(output_path),
            "result_count": int(summary.get("pending_rows", 0)),
            "used_cache": False,
            "cache_path": "",
            "suggested_next_step": "下一步：人工复核 pending 样本，尤其是 pending_missed_winner_rows。",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    )
    result_file = _write_task_result(record, summary)
    message = (
        f"待人工复核表已导出：{output_path}；pending_rows={summary['pending_rows']}；"
        f"pending_missed_winner_rows={summary['pending_missed_winner_rows']}。下一步：{summary['suggested_next_step']}"
    )
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail="exported_pending_review")
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": "exported_pending_review"}


def _manual_review_missed_winner_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    from historical_ml.io_utils import write_table
    from historical_ml.manual_label_suggester import pending_review_rows

    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    output_dir = _historical_dirs(artifacts_dir)["to_review"]
    source_path = _preferred_manual_review_source(artifacts_dir)
    if not source_path.exists():
        raise FileNotFoundError(f"请先运行自动预填或采纳标注：{source_path}")
    queue.update_task(record["task_id"], status="running", progress=35, message=f"正在导出 missed_big_winner 复核表：{source_path}")
    source = pd.read_csv(source_path)
    missed = source.loc[source.get("review_reason", pd.Series("", index=source.index)).fillna("").astype(str).eq("missed_big_winner")].copy()
    output_path = write_table(missed, output_dir, "missed_big_winner_review", "csv")
    _mirror_legacy_historical_file(output_path, artifacts_dir)
    _, summary = pending_review_rows(source)
    summary.update(
        {
            "result_count": int(len(missed)),
            "input_path": str(source_path),
            "output_path": str(output_path),
            "used_cache": False,
            "cache_path": "",
            "suggested_next_step": "下一步：抽查 missed_big_winner，确认哪些属于敢买建议。",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    )
    result_file = _write_task_result(record, summary)
    message = f"missed_big_winner 复核表已导出：{output_path}；总数 {len(missed)}，pending {summary['missed_big_winner_pending']}。"
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail="exported_missed_winner_review")
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": "exported_missed_winner_review"}


def _historical_ml_runner(record: dict[str, Any], queue: TaskQueue) -> dict[str, Any]:
    started = time.perf_counter()
    action_name = str(record.get("action_name") or "")
    parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
    artifacts_dir = Path(parameters.get("artifacts_dir") or DEFAULT_HISTORICAL_ML_ARTIFACTS)
    cache_path = _historical_output_path(artifacts_dir, action_name)
    legacy_cache_path = artifacts_dir / HISTORICAL_CACHE_FILES.get(action_name, "entry_candidate_samples_labeled.csv")
    if not cache_path.exists() and legacy_cache_path.exists() and legacy_cache_path.is_file():
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(legacy_cache_path, cache_path)
    queue.update_task(record["task_id"], status="running", progress=30, message=f"正在读取 historical_ml 产物：{cache_path}")

    summary = _historical_result_summary(action_name, artifacts_dir, cache_path, parameters)
    if legacy_cache_path.exists() and legacy_cache_path.is_file():
        summary["legacy_cache_path"] = str(legacy_cache_path)
    elapsed = round(time.perf_counter() - started, 3)
    summary["elapsed_seconds"] = elapsed

    result_dir = OUTPUT_DIR / "tasks" / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_file = result_dir / f"{record['task_id']}.json"
    result_file.write_text(json.dumps({"task_id": record["task_id"], "action_name": action_name, "result_summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")

    if summary["used_cache"]:
        message = (
            f"命中缓存：{summary['cache_path']}；原因：{summary.get('cache_hit_reason', 'input_fingerprint 未变化')}；"
            f"input_fingerprint={summary.get('input_fingerprint', '')}；缓存生成时间：{summary.get('cache_generated_at', summary.get('cache_updated_at', ''))}；"
            f"缓存更新时间：{summary.get('cache_updated_at', '')}；是否包含人工标注：{'是' if summary.get('used_manual_labels') else '否'}；"
            f"valid_manual_label_rows={summary.get('valid_manual_label_rows', 0)}；缓存文件行数：{summary['output_rows']}；"
            f"legacy_cache_path={summary.get('legacy_cache_path', '')}；"
            f"elapsed_seconds={elapsed:.3f}。下一步：{summary['next_step']}"
        )
        status_detail = "cache_hit"
    elif summary["output_rows"] <= 0:
        message = f"完成但无样本：{summary.get('empty_reason', '未找到可用输出')}；建议下一步检查项：{summary['next_step']}；elapsed_seconds={elapsed:.3f}。"
        status_detail = "completed_empty"
    else:
        message = (
            f"实际执行：elapsed_seconds={elapsed:.3f}；输出文件：{summary['output_path']}；"
            f"输出行数：{summary['output_rows']}。下一步：{summary['next_step']}"
        )
        status_detail = "executed"
    if action_name == "generate_entry_calibration_report":
        message += (
            f" 是否使用人工标注：{'是' if summary.get('used_manual_labels') else '否'}；"
            f"人工标注样本数量：{summary.get('valid_manual_label_rows', 0)}；"
            f"自动标签样本数量：{summary.get('auto_label_sample_count', 0)}；"
            f"人工标注覆盖率：{summary.get('manual_label_coverage', 0):.2%}；"
            f"报告输出路径：{summary.get('output_path', '')}。"
        )
        if summary.get("manual_label_balance_warning"):
            message += f" {summary['manual_label_balance_warning']} 当前报告主要基于失败类样本，未充分覆盖错过机会样本。"
    if action_name == "generate_parameter_suggestions":
        message += (
            " 参数建议分组：防错建议=提高门槛/过热惩罚/假突破过滤/买后快速失败过滤；"
            "敢买建议=降低观察转试探门槛/允许小仓试探/优化候选池遗漏/调整 selected_not_bought 逻辑。"
        )
    queue.update_task(record["task_id"], progress=85, message=message, result_summary=summary, status_detail=status_detail)
    return {"message": message, "result_file": str(result_file), "result_summary": summary, "status_detail": status_detail}


def _historical_result_summary(action_name: str, artifacts_dir: Path, cache_path: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    summary = _blank_result_summary(action_name, cache_path, parameters)
    fingerprint = _historical_input_fingerprint(artifacts_dir, parameters)
    summary.update(
        {
            "input_fingerprint": fingerprint["fingerprint"],
            "input_fingerprint_detail": fingerprint,
            "force_regenerate": _force_regenerate_requested(action_name, parameters),
        }
    )

    cache_decision = _historical_cache_decision(action_name, artifacts_dir, cache_path, summary)
    executed_this_run = False
    if cache_decision["execute"]:
        summary["used_cache"] = False
        summary["cache_path"] = ""
        summary["cache_hit_reason"] = ""
        summary["cache_miss_reason"] = cache_decision["reason"]
        executed = _execute_historical_output_action(action_name, artifacts_dir, cache_path, summary)
        if executed:
            executed_this_run = True
            _write_historical_cache_metadata(action_name, cache_path, summary)
            _clear_historical_stale_flag(artifacts_dir, action_name)
        else:
            summary["used_cache"] = False
            summary["cache_hit_reason"] = ""
            summary["cache_miss_reason"] = cache_decision["reason"]
            summary["empty_reason"] = summary.get("empty_reason") or f"缓存失效但无法重新生成：{cache_path}"
            return summary
    elif not cache_path.exists():
        summary["empty_reason"] = f"缓存文件不存在：{cache_path}"
        return summary
    else:
        summary["used_cache"] = True
        summary["cache_hit_reason"] = cache_decision["reason"]

    summary.update(_cache_meta(cache_path))
    if executed_this_run:
        summary["used_cache"] = False
        summary["cache_path"] = ""
    summary["cache_metadata_path"] = str(_historical_cache_metadata_path(cache_path))
    daily = _read_csv_if_exists(_historical_existing_generated_path(artifacts_dir, "daily_etf_samples.csv"))
    candidates = _read_csv_if_exists(_historical_existing_generated_path(artifacts_dir, "entry_candidate_samples_unlabeled.csv"))
    labeled = _read_csv_if_exists(_historical_existing_generated_path(artifacts_dir, "entry_candidate_samples_labeled.csv"))
    review = _read_csv_if_exists(_historical_existing_review_path(artifacts_dir, "manual_review_queue.csv"))
    target = _read_csv_if_exists(cache_path) if cache_path.suffix.lower() == ".csv" else pd.DataFrame()

    summary["output_rows"] = _file_rows(cache_path, target)
    summary["trade_days"] = _nunique_date(_first_non_empty(target, labeled, candidates, daily), ["trade_date", "date"])
    summary["etf_count"] = _nunique(_first_non_empty(target, labeled, candidates, daily), "code")
    summary["daily_etf_samples_rows"] = len(daily)
    summary["entry_candidate_samples_rows"] = len(candidates)
    summary["actual_trading_days"] = _nunique_date(daily, ["trade_date", "date"])

    if not labeled.empty:
        labels = labeled.get("auto_label", pd.Series(dtype=str)).fillna("").astype(str)
        status = labeled.get("label_status", pd.Series(dtype=str)).fillna("").astype(str)
        summary["labeled_rows"] = len(labeled)
        summary["good_entry_count"] = int(labels.eq("good_entry").sum())
        summary["bad_entry_count"] = int(labels.eq("bad_entry").sum())
        summary["neutral_entry_count"] = int(labels.eq("neutral_entry").sum())
        summary["insufficient_future_data"] = int(status.eq("insufficient_future_data").sum())
        summary.update(_missed_winner_counts(labeled))

    if not review.empty:
        reasons = review.get("review_reason", pd.Series(dtype=str)).fillna("").astype(str)
        reason_counts = reasons.value_counts().to_dict()
        summary["review_queue_count"] = len(review)
        summary["review_queue_path"] = str(_historical_existing_review_path(artifacts_dir, "manual_review_queue.csv"))
        summary["reason_distribution"] = {str(k): int(v) for k, v in reason_counts.items()}
        summary["large_loss_count"] = int(reason_counts.get("large_loss_entry", 0))
        summary["quick_failure_count"] = int(reason_counts.get("quick_failure_entry", 0))
        summary["bought_and_knocked_out_count"] = int(reason_counts.get("bought_and_knocked_out", 0))
        summary["failed_sample_count"] = summary["large_loss_count"] + summary["quick_failure_count"] + summary["bought_and_knocked_out_count"]

    summary.update(_manual_prefill_stats(artifacts_dir))
    summary.update(_manual_label_stats(OUTPUT_DIR / "historical_ml_manual_labels_imported.csv", summary.get("labeled_rows", 0), artifacts_dir=artifacts_dir))
    summary.update(_historical_cache_metadata_fields(cache_path))
    if summary["output_rows"] <= 0:
        summary["empty_reason"] = "输出文件存在但没有可用数据行"
    if action_name == "generate_entry_calibration_report" and cache_path.exists():
        _write_entry_report_run_metadata(cache_path, summary)
        _mirror_legacy_historical_file(cache_path, artifacts_dir)
    return summary


def _blank_result_summary(action_name: str, cache_path: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    next_steps = {
        "run_historical_replay": "下一步生成每日样本",
        "generate_daily_samples": "下一步生成 entry 候选样本",
        "generate_entry_samples": "下一步自动打标签",
        "auto_label_samples": "下一步生成失败样本/错过样本/复核队列",
        "generate_failure_samples": "下一步检查失败样本归因",
        "generate_missed_opportunity_samples": "下一步检查错过样本过滤原因",
        "generate_manual_review_queue": "下一步导出人工标注表或生成校准报告",
        "generate_entry_calibration_report": "下一步生成参数建议或人工吸收清单",
        "generate_parameter_suggestions": "下一步仅做人工 review，不自动写回 entry",
        "run_overfit_check": "下一步检查稳定性失败分组，不直接上线模型",
    }
    return {
        "output_path": str(cache_path),
        "output_rows": 0,
        "trade_days": 0,
        "etf_count": 0,
        "good_entry_count": 0,
        "bad_entry_count": 0,
        "neutral_entry_count": 0,
        "review_queue_count": 0,
        "failed_sample_count": 0,
        "missed_winner_count": 0,
        "true_missed_winner_count": 0,
        "market_outperform_missed_winner_count": 0,
        "sector_outperform_missed_winner_count": 0,
        "used_cache": cache_path.exists(),
        "cache_path": str(cache_path) if cache_path.exists() else "",
        "next_step": next_steps.get(action_name, "下一步查看任务结果摘要"),
        "replay_start": parameters.get("start_date", ""),
        "replay_end": parameters.get("end_date", ""),
        "actual_trading_days": 0,
        "daily_etf_samples_rows": 0,
        "entry_candidate_samples_rows": 0,
        "labeled_rows": 0,
        "insufficient_future_data": 0,
        "large_loss_count": 0,
        "quick_failure_count": 0,
        "bought_and_knocked_out_count": 0,
        "review_queue_path": "",
        "reason_distribution": {},
    }


def _cache_meta(path: Path) -> dict[str, Any]:
    return {
        "used_cache": True,
        "cache_path": str(path),
        "cache_updated_at": format_datetime_shanghai(datetime.fromtimestamp(path.stat().st_mtime, tz=ZoneInfo("Asia/Shanghai"))),
    }


def _read_csv_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists() or path.suffix.lower() != ".csv":
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def _file_rows(path: Path, frame: pd.DataFrame) -> int:
    if path.suffix.lower() == ".csv":
        return int(len(frame))
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    return 1 if text else 0


def _first_non_empty(*frames: pd.DataFrame) -> pd.DataFrame:
    for frame in frames:
        if frame is not None and not frame.empty:
            return frame
    return pd.DataFrame()


def _nunique(frame: pd.DataFrame, col: str) -> int:
    if frame.empty or col not in frame.columns:
        return 0
    return int(frame[col].nunique(dropna=True))


def _nunique_date(frame: pd.DataFrame, cols: list[str]) -> int:
    for col in cols:
        if not frame.empty and col in frame.columns:
            return int(pd.to_datetime(frame[col], errors="coerce").dt.normalize().nunique(dropna=True))
    return 0


def _bool_series(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(False, index=frame.index)
    s = frame[col]
    if s.dtype == bool:
        return s.fillna(False)
    return s.fillna(False).map(lambda value: str(value).strip().lower() in {"1", "true", "yes", "y"})


def _missed_winner_counts(labeled: pd.DataFrame) -> dict[str, int]:
    ret10 = pd.to_numeric(labeled.get("future_return_10d"), errors="coerce")
    not_bought = ~_bool_series(labeled, "was_bought")
    raw = not_bought & (ret10 >= 0.06)
    market = raw & _bool_series(labeled, "outperform_market_10d")
    sector = raw & _bool_series(labeled, "outperform_sector_10d")
    true = market | sector
    return {
        "missed_winner_count": int(raw.sum()),
        "market_outperform_missed_winner_count": int(market.sum()),
        "sector_outperform_missed_winner_count": int(sector.sum()),
        "true_missed_winner_count": int(true.sum()),
    }


def _historical_input_fingerprint(artifacts_dir: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    accepted_path = _effective_manual_label_path(artifacts_dir)
    latest_return_path = _historical_latest_return_file(artifacts_dir)
    files = {
        "daily_etf_samples": _historical_existing_generated_path(artifacts_dir, "daily_etf_samples.csv"),
        "entry_candidate_samples_labeled": _historical_existing_generated_path(artifacts_dir, "entry_candidate_samples_labeled.csv"),
        "manual_review_queue": _historical_existing_review_path(artifacts_dir, "manual_review_queue.csv"),
        "manual_review_prefilled": _historical_existing_review_path(artifacts_dir, "manual_review_prefilled.csv"),
        "manual_review_accepted": _historical_existing_review_path(artifacts_dir, "manual_review_accepted.csv"),
        "latest_review_return": latest_return_path,
        "accepted_manual_labels": accepted_path,
    }
    file_meta = {name: _fingerprint_file(path) for name, path in files.items()}
    manual_stats = _manual_label_stats(Path("__missing_manual_labels__.csv"), artifacts_dir=artifacts_dir)
    payload = {
        "version": 2,
        "files": file_meta,
        "valid_manual_label_rows": int(manual_stats.get("valid_manual_label_rows", 0)),
        "accepted_high_confidence_rows": int(manual_stats.get("accepted_high_confidence_rows", 0)),
        "accepted_manual_label_rows": int(manual_stats.get("valid_manual_label_rows", 0)),
        "missed_big_winner_accepted_rows": int(manual_stats.get("adopted_missed_winner_rows", 0)),
        "config_version": str(parameters.get("config_version") or parameters.get("parameter_version") or "default"),
        "run_config": {
            "start_date": str(parameters.get("start_date") or ""),
            "end_date": str(parameters.get("end_date") or ""),
            "artifacts_dir": str(artifacts_dir),
        },
    }
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    payload["fingerprint"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return payload


def _fingerprint_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists() or not path.is_file():
        return {"path": str(path) if path else "", "exists": False, "mtime_ns": 0, "size": 0, "sha256": ""}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "mtime_ns": int(stat.st_mtime_ns),
        "mtime": format_datetime_shanghai(datetime.fromtimestamp(stat.st_mtime, tz=ZoneInfo("Asia/Shanghai"))),
        "size": int(stat.st_size),
        "sha256": _sha256_file(path),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _force_regenerate_requested(action_name: str, parameters: dict[str, Any]) -> bool:
    keys = {
        "generate_entry_calibration_report": "force_regenerate_calibration_report",
        "generate_parameter_suggestions": "force_regenerate_parameter_suggestions",
        "run_overfit_check": "force_regenerate_overfit_check",
    }
    return bool(parameters.get("force_regenerate") or parameters.get(keys.get(action_name, "")))


def _historical_cache_decision(action_name: str, artifacts_dir: Path, cache_path: Path, summary: dict[str, Any]) -> dict[str, Any]:
    if action_name not in HISTORICAL_FINGERPRINTED_ACTIONS:
        if cache_path.exists():
            return {"execute": False, "reason": "legacy cache action does not require input_fingerprint"}
        if action_name in HISTORICAL_MATERIALIZED_SAMPLE_ACTIONS:
            return {"execute": True, "reason": "cache file missing; materialize historical_ml sample output"}
        return {"execute": False, "reason": "cache file missing"}
    if summary.get("force_regenerate"):
        return {"execute": True, "reason": "用户请求强制重新生成"}
    if _is_historical_downstream_stale(artifacts_dir, action_name):
        return {"execute": True, "reason": "人工标注/预标状态变化，下游产物已标记 stale"}
    if not cache_path.exists():
        return {"execute": True, "reason": "缓存文件不存在"}
    metadata = _read_historical_cache_metadata(cache_path, artifacts_dir)
    if not metadata:
        return {"execute": True, "reason": "缓存缺少 input_fingerprint 元数据"}
    if metadata.get("input_fingerprint") != summary.get("input_fingerprint"):
        return {"execute": True, "reason": "input_fingerprint 已变化"}
    cached_valid = int(metadata.get("valid_manual_label_rows", -1))
    current_valid = int(summary.get("input_fingerprint_detail", {}).get("valid_manual_label_rows", 0))
    if cached_valid != current_valid:
        return {"execute": True, "reason": "缓存报告有效人工标注数与当前不一致"}
    return {"execute": False, "reason": "input_fingerprint unchanged and manual label count matched"}


def _historical_cache_metadata_path(cache_path: Path) -> Path:
    return cache_path.with_name(f"{cache_path.name}.meta.json")


def _read_historical_cache_metadata(cache_path: Path, artifacts_dir: Path) -> dict[str, Any]:
    primary = _historical_cache_metadata_path(cache_path)
    legacy = artifacts_dir / primary.name
    candidates = [path for path in [primary, legacy] if path.exists()]
    if not candidates:
        return {}
    newest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    data = _read_json(newest, {})
    return data if isinstance(data, dict) else {}


def _historical_cache_metadata_fields(cache_path: Path) -> dict[str, Any]:
    artifacts_dir = cache_path.parent.parent if cache_path.parent.name in {HISTORICAL_GENERATED_DIR, HISTORICAL_TO_REVIEW_DIR} else cache_path.parent
    metadata = _read_historical_cache_metadata(cache_path, artifacts_dir)
    if not isinstance(metadata, dict):
        return {}
    return {
        "cache_generated_at": metadata.get("generated_at", ""),
        "cache_input_fingerprint": metadata.get("input_fingerprint", ""),
        "cache_valid_manual_label_rows": int(metadata.get("valid_manual_label_rows", 0) or 0),
        "cache_used_manual_labels": bool(metadata.get("used_manual_labels", False)),
    }


def _write_historical_cache_metadata(action_name: str, cache_path: Path, summary: dict[str, Any]) -> None:
    detail = summary.get("input_fingerprint_detail") if isinstance(summary.get("input_fingerprint_detail"), dict) else {}
    valid_rows = int(summary.get("valid_manual_label_rows", detail.get("valid_manual_label_rows", 0)) or 0)
    metadata = {
        "action_name": action_name,
        "artifact_path": str(cache_path),
        "generated_at": format_datetime_shanghai(datetime.now(ZoneInfo("Asia/Shanghai"))),
        "input_fingerprint": summary.get("input_fingerprint", ""),
        "input_fingerprint_detail": detail,
        "used_manual_labels": bool(valid_rows > 0),
        "valid_manual_label_rows": valid_rows,
        "accepted_high_confidence_rows": int(detail.get("accepted_high_confidence_rows", 0) or 0),
    }
    path = _historical_cache_metadata_path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    artifacts_dir = Path(str(summary.get("input_fingerprint_detail", {}).get("run_config", {}).get("artifacts_dir") or cache_path.parent.parent))
    if cache_path.parent.name in {HISTORICAL_GENERATED_DIR, HISTORICAL_TO_REVIEW_DIR}:
        legacy_path = artifacts_dir / path.name
        legacy_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def _execute_historical_output_action(action_name: str, artifacts_dir: Path, cache_path: Path, summary: dict[str, Any]) -> bool:
    labeled = _historical_labeled_samples_for_outputs(artifacts_dir)
    if labeled.empty:
        summary["empty_reason"] = f"缺少 entry_candidate_samples_labeled，无法重新生成：{artifacts_dir / 'entry_candidate_samples_labeled.csv'}"
        return False
    if action_name in HISTORICAL_MATERIALIZED_SAMPLE_ACTIONS:
        try:
            review = _build_historical_review_queue_for_outputs(labeled)
            if action_name == "generate_failure_samples":
                output = review.loc[_review_reason_mask(review, {"large_loss_entry", "quick_failure_entry", "bought_and_knocked_out"})].copy()
            elif action_name == "generate_missed_opportunity_samples":
                output = review.loc[_review_reason_mask(review, {"missed_big_winner"})].copy()
            else:
                output = review
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            output.to_csv(cache_path, index=False, encoding="utf-8-sig")
            _mirror_legacy_historical_file(cache_path, artifacts_dir)
        except Exception as exc:  # noqa: BLE001
            summary["empty_reason"] = f"historical_ml 样本文件无法生成：{exc}"
            return False
        summary["output_path"] = str(cache_path)
        return cache_path.exists()
    if action_name in {"generate_entry_calibration_report", "generate_parameter_suggestions"}:
        from historical_ml.calibration import generate_entry_calibration_outputs

        generate_entry_calibration_outputs(labeled, _historical_dirs(artifacts_dir)["generated"])
        _mirror_legacy_historical_file(_historical_generated_path(artifacts_dir, "entry_calibration_report.md"), artifacts_dir)
        _mirror_legacy_historical_file(_historical_generated_path(artifacts_dir, "entry_calibration_suggestions.csv"), artifacts_dir)
        summary["output_path"] = str(cache_path)
        return cache_path.exists()
    if action_name == "run_overfit_check":
        from historical_ml.ml_stability import run_ml_stability

        try:
            run_ml_stability(labeled, _historical_dirs(artifacts_dir)["generated"])
            _mirror_legacy_historical_file(_historical_generated_path(artifacts_dir, "ml_stability_report.md"), artifacts_dir)
        except Exception as exc:  # noqa: BLE001
            summary["empty_reason"] = f"过拟合/稳定性检查无法重新生成：{exc}"
            return False
        summary["output_path"] = str(cache_path)
        return cache_path.exists()
    return False


def _build_historical_review_queue_for_outputs(labeled: pd.DataFrame) -> pd.DataFrame:
    from historical_ml.review_queue import build_manual_review_queue

    if "review_reason" in labeled.columns:
        return labeled.copy()
    return build_manual_review_queue(labeled)


def _historical_labeled_samples_for_outputs(artifacts_dir: Path) -> pd.DataFrame:
    labeled = _read_csv_if_exists(_historical_existing_generated_path(artifacts_dir, "entry_candidate_samples_labeled.csv"))
    if labeled.empty:
        return labeled
    manual_path = _effective_manual_label_path(artifacts_dir)
    manual = _read_csv_if_exists(manual_path) if manual_path else pd.DataFrame()
    if manual.empty or "manual_label" not in manual.columns:
        return labeled
    return _merge_manual_labels(labeled, manual)


def _merge_manual_labels(labeled: pd.DataFrame, manual: pd.DataFrame) -> pd.DataFrame:
    out = labeled.copy()
    manual_cols = [col for col in ["manual_label", "manual_failure_reason", "manual_action", "manual_confidence", "manual_review_note", "review_reason"] if col in manual.columns]
    keys = _manual_merge_keys(out, manual)
    if keys:
        overlay = manual[keys + manual_cols].copy()
        overlay = overlay.drop_duplicates(subset=keys, keep="last")
        out = out.merge(overlay, on=keys, how="left", suffixes=("", "_manual_src"))
        for col in manual_cols:
            src = f"{col}_manual_src"
            if src in out.columns:
                if col in out.columns:
                    out[col] = out[src].combine_first(out[col])
                else:
                    out[col] = out[src]
                out = out.drop(columns=[src])
        return out
    if len(manual) == len(out):
        for col in manual_cols:
            out[col] = manual[col].to_numpy()
    return out


def _manual_merge_keys(labeled: pd.DataFrame, manual: pd.DataFrame) -> list[str]:
    candidates = [
        ["sample_id"],
        ["trade_date", "code"],
        ["signal_date", "code"],
        ["execution_date", "code"],
    ]
    for keys in candidates:
        if all(key in labeled.columns and key in manual.columns for key in keys):
            return keys
    return []


def _mark_historical_downstream_stale(artifacts_dir: Path, reason: str, input_path: Path | None = None) -> dict[str, Any]:
    flags = {
        "calibration_report_stale": True,
        "suggestions_stale": True,
        "stability_report_stale": True,
        "reason": reason,
        "input_path": str(input_path or ""),
        "marked_at": format_datetime_shanghai(datetime.now(ZoneInfo("Asia/Shanghai"))),
    }
    path = _historical_state_path(artifacts_dir, HISTORICAL_STALE_FLAG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(flags, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: flags[key] for key in ["calibration_report_stale", "suggestions_stale", "stability_report_stale"]}


def _read_historical_stale_flags(artifacts_dir: Path) -> dict[str, Any]:
    data = _read_json(_historical_state_path(artifacts_dir, HISTORICAL_STALE_FLAG_FILE), {})
    return data if isinstance(data, dict) else {}


def _is_historical_downstream_stale(artifacts_dir: Path, action_name: str) -> bool:
    key = {
        "generate_entry_calibration_report": "calibration_report_stale",
        "generate_parameter_suggestions": "suggestions_stale",
        "run_overfit_check": "stability_report_stale",
    }.get(action_name, "")
    flags = _read_historical_stale_flags(artifacts_dir)
    return bool(key and flags.get(key))


def _clear_historical_stale_flag(artifacts_dir: Path, action_name: str) -> None:
    key = {
        "generate_entry_calibration_report": "calibration_report_stale",
        "generate_parameter_suggestions": "suggestions_stale",
        "run_overfit_check": "stability_report_stale",
    }.get(action_name, "")
    if not key:
        return
    path = _historical_state_path(artifacts_dir, HISTORICAL_STALE_FLAG_FILE)
    flags = _read_historical_stale_flags(artifacts_dir)
    if not flags:
        return
    flags[key] = False
    path.write_text(json.dumps(flags, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_entry_report_run_metadata(cache_path: Path, summary: dict[str, Any]) -> None:
    if not cache_path.exists():
        return
    marker_start = "<!-- historical_ml_run_metadata:start -->"
    marker_end = "<!-- historical_ml_run_metadata:end -->"
    block_lines = [
        marker_start,
        "## Run Metadata",
        "",
        f"- auto_label 样本数：{summary.get('auto_label_sample_count', 0)}",
        f"- 自动预填样本数：{summary.get('auto_prefilled_rows', 0)}",
        f"- 高置信采纳数：{summary.get('accepted_high_confidence_rows', 0)}",
        f"- 人工修正数：{summary.get('human_corrected_rows', 0)}",
        f"- 有效人工标注数：{summary.get('valid_manual_label_rows', 0)}",
        f"- 是否包含 missed_big_winner 分析：{'是' if (summary.get('missed_winner_count') or summary.get('missed_big_winner_total')) else '否'}",
        f"- missed_big_winner 人工采纳数：{summary.get('adopted_missed_winner_rows', 0)}",
        f"- 是否命中缓存：{'是' if summary.get('used_cache') else '否'}",
        f"- input_fingerprint：{summary.get('input_fingerprint', '')}",
        f"- 生成时间：{format_datetime_shanghai(datetime.now(ZoneInfo('Asia/Shanghai')))}",
        "",
        marker_end,
        "",
    ]
    text = cache_path.read_text(encoding="utf-8", errors="ignore")
    if marker_start in text and marker_end in text:
        before = text.split(marker_start, 1)[0]
        after = text.split(marker_end, 1)[1].lstrip("\n")
        text = before + "\n".join(block_lines) + after
    else:
        text = "\n".join(block_lines) + text
    cache_path.write_text(text, encoding="utf-8")


def _manual_label_stats(path: Path, auto_label_sample_count: int = 0, artifacts_dir: Path | None = None) -> dict[str, Any]:
    effective_path = path if path.exists() else (_effective_manual_label_path(artifacts_dir) if artifacts_dir else path)
    if not effective_path.exists():
        return _empty_manual_label_stats(auto_label_sample_count)
    try:
        df = pd.read_csv(effective_path)
    except Exception:  # noqa: BLE001
        return _empty_manual_label_stats(auto_label_sample_count)
    stats = _manual_label_stats_from_frame(df)
    stats["used_manual_labels"] = bool(stats["valid_manual_label_rows"] > 0)
    stats["manual_label_sample_count"] = int(stats["valid_manual_label_rows"])
    stats["auto_label_sample_count"] = int(auto_label_sample_count or 0)
    denominator = max(1, int(auto_label_sample_count or len(df) or 0))
    stats["manual_label_coverage"] = float(stats["valid_manual_label_rows"] / denominator)
    return stats


def _effective_manual_label_path(artifacts_dir: Path | None) -> Path:
    candidates: list[Path] = []
    if artifacts_dir:
        latest_return = _historical_latest_return_file(artifacts_dir)
        if latest_return:
            candidates.append(latest_return)
        candidates.extend(
            [
                _historical_to_review_path(artifacts_dir, "manual_review_labeled.csv"),
                _historical_to_review_path(artifacts_dir, "manual_review_accepted.csv"),
                _historical_to_review_path(artifacts_dir, "manual_review_queue_labeled.csv"),
                artifacts_dir / "manual_review_queue_labeled.csv",
            ]
        )
    candidates.append(OUTPUT_DIR / "historical_ml_manual_labels_imported.csv")
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return candidates[0] if candidates else Path("__missing_manual_labels__.csv")
    return max(existing, key=lambda path: path.stat().st_mtime_ns)


def _empty_manual_label_stats(auto_label_sample_count: int = 0) -> dict[str, Any]:
    return {
        "used_manual_labels": False,
        "manual_label_sample_count": 0,
        "valid_manual_label_rows": 0,
        "empty_manual_label_rows": 0,
        "invalid_rows": 0,
        "accepted_high_confidence_rows": 0,
        "auto_label_sample_count": int(auto_label_sample_count or 0),
        "manual_label_coverage": 0.0,
        "adopted_failure_rows": 0,
        "adopted_missed_winner_rows": 0,
        "pending_failure_rows": 0,
        "pending_missed_winner_rows": 0,
        "manual_label_balance_warning": "",
    }


def _manual_prefill_stats(artifacts_dir: Path) -> dict[str, Any]:
    prefilled_path = _historical_existing_review_path(artifacts_dir, "manual_review_prefilled.csv")
    if not prefilled_path.exists():
        prefilled_path = _historical_existing_review_path(artifacts_dir, "manual_review_queue_prefilled.csv")
    if not prefilled_path.exists():
        return {
            "auto_prefilled_rows": 0,
            "high_confidence_rows": 0,
            "medium_confidence_rows": 0,
            "low_confidence_rows": 0,
            "need_human_review_rows": 0,
            "human_review_required_rows": 0,
            "human_corrected_rows": 0,
            "final_effective_label_rows": 0,
            "missed_big_winner_total": 0,
            "missed_big_winner_high_confidence": 0,
            "missed_big_winner_medium_confidence": 0,
            "missed_big_winner_low_confidence": 0,
            "missed_big_winner_need_review": 0,
            "missed_big_winner_accepted": 0,
            "missed_big_winner_pending": 0,
        }
    try:
        df = pd.read_csv(prefilled_path)
    except Exception:  # noqa: BLE001
        return {
            "auto_prefilled_rows": 0,
            "high_confidence_rows": 0,
            "medium_confidence_rows": 0,
            "low_confidence_rows": 0,
            "need_human_review_rows": 0,
            "human_review_required_rows": 0,
            "human_corrected_rows": 0,
            "final_effective_label_rows": 0,
            "missed_big_winner_total": 0,
            "missed_big_winner_high_confidence": 0,
            "missed_big_winner_medium_confidence": 0,
            "missed_big_winner_low_confidence": 0,
            "missed_big_winner_need_review": 0,
            "missed_big_winner_accepted": 0,
            "missed_big_winner_pending": 0,
        }
    confidence = df.get("suggested_confidence", pd.Series(dtype=str)).fillna("").astype(str)
    suggested = df.get("suggested_manual_label", pd.Series(dtype=str)).fillna("").astype(str).str.strip().ne("")
    need = _bool_series(df, "need_human_review") if "need_human_review" in df.columns else pd.Series(False, index=df.index)
    manual_label = df.get("manual_label", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    suggested_label = df.get("suggested_manual_label", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    corrected = manual_label.ne("") & manual_label.ne(suggested_label)
    labeled_path = _effective_manual_label_path(artifacts_dir)
    final_rows = 0
    if labeled_path.exists():
        try:
            labeled = pd.read_csv(labeled_path)
            final_rows = int(labeled.get("manual_label", pd.Series(dtype=str)).fillna("").astype(str).str.strip().ne("").sum())
        except Exception:  # noqa: BLE001
            final_rows = 0
    missed = _review_reason_mask(df, {"missed_big_winner"})
    accepted = manual_label.ne("")
    pending = ~accepted
    return {
        "auto_prefilled_rows": int(suggested.sum()),
        "high_confidence_rows": int(confidence.eq("high").sum()),
        "medium_confidence_rows": int(confidence.eq("medium").sum()),
        "low_confidence_rows": int(confidence.eq("low").sum()),
        "need_human_review_rows": int(need.sum()),
        "human_review_required_rows": int(need.sum()),
        "human_corrected_rows": int(corrected.sum()),
        "final_effective_label_rows": int(final_rows),
        "missed_big_winner_total": int(missed.sum()),
        "missed_big_winner_high_confidence": int((missed & confidence.eq("high")).sum()),
        "missed_big_winner_medium_confidence": int((missed & confidence.eq("medium")).sum()),
        "missed_big_winner_low_confidence": int((missed & confidence.eq("low")).sum()),
        "missed_big_winner_need_review": int((missed & need).sum()),
        "missed_big_winner_accepted": int((missed & accepted).sum()),
        "missed_big_winner_pending": int((missed & pending).sum()),
    }


def _preferred_manual_review_source(artifacts_dir: Path) -> Path:
    for filename in ["manual_review_accepted.csv", "manual_review_labeled.csv", "manual_review_queue_labeled.csv"]:
        path = _historical_existing_review_path(artifacts_dir, filename)
        if path.exists():
            return path
    path = _historical_existing_review_path(artifacts_dir, "manual_review_prefilled.csv")
    if path.exists():
        return path
    return _historical_existing_review_path(artifacts_dir, "manual_review_queue_prefilled.csv")


def _manual_label_stats_from_frame(df: pd.DataFrame) -> dict[str, Any]:
    manual_cols = ["manual_label", "manual_failure_reason", "manual_action", "manual_confidence", "manual_review_note"]
    if "manual_label" not in df.columns:
        return {
            "valid_manual_label_rows": 0,
            "empty_manual_label_rows": int(len(df)),
            "invalid_rows": 0,
            "accepted_high_confidence_rows": 0,
            "adopted_failure_rows": 0,
            "adopted_missed_winner_rows": 0,
            "pending_failure_rows": 0,
            "pending_missed_winner_rows": 0,
            "manual_label_balance_warning": "",
        }
    label = df["manual_label"].fillna("").astype(str).str.strip()
    valid = label.ne("")
    other_filled = pd.Series(False, index=df.index)
    for col in manual_cols[1:]:
        if col in df.columns:
            other_filled = other_filled | df[col].fillna("").astype(str).str.strip().ne("")
    invalid = label.eq("") & other_filled
    failure = _review_reason_mask(df, {"large_loss_entry", "quick_failure_entry", "bought_and_knocked_out"})
    missed = _review_reason_mask(df, {"missed_big_winner"})
    if "suggested_confidence" in df.columns:
        high_confidence = df["suggested_confidence"].fillna("").astype(str).eq("high")
    else:
        high_confidence = df.get("manual_review_note", pd.Series("", index=df.index)).fillna("").astype(str).str.contains("auto_adopted_high_confidence", regex=False)
    warning = "当前人工标注覆盖偏向失败类样本，敢买类样本覆盖不足。" if int(missed.sum()) and int((valid & missed).sum()) == 0 else ""
    return {
        "valid_manual_label_rows": int(valid.sum()),
        "empty_manual_label_rows": int((~valid).sum()),
        "invalid_rows": int(invalid.sum()),
        "accepted_high_confidence_rows": int((valid & high_confidence).sum()),
        "adopted_failure_rows": int((valid & failure).sum()),
        "adopted_missed_winner_rows": int((valid & missed).sum()),
        "pending_failure_rows": int((~valid & failure).sum()),
        "pending_missed_winner_rows": int((~valid & missed).sum()),
        "manual_label_balance_warning": warning,
    }


def _review_reason_mask(df: pd.DataFrame, values: set[str]) -> pd.Series:
    if "review_reason" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["review_reason"].fillna("").astype(str).isin(values)


def _write_task_result(record: dict[str, Any], summary: dict[str, Any]) -> Path:
    result_dir = OUTPUT_DIR / "tasks" / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_file = result_dir / f"{record['task_id']}.json"
    result_file.write_text(
        json.dumps({"task_id": record["task_id"], "action_name": record.get("action_name", ""), "result_summary": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result_file


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
