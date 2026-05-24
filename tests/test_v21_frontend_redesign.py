from __future__ import annotations

import inspect
import json
from pathlib import Path

import pandas as pd

import app
from data.portfolio_store import load_portfolio, save_portfolio
from signal.entry.engine import EntryEngine
from signal.v21_orchestrator import run_v21_backend_pipeline
from contracts.signal_schema import MarketState


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_v21_snapshots_missing_degrade_without_crash(tmp_path: Path) -> None:
    snapshots = app.load_v21_frontend_snapshots(tmp_path)

    assert set(snapshots["missing_files"]) == set(app.V21_FRONTEND_JSON_FILES)
    assert snapshots["daily_decision"] == {}
    assert snapshots["order_intent"] == []


def test_v21_daily_decision_risk_and_order_intent_parse(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "daily_decision_snapshot.json",
        {"trade_date": "2026-05-20", "risk_level": "R0", "candidate_etfs": [{"etf_code": "159915"}]},
    )
    _write_json(tmp_path / "risk_gate_snapshot.json", {"risk_level": "R0", "risk_score": 8})
    _write_json(
        tmp_path / "order_intent.json",
        [{"etf_code": "159915", "execution_mode": "DRAFT", "requires_manual_confirm": True}],
    )

    snapshots = app.load_v21_frontend_snapshots(tmp_path)
    status = app.build_v21_frontend_status(snapshots)

    assert status["trade_date"] == "2026-05-20"
    assert status["risk_level"] == "R0"
    assert app._v21_records(snapshots["order_intent"])[0]["requires_manual_confirm"] is True


def test_v21_frontend_exposes_ml_observation_status_and_fields(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "daily_decision_snapshot.json",
        {
            "trade_date": "2026-05-20",
            "risk_level": "R0",
            "ml_observation_status": "ML 观察模式已启用（仅供观察，不自动修改交易参数。）",
            "candidate_etfs": [
                {
                    "etf_code": "159915",
                    "ml_entry_advice": "建议等待回踩",
                    "ml_confidence": 0.73,
                    "ml_reason": "历史样本提示当前买点偏急。",
                    "ml_action_suggestion": "WAIT_PULLBACK",
                    "ml_observation_notice": "仅供观察，不自动修改交易参数。",
                }
            ],
            "entry_actions": [
                {
                    "etf_code": "159915",
                    "entry_action": "观察",
                    "ml_entry_advice": "建议等待回踩",
                    "ml_confidence": 0.73,
                    "ml_reason": "历史样本提示当前买点偏急。",
                    "ml_action_suggestion": "WAIT_PULLBACK",
                    "ml_observation_notice": "仅供观察，不自动修改交易参数。",
                }
            ],
        },
    )

    snapshots = app.load_v21_frontend_snapshots(tmp_path)
    status = app.build_v21_frontend_status(snapshots)
    frame = app._v21_frame(
        app._v21_records(snapshots["daily_decision"]["entry_actions"]),
        {
            "ml_entry_advice": "ML观察建议",
            "ml_confidence": "ML置信度",
            "ml_reason": "ML原因",
            "ml_action_suggestion": "ML动作建议",
            "ml_observation_notice": "ML观察说明",
        },
    )

    assert status["ml_observation_status"].startswith("ML 观察模式已启用")
    text = " ".join(frame.iloc[0].astype(str).tolist())
    assert "建议等待回踩" in text
    assert "仅供观察，不自动修改交易参数" in text


