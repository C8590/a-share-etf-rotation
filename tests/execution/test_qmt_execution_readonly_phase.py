from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import qmt_execution.qmt_readonly_smoke as smoke
from qmt_execution.contracts import AccountSnapshot, Action
from qmt_execution.qmt_adapter import QmtAdapter, QmtSafetyError
from qmt_execution.qmt_mapping import map_qmt_order_update, map_qmt_position, map_qmt_trade_update
from qmt_execution.qmt_readonly_smoke import load_config, resolve_config_path, run_smoke


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


def test_missing_local_config_uses_disabled_example_without_connect(monkeypatch, tmp_path) -> None:
    missing_local = tmp_path / "missing.local.yaml"
    monkeypatch.setattr(smoke, "LOCAL_CONFIG", missing_local)
    monkeypatch.setattr(smoke, "DEFAULT_CONFIG", Path("config/qmt_execution.example.yaml"))

    assert resolve_config_path() == Path("config/qmt_execution.example.yaml")
    with pytest.raises(RuntimeError, match="disabled"):
        run_smoke(adapter_factory=lambda config: _fail_if_connected())


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
    _write_config(config_path, qmt_submit_enabled=True)

    with pytest.raises(RuntimeError, match="qmt_submit_enabled=false"):
        run_smoke(config_path)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"read_only": False}, "read_only=true"),
        ({"allow_place_order": True}, "allow_place_order=false"),
        ({"allow_cancel_order": True}, "allow_cancel_order=false"),
    ],
)
def test_readonly_smoke_rejects_unsafe_local_config(tmp_path, override: dict[str, object], message: str) -> None:
    config_path = tmp_path / "qmt.yaml"
    _write_config(config_path, **override)

    with pytest.raises(RuntimeError, match=message):
        run_smoke(config_path)


def test_readonly_smoke_snapshot_must_be_under_runtime(tmp_path) -> None:
    config_path = tmp_path / "qmt.yaml"
    _write_config(config_path, snapshot_path=str(tmp_path / "snapshot.json"))

    with pytest.raises(RuntimeError, match="runtime"):
        run_smoke(config_path)


def test_readonly_smoke_collects_snapshot_without_order_or_cancel(tmp_path) -> None:
    config_path = tmp_path / "qmt.yaml"
    snapshot_path = Path("runtime/qmt_execution/test_qmt_readonly_snapshot.json")
    _write_config(config_path, snapshot_path=str(snapshot_path))
    adapter = _FakeReadonlyAdapter()

    try:
        output_path = run_smoke(config_path, adapter_factory=lambda config: adapter)
        assert output_path == snapshot_path
        data = yaml.safe_load(snapshot_path.read_text(encoding="utf-8"))
        assert data["account_type"] == "STOCK"
        assert data["account_summary_sample"]["account_id"] == "SIM-ACCOUNT"
        assert data["positions_sample"][0]["code"] == "510300.SH"
        assert data["mapping_result"] == {"positions_mapped": 1, "orders_mapped": 1, "trades_mapped": 1}
        assert data["errors"] == []
        assert adapter.place_order_calls == 0
        assert adapter.cancel_order_calls == 0
        assert adapter.subscribed is True
        assert adapter.disconnected is True
    finally:
        if snapshot_path.exists():
            snapshot_path.unlink()


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


def _write_config(path: Path, **overrides) -> None:
    data = {
        "enabled": True,
        "trading_env": "SIM",
        "qmt_submit_enabled": False,
        "read_only": True,
        "allow_place_order": False,
        "allow_cancel_order": False,
        "snapshot_path": "runtime/qmt_execution/qmt_readonly_snapshot.json",
    }
    data.update(overrides)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def _fail_if_connected():
    raise AssertionError("adapter should not be created when example config is disabled")


class _FakeReadonlyAdapter:
    def __init__(self) -> None:
        self.place_order_calls = 0
        self.cancel_order_calls = 0
        self.subscribed = False
        self.disconnected = False

    def connect(self) -> None:
        return None

    def subscribe_updates(self, callback) -> None:
        self.subscribed = True

    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            account_id="SIM-ACCOUNT",
            cash=1000.0,
            total_asset=1200.0,
            market_value=200.0,
            update_time="2026-05-20T09:30:00",
        )

    def get_positions(self):
        return [
            {
                "stock_code": "510300.SH",
                "stock_name": "CSI 300 ETF",
                "volume": 100,
                "can_use_volume": 100,
                "open_price": 2.0,
                "last_price": 2.0,
            }
        ]

    def get_orders(self):
        return [{"order_id": "O-1", "stock_code": "510300.SH", "order_type": 23, "order_volume": 100}]

    def get_trades(self):
        return [{"trade_id": "T-1", "order_id": "O-1", "stock_code": "510300.SH", "order_type": 23, "traded_volume": 100}]

    def place_order(self, intent) -> None:
        self.place_order_calls += 1

    def cancel_order(self, broker_order_id) -> None:
        self.cancel_order_calls += 1

    def disconnect(self) -> None:
        self.disconnected = True
