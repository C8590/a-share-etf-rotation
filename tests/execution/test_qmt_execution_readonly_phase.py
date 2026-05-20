from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from qmt_execution.contracts import Action
from qmt_execution.qmt_adapter import QmtAdapter, QmtSafetyError
from qmt_execution.qmt_mapping import map_qmt_order_update, map_qmt_position, map_qmt_trade_update
from qmt_execution.qmt_readonly_smoke import load_config, run_smoke


def test_example_config_defaults_to_readonly_and_submit_disabled() -> None:
    config = load_config(Path("config/qmt_execution.example.yaml"))

    assert config["enabled"] is False
    assert config["trading_env"] == "SIM"
    assert config["qmt_submit_enabled"] is False
    assert config["read_only"] is True
    assert config["allow_place_order"] is False
    assert config["allow_cancel_order"] is False


def test_readonly_smoke_refuses_disabled_example_config() -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        run_smoke(Path("config/qmt_execution.example.yaml"))


def test_readonly_true_rejects_place_order_before_qmt_submit() -> None:
    adapter = QmtAdapter("unused", "acct", 1, read_only=True, qmt_submit_enabled=True, allow_place_order=True)

    with pytest.raises(QmtSafetyError, match="read_only=True"):
        adapter.place_order(_intent_stub())


def test_allow_place_order_false_rejects_place_order() -> None:
    adapter = QmtAdapter("unused", "acct", 1, read_only=False, qmt_submit_enabled=True, allow_place_order=False)

    with pytest.raises(QmtSafetyError, match="allow_place_order=False"):
        adapter.place_order(_intent_stub())


def test_allow_cancel_order_false_rejects_cancel_order() -> None:
    adapter = QmtAdapter("unused", "acct", 1, read_only=False, allow_cancel_order=False)

    with pytest.raises(QmtSafetyError, match="allow_cancel_order=False"):
        adapter.cancel_order("123")


def test_qmt_raw_position_maps_to_position_snapshot() -> None:
    raw = {
        "stock_code": "510300.SH",
        "stock_name": "CSI 300 ETF",
        "volume": 1000,
        "can_use_volume": 800,
        "open_price": 4.0,
        "last_price": 4.2,
        "market_value": 4200.0,
        "profit": 200.0,
    }

    position = map_qmt_position(raw, default_update_time="2026-05-20T09:30:00")

    assert position.code == "510300.SH"
    assert position.name == "CSI 300 ETF"
    assert position.quantity == 1000
    assert position.available_quantity == 800
    assert position.cost_price == 4.0
    assert position.last_price == 4.2
    assert position.market_value == 4200.0
    assert position.pnl == 200.0
    assert position.pnl_ratio == pytest.approx(0.05)


def test_qmt_raw_order_maps_to_order_update() -> None:
    raw = {
        "order_id": "O-1",
        "stock_code": "510300.SH",
        "order_type": 24,
        "order_volume": 1000,
        "price": 4.2,
        "order_status": "FILLED",
        "traded_volume": 1000,
        "traded_price": 4.19,
        "status_msg": "ok",
    }

    update = map_qmt_order_update(raw, default_update_time="2026-05-20T09:31:00")

    assert update.broker_order_id == "O-1"
    assert update.code == "510300.SH"
    assert update.action == Action.SELL
    assert update.quantity == 1000
    assert update.limit_price == 4.2
    assert update.status == "FILLED"
    assert update.filled_quantity == 1000
    assert update.avg_price == 4.19
    assert update.error_message == "ok"


def test_qmt_raw_trade_maps_to_trade_update() -> None:
    raw = {
        "trade_id": "T-1",
        "order_id": "O-1",
        "stock_code": "510300.SH",
        "order_type": 23,
        "traded_volume": 1000,
        "traded_price": 4.21,
        "business_time": "2026-05-20T09:32:00",
    }

    update = map_qmt_trade_update(raw)

    assert update.broker_trade_id == "T-1"
    assert update.broker_order_id == "O-1"
    assert update.code == "510300.SH"
    assert update.action == Action.BUY
    assert update.quantity == 1000
    assert update.price == 4.21
    assert update.trade_time == "2026-05-20T09:32:00"


def test_readonly_smoke_rejects_enabled_submit_config(tmp_path) -> None:
    config_path = tmp_path / "qmt.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "enabled": True,
                "read_only": True,
                "qmt_submit_enabled": True,
                "allow_place_order": False,
                "allow_cancel_order": False,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="qmt_submit_enabled=false"):
        run_smoke(config_path)


def _intent_stub():
    from qmt_execution.contracts import OrderIntent, PriceType

    return OrderIntent(
        trade_date="2026-05-20",
        action=Action.BUY,
        code="510300.SH",
        name="CSI 300 ETF",
        target_weight=0.1,
        target_amount=100_000.0,
        quantity=1000,
        price_type=PriceType.LIMIT,
        limit_price=4.2,
        reason="readonly rejection test",
        source_signal="test",
        risk_level="R1",
        manual_confirmed=True,
    )