def test_positive_ml_suggestion_csv_reaches_controller_snapshot_and_frontend(tmp_path: Path) -> None:
    suggestions_path = tmp_path / "artifacts" / "historical_ml_61" / "generated" / "entry_calibration_suggestions.csv"
    suggestions_path.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "etf_code": "159915",
                "ml_entry_advice": "OBSERVE_WAIT_PULLBACK",
                "ml_confidence": 0.73,
                "ml_reason": "historical calibration matched current candidate; observe only",
                "ml_action_suggestion": "WAIT_PULLBACK",
            }
        ]
    ).to_csv(suggestions_path, index=False, encoding="utf-8-sig")
    pre_rows = [
        {
            "trade_date": "2026-05-20",
            "symbol": "159915",
            "name": "创业板ETF",
            "sector": "成长",
            "market_state": MarketState.ATTACK.value,
            "score": 88,
            "rank": 1,
            "selected": True,
            "momentum_20": 0.05,
            "momentum_60": 0.08,
            "momentum_120": 0.10,
            "distance_ma20": 0.01,
            "pullback": True,
            "close": 1.234,
            "reason": "进入候选池",
        }
    ]

    entry_rows = EntryEngine(first_buy_weight=0.3, target_weight=1.0).run(pre_rows, output_dir=tmp_path)
    original_buy_action = entry_rows[0]["buy_action"]
    original_position_size = entry_rows[0]["position_size"]

    result = run_v21_backend_pipeline(
        output_dir=tmp_path,
        pre_selection_rows=pre_rows,
        risk_gate={
            "risk_date": "2026-05-20",
            "risk_level": "R3",
            "risk_score": 90,
            "freeze_entry": True,
            "equity_cap_override": 0.0,
            "manual_takeover_required": True,
            "affected_etfs": ["159915"],
            "explain": "test RiskGate freeze",
        },
        entry_rows=entry_rows,
        exit_rows=[],
        learning_rows=[
            {
                "trade_date": "2026-05-20",
                "symbol": "159915",
                "lesson": "daily learning sample",
            }
        ],
        historical_ml_rows=[
            {
                "trade_date": "2026-05-20",
                "etf_code": "159915",
                "ml_entry_advice": "OBSERVE_WAIT_PULLBACK",
                "ml_confidence": 0.73,
                "ml_reason": "historical calibration matched current candidate; observe only",
                "ml_action_suggestion": "WAIT_PULLBACK",
            }
        ],
        holdings=[],
        qmt_execution_available=True,
    )
    snapshots = app.load_v21_frontend_snapshots(tmp_path)
    frame = app._v21_frame(
        app._v21_records(snapshots["daily_decision"]["entry_actions"]),
        {
            "entry_action": "买入动作",
            "target_weight": "建议仓位",
            "ml_entry_advice": "ML观察建议",
            "ml_confidence": "ML置信度",
            "ml_reason": "ML原因",
            "ml_action_suggestion": "ML动作建议",
            "ml_observation_notice": "ML观察说明",
        },
    )

    entry_signal = pd.read_csv(tmp_path / "entry_signal.csv")
    learning_summary = pd.read_csv(tmp_path / "learning_summary.csv")
    historical_summary = pd.read_csv(tmp_path / "historical_ml_summary.csv")
    decision = result["daily_decision"]
    entry_action = decision["entry_actions"][0]

    for field in ("ml_entry_advice", "ml_confidence", "ml_reason", "ml_action_suggestion"):
        assert field in entry_signal.columns
        assert field in entry_action
        assert field in decision["candidate_etfs"][0]
        assert field in learning_summary.columns
        assert field in historical_summary.columns
    assert entry_signal.iloc[0]["ml_entry_advice"] == "OBSERVE_WAIT_PULLBACK"
    assert entry_signal.iloc[0]["ml_action_suggestion"] == "WAIT_PULLBACK"
    assert entry_action["entry_action"] == original_buy_action
    assert entry_action["target_weight"] == original_position_size
    assert decision["actual_buy_etfs"] == []
    assert result["order_intent"][0]["risk_check_passed"] is False
    assert "OBSERVE_WAIT_PULLBACK" in frame.iloc[0].astype(str).to_string()
    assert "WAIT_PULLBACK" in frame.iloc[0].astype(str).to_string()
    assert "仅供观察，不自动修改交易参数" in frame.iloc[0].astype(str).to_string()


def test_v21_display_values_do_not_expose_raw_codes() -> None:
    frame = app._v21_frame(
        [{"state": "selected", "cache": "up_to_date", "empty": None, "bad": float("nan")}],
        {"state": "状态", "cache": "缓存", "empty": "空值", "bad": "异常值"},
    )
    text = " ".join(frame.iloc[0].astype(str).tolist())

    assert "进入候选池" in text
    assert "行情已是最新" in text
    assert "selected" not in text
    assert "up_to_date" not in text
    assert "nan" not in text.lower()
    assert "None" not in text


def test_v21_page_reads_snapshots_without_strategy_generation() -> None:
    page_source = inspect.getsource(app.render_page)
    loader_source = inspect.getsource(app.load_v21_frontend_snapshots)

    assert "run_project_command" not in page_source
    assert "command_compare_signal" not in page_source
    assert "load_dashboard_data" not in page_source
    assert "pre_selection_result.csv" not in page_source
    assert "entry_signal.csv" not in page_source
    assert "exit_signal.csv" not in page_source
    assert "learning_report.csv" not in page_source
    assert "daily_decision_snapshot.json" in loader_source
    assert "order_intent.json" in loader_source


def test_v21_v1_and_v2_regions_are_distinct() -> None:
    page_source = inspect.getsource(app.render_page)
    v1_source = inspect.getsource(app.render_v21_v1_reference)

    assert "今日总览" in page_source
    assert "V1 对照" in page_source
    assert "V1 传统信号，仅用于对照" in v1_source
    assert "compare_signal.csv" in v1_source


def test_qmt_page_defaults_to_manual_confirm_draft_boundary() -> None:
    qmt_source = inspect.getsource(app.render_v21_qmt)

    assert "QMT 当前为人工确认/模拟执行边界，不自动实盘下单。" in qmt_source
    assert "实盘自动下单" in qmt_source
    assert "没有" in qmt_source


