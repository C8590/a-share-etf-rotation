from __future__ import annotations

import inspect
import json
from pathlib import Path

import pandas as pd

import app
from data.portfolio_store import load_portfolio, save_portfolio


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
                "error": "",
            }
        ]
    )
    text = " ".join(frame.iloc[0].astype(str).tolist())

    assert "正在执行" in text
    assert "重新生成今日信号" in text
    assert "2026-05-20 21:08:42" in text
    assert "T21:08:42" not in text
    assert "+08:00" not in text


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
