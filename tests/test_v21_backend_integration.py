from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from contracts.v21_schema import DAILY_DECISION_FIELDS, ORDER_INTENT_FIELDS, RISK_GATE_FIELDS
from signal.v21_orchestrator import run_v21_backend_pipeline


def _pre_rows():
    return [
        {
            "trade_date": "2026-05-20",
            "symbol": "159915",
            "name": "创业板ETF",
            "sector": "成长",
            "market_state": "进攻",
            "score": 88,
            "rank": 1,
            "selected": True,
            "reason": "进入候选池。",
        }
    ]


def _entry_rows(action: str = "标准买入", weight: float = 0.3):
    return [
        {
            "trade_date": "2026-05-20",
            "symbol": "159915",
            "name": "创业板ETF",
            "market_state": "进攻",
            "buy_action": action,
            "position_size": weight,
            "confidence": 0.8,
            "entry_reason": "趋势成熟度：确认期；买点质量：回踩确认；理由：测试买入。",
            "source_file": "entry_signal.csv",
        }
    ]


def _risk(level: str = "R0", *, freeze: bool = False, manual: bool = False):
    return {
        "risk_date": "2026-05-20",
        "risk_level": level,
        "risk_score": 85 if level in {"R3", "R4", "P0"} else 5,
        "freeze_entry": freeze,
        "equity_cap_override": 0.0 if freeze else 1.0,
        "manual_takeover_required": manual,
        "affected_sectors": ["成长"] if freeze else [],
        "active_events": [{"title": "测试风险", "affected_assets": ["159915"]}] if freeze else [],
        "explain": "测试风险门控说明。",
    }


def test_risk_warning_freeze_blocks_actual_buy(tmp_path: Path) -> None:
    result = run_v21_backend_pipeline(
        output_dir=tmp_path,
        pre_selection_rows=_pre_rows(),
        risk_gate=_risk("R3", freeze=True),
        entry_rows=_entry_rows(),
        exit_rows=[],
        learning_rows=[],
        historical_ml_rows=[],
        holdings=[],
        qmt_execution_available=True,
    )

    decision = result["daily_decision"]
    assert decision["freeze_entry"] is True
    assert decision["actual_buy_etfs"] == []
    assert any(item["risk_check_passed"] is False for item in result["order_intent"])


def test_exit_clear_has_priority_over_new_buy(tmp_path: Path) -> None:
    exit_rows = [
        {
            "trade_date": "2026-05-20",
            "symbol": "159915",
            "name": "创业板ETF",
            "sell_action": "清仓",
            "reduce_ratio": 1.0,
            "exit_reason": "风险退出：跌破趋势线。",
        }
    ]
    result = run_v21_backend_pipeline(
        output_dir=tmp_path,
        pre_selection_rows=_pre_rows(),
        risk_gate=_risk(),
        entry_rows=_entry_rows(),
        exit_rows=exit_rows,
        learning_rows=[],
        historical_ml_rows=[],
        holdings=[{"symbol": "159915", "name": "创业板ETF", "shares": 1000, "cost_price": 1.0, "current_price": 1.2}],
        qmt_execution_available=True,
    )

    assert result["daily_decision"]["actual_buy_etfs"] == []
    assert any(item["side"] == "SELL" for item in result["order_intent"])


def test_historical_ml_missing_degrades_without_interrupt(tmp_path: Path) -> None:
    result = run_v21_backend_pipeline(
        output_dir=tmp_path,
        pre_selection_rows=_pre_rows(),
        risk_gate=_risk(),
        entry_rows=_entry_rows("观察", 0.0),
        exit_rows=[],
        learning_rows=[],
        holdings=[],
        qmt_execution_available=True,
    )

    assert result["historical_ml_summary"] == []
    assert "historical_ml" in result["daily_decision"]["fallback_reason"]
    assert (tmp_path / "historical_ml_summary.csv").exists()


def test_qmt_execution_missing_writes_draft_fallback(tmp_path: Path) -> None:
    result = run_v21_backend_pipeline(
        output_dir=tmp_path,
        pre_selection_rows=_pre_rows(),
        risk_gate=_risk(),
        entry_rows=_entry_rows(),
        exit_rows=[],
        learning_rows=[],
        historical_ml_rows=[],
        holdings=[],
        qmt_execution_available=False,
    )

    assert "qmt_execution" in result["daily_decision"]["fallback_reason"]
    assert all(item["execution_mode"] in {"DRAFT", "MANUAL_CONFIRM", "SIMULATION"} for item in result["order_intent"])


