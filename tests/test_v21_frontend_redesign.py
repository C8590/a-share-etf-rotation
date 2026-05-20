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
    assert "数据已是最新" in text
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
        [{"etf_code": "159915", "execution_mode": "DRAFT", "requires_manual_confirm": True, "risk_check_passed": False}]
    )
    text = " ".join(frame.iloc[0].astype(str).tolist())

    assert "DRAFT" in text
    assert "是" in text
    assert "否" in text
