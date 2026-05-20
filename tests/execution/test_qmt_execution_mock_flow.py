from __future__ import annotations

import json

import pytest

from qmt_execution.contracts import Action, IntentStatus, OrderIntent, PriceType
from qmt_execution.logger import ExecutionLogger
from qmt_execution.mock_broker import MockBroker
from qmt_execution.qmt_adapter import QmtAdapter, QmtSafetyError
from qmt_execution.risk import RiskContext, RiskEngine
from qmt_execution.service import ExecutionService


class CountingMockBroker(MockBroker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.place_order_calls = 0

    def place_order(self, intent: OrderIntent):
        self.place_order_calls += 1
        return super().place_order(intent)


def make_intent(**overrides) -> OrderIntent:
    data = {
        "trade_date": "2026-05-20",
        "action": Action.BUY,
        "code": "510300.SH",
        "name": "CSI 300 ETF",
        "target_weight": 0.10,
        "target_amount": 100_000.0,
        "quantity": 24_600,
        "price_type": PriceType.LIMIT,
        "limit_price": 4.05,
        "reason": "mock execution acceptance",
        "source_signal": "aetfv2.entry.rank_momentum_acceleration.v1",
        "risk_level": "R1",
        "manual_confirmed": True,
    }
    data.update(overrides)
    return OrderIntent(**data)


def make_ctx(**overrides) -> RiskContext:
    data = {
        "account_total_asset": 1_000_000.0,
        "risk_freeze_level": None,
        "p0_manual_takeover": False,
        "equity_position_limit": 0.80,
        "single_etf_position_limit": 0.20,
        "sector_exposure_limit": 0.35,
        "current_equity_weight": 0.0,
        "current_single_weight_by_code": {},
        "current_sector_weight_by_sector": {"broad": 0.0},
        "code_to_sector": {"510300.SH": "broad"},
        "last_price_by_code": {"510300.SH": 4.05},
        "avg_daily_turnover_by_code": {"510300.SH": 1_000_000_000.0},
        "trading_time_valid": True,
        "positions_synced": True,
        "manual_confirm_required": True,
    }
    data.update(overrides)
    return RiskContext(**data)


def make_service(tmp_path):
    broker = CountingMockBroker(cash=1_000_000.0, quotes={"510300.SH": 4.05})
    broker.connect()
    log_path = tmp_path / "execution_log.jsonl"
    service = ExecutionService(broker=broker, risk_engine=RiskEngine(), logger=ExecutionLogger(log_path))
    return service, broker, log_path


def read_logs(log_path):
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_order_intent_defaults_to_manual_confirm_draft() -> None:
    intent = make_intent(manual_confirmed=False)

    assert intent.requires_manual_confirm is True
    assert intent.manual_confirmed is False
    assert intent.status == IntentStatus.DRAFT
    assert intent.to_dict()["status"] == "DRAFT"


def test_risk_pass_then_mock_broker_submits_and_logs(tmp_path) -> None:
    service, broker, log_path = make_service(tmp_path)

    order = service.submit_intent(make_intent(), make_ctx())

    assert order is not None
    assert order.status == "FILLED"
    assert broker.place_order_calls == 1
    assert read_logs(log_path)[0]["status"] == "FILLED"


def test_manual_confirmation_required_blocks_broker_submit(tmp_path) -> None:
    service, broker, log_path = make_service(tmp_path)
    intent = make_intent(manual_confirmed=False)

    order = service.submit_intent(intent, make_ctx())

    assert order is None
    assert intent.status == IntentStatus.WAITING_MANUAL_CONFIRM
    assert broker.place_order_calls == 0
    assert read_logs(log_path)[0]["error_message"] == "manual confirmation required"


@pytest.mark.parametrize("freeze_level", ["R3", "R4"])
def test_r3_r4_freeze_blocks_broker_submit(tmp_path, freeze_level: str) -> None:
    service, broker, log_path = make_service(tmp_path)
    intent = make_intent(risk_level=freeze_level)

    order = service.submit_intent(intent, make_ctx(risk_freeze_level=freeze_level))

    assert order is None
    assert intent.status == IntentStatus.RISK_REJECTED
    assert broker.place_order_calls == 0
    assert "risk_freeze_R3_R4" in read_logs(log_path)[0]["risk_check_result"]["failed_codes"]


def test_p0_manual_takeover_blocks_broker_submit(tmp_path) -> None:
    service, broker, log_path = make_service(tmp_path)
    intent = make_intent()

    order = service.submit_intent(intent, make_ctx(p0_manual_takeover=True))

    assert order is None
    assert intent.status == IntentStatus.RISK_REJECTED
    assert broker.place_order_calls == 0
    assert "p0_manual_takeover" in read_logs(log_path)[0]["risk_check_result"]["failed_codes"]


def test_duplicate_order_is_rejected_before_second_broker_submit(tmp_path) -> None:
    service, broker, log_path = make_service(tmp_path)
    ctx = make_ctx()

    first = service.submit_intent(make_intent(), ctx)
    second_intent = make_intent()
    second = service.submit_intent(second_intent, ctx)

    assert first is not None
    assert second is None
    assert second_intent.status == IntentStatus.RISK_REJECTED
    assert broker.place_order_calls == 1
    assert "duplicate_order" in read_logs(log_path)[1]["risk_check_result"]["failed_codes"]


def test_fill_report_updates_positions(tmp_path) -> None:
    service, _, _ = make_service(tmp_path)

    service.submit_intent(make_intent(), make_ctx())
    positions = service.sync_positions()

    assert len(positions) == 1
    assert positions[0].code == "510300.SH"
    assert positions[0].quantity == 24_600
    assert positions[0].market_value == pytest.approx(99_630.0)


def test_qmt_adapter_live_and_unconfirmed_paths_reject_before_qmt_submit() -> None:
    intent = make_intent()
    live = QmtAdapter("unused", "acct", 1, trading_env="LIVE", qmt_submit_enabled=True)
    disabled = QmtAdapter("unused", "acct", 1)
    unconfirmed = QmtAdapter("unused", "acct", 1, trading_env="SIM", qmt_submit_enabled=True)

    with pytest.raises(QmtSafetyError, match="LIVE"):
        live.place_order(intent)
    with pytest.raises(QmtSafetyError, match="qmt_submit_enabled=False"):
        disabled.place_order(intent)
    with pytest.raises(QmtSafetyError, match="人工确认"):
        unconfirmed.place_order(make_intent(manual_confirmed=False, requires_manual_confirm=False))