def test_daily_decision_and_risk_gate_write_csv_json(tmp_path: Path) -> None:
    run_v21_backend_pipeline(
        output_dir=tmp_path,
        pre_selection_rows=_pre_rows(),
        risk_gate=_risk(),
        entry_rows=_entry_rows("观察", 0.0),
        exit_rows=[],
        learning_rows=[],
        historical_ml_rows=[],
        holdings=[],
        qmt_execution_available=True,
    )

    daily_csv = pd.read_csv(tmp_path / "daily_decision_snapshot.csv")
    risk_csv = pd.read_csv(tmp_path / "risk_gate_snapshot.csv")
    daily_json = json.loads((tmp_path / "daily_decision_snapshot.json").read_text(encoding="utf-8"))
    risk_json = json.loads((tmp_path / "risk_gate_snapshot.json").read_text(encoding="utf-8"))
    assert set(DAILY_DECISION_FIELDS).issubset(daily_csv.columns)
    assert set(RISK_GATE_FIELDS).issubset(risk_csv.columns)
    assert set(DAILY_DECISION_FIELDS).issubset(daily_json.keys())
    assert set(RISK_GATE_FIELDS).issubset(risk_json.keys())


def test_order_intent_defaults_to_manual_confirm(tmp_path: Path) -> None:
    result = run_v21_backend_pipeline(
        output_dir=tmp_path,
        pre_selection_rows=_pre_rows(),
        risk_gate=_risk(),
        entry_rows=_entry_rows(),
        exit_rows=[],
        learning_rows=[],
        historical_ml_rows=[],
        holdings=[],
        qmt_execution_available=True,
    )

    assert result["order_intent"]
    assert all(item["requires_manual_confirm"] is True for item in result["order_intent"])


def test_frontend_output_fields_are_stable(tmp_path: Path) -> None:
    run_v21_backend_pipeline(
        output_dir=tmp_path,
        pre_selection_rows=_pre_rows(),
        risk_gate=_risk(),
        entry_rows=_entry_rows(),
        exit_rows=[],
        learning_rows=[],
        historical_ml_rows=[],
        holdings=[],
        qmt_execution_available=True,
    )

    daily = pd.read_csv(tmp_path / "daily_decision_snapshot.csv")
    order = pd.read_csv(tmp_path / "order_intent.csv")
    risk = pd.read_csv(tmp_path / "risk_gate_snapshot.csv")
    assert list(daily.columns) == list(DAILY_DECISION_FIELDS)
    assert list(order.columns) == list(ORDER_INTENT_FIELDS)
    assert list(risk.columns) == list(RISK_GATE_FIELDS)


def test_r3_r4_p0_risk_freezes_or_requires_manual(tmp_path: Path) -> None:
    for level in ("R3", "R4", "P0"):
        result = run_v21_backend_pipeline(
            output_dir=tmp_path / level,
            pre_selection_rows=_pre_rows(),
            risk_gate=_risk(level),
            entry_rows=_entry_rows(),
            exit_rows=[],
            learning_rows=[],
            historical_ml_rows=[],
            holdings=[],
            qmt_execution_available=True,
        )
        risk = result["risk_gate"]
        assert risk["freeze_entry"] is True or risk["manual_takeover_required"] is True


def test_post_924_regime_is_preserved(tmp_path: Path) -> None:
    result = run_v21_backend_pipeline(
        output_dir=tmp_path,
        pre_selection_rows=_pre_rows(),
        risk_gate=_risk(),
        entry_rows=_entry_rows("观察", 0.0),
        exit_rows=[],
        learning_rows=[
            {
                "trade_date": "2026-05-20",
                "symbol": "159915",
                "name": "创业板ETF",
                "return_pct": 0.02,
                "failure_attribution": "买点太差",
                "lesson": "测试复盘。",
                "adjustment": "仅建议，不改参数。",
            }
        ],
        historical_ml_rows=[],
        holdings=[],
        qmt_execution_available=True,
    )

    assert result["learning_summary"][0]["post_924_regime"] is True