def test_portfolio_save_once_round_trips(tmp_path: Path) -> None:
    portfolio_path = tmp_path / "portfolio.csv"
    position_path = tmp_path / "current_position.yaml"

    save_portfolio(
        [{"symbol": "159915", "name": "创业板ETF", "shares": 100.0, "average_buy_price": 2.5, "last_buy_date": "2026-05-20"}],
        cash=1000.0,
        current_empty=False,
        portfolio_path=portfolio_path,
        current_position_path=position_path,
    )

    saved = load_portfolio(portfolio_path)
    assert saved.iloc[0]["ETF代码"] == "159915"
    assert float(saved.iloc[0]["持仓份额"]) == 100.0
    assert float(saved.iloc[0]["平均买入价"]) == 2.5


def test_order_intent_frame_manual_confirm_is_chinese() -> None:
    frame = app._v21_order_frame(
        [
            {
                "etf_code": "159915",
                "action": "DRAFT_BUY",
                "side": "BUY",
                "execution_mode": "DRAFT",
                "requires_manual_confirm": True,
                "risk_check_passed": False,
                "source_signal": "V2_MODULAR",
            }
        ]
    )
    text = " ".join(frame.iloc[0].astype(str).tolist())

    assert "订单草稿" in text
    assert "买入方向" in text
    assert "人工确认" not in text
    assert "DRAFT" not in text
    assert "BUY" not in text
    assert "V2_MODULAR" not in text
    assert "是" in text
    assert "否" in text


def test_v21_semantic_translation_covers_internal_codes_and_field_names() -> None:
    frame = app._v21_frame(
        [
            {
                "action": "WATCH",
                "execution_mode": "MANUAL_CONFIRM",
                "risk_block_reason": "fallback_reason: qmt_execution 只读快照缺失，仅输出 DRAFT/MANUAL_CONFIRM 草稿。",
            }
        ],
        {"action": "动作", "execution_mode": "执行模式", "risk_block_reason": "风险阻断原因"},
    )
    text = " ".join(frame.iloc[0].astype(str).tolist())

    assert "观察，不买入" in text
    assert "人工确认" in text
    assert "降级原因" in text
    assert "QMT 执行模块" in text
    assert "订单草稿/人工确认" in text
    assert "WATCH" not in text
    assert "MANUAL_CONFIRM" not in text
    assert "fallback_reason" not in text


def test_v21_global_buttons_bind_real_action_api_without_fake_refresh_generation() -> None:
    source = inspect.getsource(app.render_v21_global_actions)
    refresh_block = source.split('key="v21_top_reload_snapshot"', 1)[1].split("with cols[1]", 1)[0]

    assert "action_api.refresh_market_data" in source
    assert "action_api.run_daily_signal" in source
    assert "action_api.rebuild_v21_snapshot" in source
    assert "action_api.get_tasks" in source
    assert "action_api.download_daily_report" in source
    assert "task_id" in refresh_block
    assert "action_api.run_daily_signal" not in refresh_block


def test_v21_page_action_sections_bind_required_actions() -> None:
    assert "action_api.run_daily_signal" in inspect.getsource(app.render_v21_overview)
    assert "action_api.refresh_market_data" in inspect.getsource(app.render_v21_overview)
    assert "action_api.recalculate_market_state" in inspect.getsource(app.render_v21_overview)
    assert "action_api.recalculate_risk_gate" in inspect.getsource(app.render_v21_overview)
    assert "action_api.run_pre_selection" in inspect.getsource(app.render_v21_candidates)
    assert "action_api.run_entry" in inspect.getsource(app.render_v21_candidates)
    assert "action_api.generate_order_intents" in inspect.getsource(app.render_v21_candidates)
    assert "action_api.sync_qmt_positions" in inspect.getsource(app.render_v21_portfolio)
    assert "action_api.run_exit" in inspect.getsource(app.render_v21_portfolio)


