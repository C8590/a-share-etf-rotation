from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from api import control_actions
from api.action_schema import ACTION_RESPONSE_FIELDS
from api.task_queue import TaskQueue


def _install_tmp_queue(monkeypatch, tmp_path: Path) -> TaskQueue:
    queue = TaskQueue(task_dir=tmp_path / "tasks", log_path=tmp_path / "logs" / "recent_actions.log")
    import api.task_queue as task_queue_module

    monkeypatch.setattr(task_queue_module, "DEFAULT_QUEUE", queue)
    monkeypatch.setattr(control_actions, "OUTPUT_DIR", tmp_path)
    return queue


def _assert_response(payload):
    assert set(ACTION_RESPONSE_FIELDS).issubset(payload.keys())
    assert isinstance(payload["success"], bool)
    assert isinstance(payload["message"], str)
    assert "T" in payload["timestamp"]


def _wait_for_terminal(queue: TaskQueue, task_id: str):
    for _ in range(50):
        task = queue.get_task(task_id)
        if task.get("status") in {"success", "failed", "cancelled"}:
            return task
        time.sleep(0.02)
    return queue.get_task(task_id)


def test_representative_actions_return_unified_contract(monkeypatch, tmp_path):
    _install_tmp_queue(monkeypatch, tmp_path)
    labels = tmp_path / "labels.csv"
    labels.write_text("sample_id,review_label\n1,ok\n", encoding="utf-8")

    actions = [
        control_actions.get_control_snapshot(output_dir=tmp_path),
        control_actions.recalculate_market_state(),
        control_actions.recalculate_risk_gate(risk_level="R3"),
        control_actions.run_pre_selection(),
        control_actions.run_entry(),
        control_actions.run_exit(),
        control_actions.download_daily_report(output_dir=tmp_path),
        control_actions.export_manual_review_file(output_dir=tmp_path),
        control_actions.import_manual_labels(str(labels)),
        control_actions.update_risk_event(title="测试"),
        control_actions.trigger_manual_takeover(),
        control_actions.release_manual_takeover(),
        control_actions.get_affected_sectors(output_dir=tmp_path),
        control_actions.export_risk_log(output_dir=tmp_path),
        control_actions.get_risk_level_explain("R3"),
        control_actions.connect_qmt(),
        control_actions.disconnect_qmt(),
        control_actions.generate_order_intents(output_dir=tmp_path),
        control_actions.run_pre_order_risk_check(risk_level="R0", output_dir=tmp_path),
        control_actions.cancel_mock_order("MOCK-1"),
        control_actions.get_execution_logs(),
        control_actions.check_etf_sample_count(),
        control_actions.check_missing_data(),
        control_actions.check_abnormal_prices(),
        control_actions.check_trading_calendar(),
        control_actions.clear_cache(cache_dir=tmp_path / "cache"),
        control_actions.get_failed_tasks(),
        control_actions.get_recent_logs(),
        control_actions.get_tasks(),
        control_actions.get_task("missing"),
        control_actions.get_historical_ml_task_logs(),
    ]

    for payload in actions:
        _assert_response(payload)


@pytest.mark.parametrize(
    "call",
    [
        lambda: control_actions.refresh_market_data(),
        lambda: control_actions.run_daily_signal(),
        lambda: control_actions.run_historical_replay("2026-01-01", "2026-01-31"),
        lambda: control_actions.generate_daily_samples("2026-01-01", "2026-01-31"),
        lambda: control_actions.generate_entry_samples("2026-01-01", "2026-01-31"),
        lambda: control_actions.auto_label_samples(),
        lambda: control_actions.generate_failure_samples(),
        lambda: control_actions.generate_missed_opportunity_samples(),
        lambda: control_actions.generate_manual_review_queue(),
        lambda: control_actions.generate_entry_calibration_report(),
        lambda: control_actions.generate_parameter_suggestions(),
        lambda: control_actions.run_overfit_check(),
        lambda: control_actions.sync_qmt_account(),
        lambda: control_actions.sync_qmt_positions(),
        lambda: control_actions.sync_qmt_orders(),
        lambda: control_actions.sync_qmt_trades(),
        lambda: control_actions.run_data_health_check(),
        lambda: control_actions.rebuild_v21_snapshot(),
        lambda: control_actions.rebuild_control_snapshot(),
    ],
)
def test_long_actions_return_task_id(monkeypatch, tmp_path, call):
    queue = _install_tmp_queue(monkeypatch, tmp_path)

    payload = call()

    _assert_response(payload)
    assert payload["success"] is True
    assert payload["task_id"]
    task = _wait_for_terminal(queue, payload["task_id"])
    assert task["task_id"] == payload["task_id"]
    assert task["status"] in {"running", "success"}


def test_refresh_market_data_and_daily_signal_are_non_blocking(monkeypatch, tmp_path):
    _install_tmp_queue(monkeypatch, tmp_path)

    started = time.perf_counter()
    refresh = control_actions.refresh_market_data()
    daily = control_actions.run_daily_signal()
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0
    assert refresh["task_id"]
    assert daily["task_id"]


def test_get_tasks_and_get_task_return_queue_records(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    payload = control_actions.refresh_market_data()
    task = _wait_for_terminal(queue, payload["task_id"])

    tasks_payload = control_actions.get_tasks()
    one_payload = control_actions.get_task(payload["task_id"])

    assert any(item["task_id"] == payload["task_id"] for item in tasks_payload["data"]["tasks"])
    assert one_payload["data"]["task"]["task_id"] == task["task_id"]


def test_qmt_mock_order_never_submits_live_order(monkeypatch, tmp_path):
    _install_tmp_queue(monkeypatch, tmp_path)

    payload = control_actions.submit_mock_order(risk_level="R0", code="159915", quantity=100)

    assert payload["success"] is True
    assert payload["data"]["execution_mode"] == "SIMULATION"
    assert payload["data"]["requires_manual_confirm"] is True
    assert payload["data"]["live_order_submitted"] is False


@pytest.mark.parametrize("level", ["R3", "R4", "P0"])
def test_r3_r4_p0_risk_blocks_qmt_mock_order(monkeypatch, tmp_path, level):
    _install_tmp_queue(monkeypatch, tmp_path)
    (tmp_path / "risk_gate_snapshot.json").write_text(json.dumps({"risk_level": level, "freeze_entry": True}, ensure_ascii=False), encoding="utf-8")

    payload = control_actions.submit_mock_order(output_dir=tmp_path, code="159915", quantity=100)

    assert payload["success"] is False
    assert "阻断" in payload["message"]
    assert payload["data"]["live_order_submitted"] is False


def test_order_intents_are_forced_to_safe_modes(monkeypatch, tmp_path):
    _install_tmp_queue(monkeypatch, tmp_path)
    (tmp_path / "order_intent.json").write_text(
        json.dumps([{"etf_code": "159915", "execution_mode": "LIVE", "requires_manual_confirm": False}], ensure_ascii=False),
        encoding="utf-8",
    )

    payload = control_actions.generate_order_intents(output_dir=tmp_path)
    intent = payload["data"]["order_intents"][0]

    assert intent["execution_mode"] == "DRAFT"
    assert intent["requires_manual_confirm"] is True
    assert intent["live_order_submitted"] is False
