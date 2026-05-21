from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
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


def test_historical_ml_task_result_summary_fields_complete(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pd.DataFrame(
        [
            {"trade_date": "2026-01-02", "code": "510300", "auto_label": "good_entry", "label_status": "ok", "was_bought": False, "future_return_10d": 0.08, "outperform_market_10d": True, "outperform_sector_10d": False},
            {"trade_date": "2026-01-03", "code": "159915", "auto_label": "bad_entry", "label_status": "ok", "was_bought": True, "future_return_10d": -0.04, "outperform_market_10d": False, "outperform_sector_10d": False},
            {"trade_date": "2026-01-04", "code": "512000", "auto_label": "neutral_entry", "label_status": "insufficient_future_data", "was_bought": False, "future_return_10d": 0.01, "outperform_market_10d": False, "outperform_sector_10d": False},
        ]
    ).to_csv(artifacts / "entry_candidate_samples_labeled.csv", index=False)

    payload = control_actions.auto_label_samples(artifacts_dir=str(artifacts))
    task = _wait_for_terminal(queue, payload["task_id"])
    summary = task["result_summary"]

    required = {
        "output_path",
        "output_rows",
        "trade_days",
        "etf_count",
        "good_entry_count",
        "bad_entry_count",
        "neutral_entry_count",
        "review_queue_count",
        "failed_sample_count",
        "missed_winner_count",
        "used_cache",
        "cache_path",
        "next_step",
    }
    assert required.issubset(summary)
    assert summary["good_entry_count"] == 1
    assert summary["bad_entry_count"] == 1
    assert summary["neutral_entry_count"] == 1
    assert summary["insufficient_future_data"] == 1
    assert summary["used_cache"] is True


def test_historical_ml_cache_message_includes_path_and_rows(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pd.DataFrame([{"trade_date": "2026-01-02", "code": "510300"}]).to_csv(artifacts / "daily_etf_samples.csv", index=False)

    payload = control_actions.generate_daily_samples("2026-01-01", "2026-01-31", artifacts_dir=str(artifacts))
    task = _wait_for_terminal(queue, payload["task_id"])

    assert task["status_detail"] == "cache_hit"
    assert "命中缓存" in task["message"]
    assert "缓存文件行数：1" in task["message"]
    assert str(artifacts / "daily_etf_samples.csv") in task["message"]


def test_historical_ml_empty_output_is_not_plain_success(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)

    payload = control_actions.generate_daily_samples("2026-01-01", "2026-01-31", artifacts_dir=str(tmp_path / "missing"))
    task = _wait_for_terminal(queue, payload["task_id"])

    assert task["status"] == "success"
    assert task["status_detail"] == "completed_empty"
    assert "完成但无样本" in task["message"]
    assert task["result_summary"]["output_rows"] == 0


def test_export_manual_review_file_creates_task_record(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pd.DataFrame([{"sample_id": 1, "review_reason": "large_loss_entry"}]).to_csv(artifacts / "manual_review_queue.csv", index=False)

    payload = control_actions.export_manual_review_file(output_dir=tmp_path, artifacts_dir=str(artifacts))
    task = _wait_for_terminal(queue, payload["task_id"])
    summary = task["result_summary"]

    assert task["action_name"] == "export_manual_review_file"
    assert task["status"] == "success"
    assert summary["result_count"] == 1
    assert "manual_label" in summary["exported_columns"]
    assert Path(summary["output_path"]).exists()


def test_import_unfilled_manual_review_queue_warns_zero_valid_labels(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    labels = tmp_path / "manual_review_queue.csv"
    pd.DataFrame([{"sample_id": 1, "manual_label": "", "manual_review_note": ""}]).to_csv(labels, index=False)

    payload = control_actions.import_manual_labels(str(labels))
    task = _wait_for_terminal(queue, payload["task_id"])
    summary = task["result_summary"]

    assert task["action_name"] == "import_manual_labels"
    assert task["status_detail"] == "imported_no_valid_manual_labels"
    assert summary["valid_manual_label_rows"] == 0
    assert summary["used_manual_labels"] is False
    assert "有效人工标注为 0" in task["message"]


def test_import_filled_manual_label_file_counts_valid_rows(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    labels = tmp_path / "manual_review_queue_labeled.csv"
    pd.DataFrame(
        [
            {"sample_id": 1, "manual_label": "bad_entry", "manual_failure_reason": "追高", "manual_review_note": "确认", "manual_action": "observe"},
            {"sample_id": 2, "manual_label": "", "manual_failure_reason": "", "manual_review_note": "", "manual_action": ""},
        ]
    ).to_csv(labels, index=False)

    payload = control_actions.import_manual_labels(str(labels))
    task = _wait_for_terminal(queue, payload["task_id"])
    summary = task["result_summary"]

    assert task["status_detail"] == "imported_with_manual_labels"
    assert summary["valid_manual_label_rows"] == 1
    assert summary["empty_manual_label_rows"] == 1
    assert summary["used_manual_labels"] is True


def test_calibration_report_summary_shows_manual_label_usage(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    imported = tmp_path / "historical_ml_manual_labels_imported.csv"
    pd.DataFrame([{"sample_id": 1, "manual_label": "bad_entry"}]).to_csv(imported, index=False)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pd.DataFrame([{"trade_date": "2026-01-02", "code": "510300", "auto_label": "bad_entry", "label_status": "ok"}]).to_csv(
        artifacts / "entry_candidate_samples_labeled.csv",
        index=False,
    )
    (artifacts / "entry_calibration_report.md").write_text("# report\n", encoding="utf-8")

    payload = control_actions.generate_entry_calibration_report(artifacts_dir=str(artifacts))
    task = _wait_for_terminal(queue, payload["task_id"])
    summary = task["result_summary"]

    assert summary["used_manual_labels"] is True
    assert summary["valid_manual_label_rows"] == 1
    assert summary["auto_label_sample_count"] == 1
    assert summary["manual_label_coverage"] == 1.0
    assert "是否使用人工标注：是" in task["message"]


def test_historical_task_logs_include_export_import_report_suggestions_and_overfit(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    labels = tmp_path / "labels.csv"
    labels.write_text("sample_id,manual_label\n1,bad_entry\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "entry_calibration_report.md").write_text("# report\n", encoding="utf-8")
    pd.DataFrame([{"x": 1}]).to_csv(artifacts / "entry_calibration_suggestions.csv", index=False)
    (artifacts / "ml_stability_report.md").write_text("# stability\n", encoding="utf-8")

    calls = [
        control_actions.export_manual_review_file(output_dir=tmp_path, artifacts_dir=str(artifacts)),
        control_actions.import_manual_labels(str(labels)),
        control_actions.generate_entry_calibration_report(artifacts_dir=str(artifacts)),
        control_actions.generate_parameter_suggestions(artifacts_dir=str(artifacts)),
        control_actions.run_overfit_check(artifacts_dir=str(artifacts)),
    ]
    for payload in calls:
        _wait_for_terminal(queue, payload["task_id"])

    logs = control_actions.get_historical_ml_task_logs(limit=100)["data"]["logs"]
    names = {item["action_name"] for item in logs}
    assert {"export_manual_review_file", "import_manual_labels", "generate_entry_calibration_report", "generate_parameter_suggestions", "run_overfit_check"}.issubset(names)


def test_prefill_adopt_and_low_confidence_review_actions(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pd.DataFrame(
        [
            {"review_reason": "large_loss_entry", "auto_label": "bad_entry", "was_bought": True, "exit_within_3d": False, "future_return_10d": -0.05, "future_max_drawdown_10d": -0.08, "data_quality_flag": "ok", "missing_ratio_60d": 0.0},
            {"review_reason": "unknown_review_reason", "auto_label": "neutral_entry", "was_bought": False, "data_quality_flag": "ok", "missing_ratio_60d": 0.0},
        ]
    ).to_csv(artifacts / "manual_review_queue.csv", index=False)

    prefill = control_actions.prefill_manual_review_labels(artifacts_dir=str(artifacts))
    prefill_task = _wait_for_terminal(queue, prefill["task_id"])
    assert prefill_task["status_detail"] == "prefilled"
    assert prefill_task["result_summary"]["auto_prefilled_rows"] == 2
    assert prefill_task["result_summary"]["high_confidence_rows"] == 1
    assert (artifacts / "manual_review_queue_prefilled.csv").exists()

    adopt = control_actions.adopt_high_confidence_manual_labels(artifacts_dir=str(artifacts))
    adopt_task = _wait_for_terminal(queue, adopt["task_id"])
    assert adopt_task["status_detail"] == "adopted_high_confidence"
    assert adopt_task["result_summary"]["adopted_rows"] == 1
    assert adopt_task["result_summary"]["adopted_missed_winner_rows"] == 0
    assert adopt_task["result_summary"]["pending_missed_winner_rows"] == 0
    assert (artifacts / "manual_review_queue_labeled.csv").exists()

    low = control_actions.export_low_confidence_review_file(output_dir=tmp_path, artifacts_dir=str(artifacts))
    low_task = _wait_for_terminal(queue, low["task_id"])
    assert low_task["status_detail"] == "exported_low_confidence"
    assert low_task["result_summary"]["low_confidence_rows"] == 1
    assert Path(low_task["result_summary"]["output_path"]).exists()


def test_medium_adopt_can_accept_missed_winner_and_pending_export_includes_missed(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pd.DataFrame(
        [
            {"review_reason": "large_loss_entry", "auto_label": "bad_entry", "was_bought": True, "future_return_10d": -0.05, "future_max_drawdown_10d": -0.08, "data_quality_flag": "ok", "missing_ratio_60d": 0.0},
            {"review_reason": "missed_big_winner", "auto_label": "good_entry", "was_candidate": True, "was_bought": False, "future_return_10d": 0.08, "data_quality_flag": "ok", "missing_ratio_60d": 0.0},
        ]
    ).to_csv(artifacts / "manual_review_queue.csv", index=False)

    _wait_for_terminal(queue, control_actions.prefill_manual_review_labels(artifacts_dir=str(artifacts))["task_id"])
    high = control_actions.adopt_high_confidence_manual_labels(artifacts_dir=str(artifacts))
    high_task = _wait_for_terminal(queue, high["task_id"])
    assert high_task["result_summary"]["pending_missed_winner_rows"] == 1
    assert "敢买类样本覆盖不足" in high_task["result_summary"]["manual_label_balance_warning"]

    pending = control_actions.export_pending_manual_review_file(output_dir=tmp_path, artifacts_dir=str(artifacts))
    pending_task = _wait_for_terminal(queue, pending["task_id"])
    assert pending_task["result_summary"]["pending_missed_winner_rows"] == 1
    assert Path(pending_task["result_summary"]["output_path"]).exists()

    medium = control_actions.adopt_medium_confidence_manual_labels(artifacts_dir=str(artifacts))
    medium_task = _wait_for_terminal(queue, medium["task_id"])
    assert medium_task["status_detail"] == "adopted_medium_confidence"
    assert medium_task["result_summary"]["adopted_missed_winner_rows"] == 1
    assert medium_task["result_summary"]["pending_missed_winner_rows"] == 0


def test_parameter_suggestions_message_splits_defense_and_courage_advice(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pd.DataFrame([{"x": 1}]).to_csv(artifacts / "entry_calibration_suggestions.csv", index=False)

    payload = control_actions.generate_parameter_suggestions(artifacts_dir=str(artifacts))
    task = _wait_for_terminal(queue, payload["task_id"])

    assert "防错建议" in task["message"]
    assert "敢买建议" in task["message"]


def test_calibration_report_warns_when_missed_winner_labels_are_pending(monkeypatch, tmp_path):
    queue = _install_tmp_queue(monkeypatch, tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pd.DataFrame(
        [
            {"trade_date": "2026-01-02", "code": "510300", "auto_label": "bad_entry", "label_status": "ok"},
            {"trade_date": "2026-01-03", "code": "159915", "auto_label": "good_entry", "label_status": "ok"},
        ]
    ).to_csv(artifacts / "entry_candidate_samples_labeled.csv", index=False)
    pd.DataFrame(
        [
            {"review_reason": "large_loss_entry", "manual_label": "valid_bad_entry"},
            {"review_reason": "missed_big_winner", "manual_label": ""},
        ]
    ).to_csv(artifacts / "manual_review_queue_labeled.csv", index=False)
    (artifacts / "entry_calibration_report.md").write_text("# report\n", encoding="utf-8")

    payload = control_actions.generate_entry_calibration_report(artifacts_dir=str(artifacts))
    task = _wait_for_terminal(queue, payload["task_id"])

    assert task["result_summary"]["adopted_failure_rows"] == 1
    assert task["result_summary"]["adopted_missed_winner_rows"] == 0
    assert task["result_summary"]["pending_missed_winner_rows"] == 1
    assert "当前报告主要基于失败类样本，未充分覆盖错过机会样本" in task["message"]


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