def test_v21_historical_qmt_and_data_quality_actions_are_bound() -> None:
    learning_source = inspect.getsource(app.render_v21_learning)
    qmt_source = inspect.getsource(app.render_v21_qmt)
    data_quality_source = inspect.getsource(app.render_v21_data_quality)

    assert "action_api.run_historical_replay" in learning_source
    assert "action_api.generate_entry_samples" in learning_source
    assert "action_api.auto_label_samples" in learning_source
    assert "action_api.prefill_manual_review_labels" in learning_source
    assert "action_api.adopt_high_confidence_manual_labels" in learning_source
    assert "action_api.adopt_medium_confidence_manual_labels" in learning_source
    assert "action_api.export_pending_manual_review_file" in learning_source
    assert "action_api.export_missed_winner_review_file" in learning_source
    assert "action_api.export_low_confidence_review_file" in learning_source
    assert "action_api.import_manual_corrections" in learning_source
    assert "强制重新生成校准报告" in learning_source
    assert "强制重新生成参数建议" in learning_source
    assert "强制重新运行过拟合检查" in learning_source
    assert "action_api.run_overfit_check" in learning_source
    assert "action_api.get_historical_ml_task_logs" in learning_source
    assert "action_api.submit_mock_order" in qmt_source
    assert "action_api.run_pre_order_risk_check" in qmt_source
    assert "action_api.get_execution_logs" in qmt_source
    assert "R3" in qmt_source and "R4" in qmt_source and "P0" in qmt_source
    assert "action_api.run_data_health_check" in data_quality_source
    assert "action_api.get_failed_tasks" in data_quality_source
    assert "action_api.get_recent_logs" in data_quality_source


def test_v21_task_queue_frame_formats_status_and_time_without_iso_raw() -> None:
    frame = app._v21_task_frame(
        [
            {
                "task_id": "task_demo",
                "action_name": "run_daily_signal",
                "status": "running",
                "progress": 45,
                "message": "处理中",
                "start_time": "2026-05-20T21:08:42+08:00",
                "end_time": "",
                "elapsed_seconds": 1.25,
                "status_detail": "cache_hit",
                "result_summary": {"output_rows": 123, "output_path": "artifacts/historical_ml_61/daily_etf_samples.csv", "used_cache": True},
                "error": "",
            }
        ]
    )
    text = " ".join(frame.iloc[0].astype(str).tolist())

    assert "正在执行" in text
    assert "重新生成今日信号" in text
    assert "2026-05-20 21:08:42" in text
    assert {"task_name", "elapsed_seconds", "result_count", "output_path", "used_cache", "status_detail", "result_summary"}.issubset(frame.columns)
    assert "123" in text
    assert "artifacts/历史学习模块_61/daily_etf_samples.csv" in text
    assert "cache_hit" in text
    assert "T21:08:42" not in text
    assert "+08:00" not in text


def test_v21_long_task_buttons_open_status_dialog() -> None:
    store_source = inspect.getsource(app._v21_store_action_response)
    dialog_source = inspect.getsource(app._v21_task_status_dialog)
    dialog_body_source = inspect.getsource(app._v21_task_status_dialog_body)
    button_source = inspect.getsource(app._v21_action_button)
    global_source = inspect.getsource(app.render_v21_global_actions)

    assert "v21_task_dialog_open" in store_source
    assert "v21_active_task_id" in store_source
    assert "open_dialog: bool = False" in inspect.getsource(app._v21_run_action)
    assert "@st.dialog" in dialog_source
    assert "action_api.get_task" in inspect.getsource(app._v21_current_task)
    assert "@st.fragment(run_every=1)" in dialog_body_source
    assert "st.progress" in dialog_body_source
    assert "确认关闭" in dialog_body_source
    assert "st.rerun" not in dialog_body_source
    assert "_v21_close_task_dialog_without_app_rerun" in dialog_body_source
    assert "_v21_task_status_dialog()" in button_source
    assert "_v21_render_task_status_dialog_if_needed" not in global_source


def test_manual_label_import_empty_path_disabled_with_reason() -> None:
    state = app._v21_manual_label_import_state("")

    assert state["disabled"] is True
    assert "请先导出人工标注表" in state["message"]


def test_manual_label_import_existing_path_enabled(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text("sample_id,review_label\n1,ok\n", encoding="utf-8")

    state = app._v21_manual_label_import_state(str(labels))

    assert state["disabled"] is False
    assert state["level"] == "success"


def test_manual_label_import_missing_path_disabled_with_error(tmp_path: Path) -> None:
    state = app._v21_manual_label_import_state(str(tmp_path / "missing.csv"))

    assert state["disabled"] is True
    assert state["level"] == "error"
    assert "不存在" in state["message"]


def test_v21_status_formats_dates_for_beijing_display() -> None:
    status = app.build_v21_frontend_status(
        {
            "daily_decision": {"trade_date": "2026-05-20", "generated_at": "2026-05-20T21:08:42+08:00"},
            "risk_gate": {"risk_level": "R0"},
            "status": {"generated_at": "2026-05-20T21:08:42+08:00"},
        }
    )

    assert status["trade_date"] == "2026-05-20"
    assert status["generated_at"] == "2026-05-20 21:08:42"
    assert "T" not in status["generated_at"]
    assert "+08:00" not in status["generated_at"]


def test_v21_position_editor_remains_form_submit_once_model() -> None:
    source = inspect.getsource(app.render_current_position_module)

    assert 'with st.form("position_editor_form"' in source
    assert "form_submit_button" in source
    assert "position_rows" in source
