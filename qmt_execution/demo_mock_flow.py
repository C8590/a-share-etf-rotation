from __future__ import annotations

import json
from pathlib import Path

from .contracts import Action, OrderIntent, PriceType
from .logger import ExecutionLogger
from .mock_broker import MockBroker
from .risk import RiskContext, RiskEngine
from .service import ExecutionService


def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "runtime" / "qmt_execution"
    log_path = out_dir / "execution_log.jsonl"
    if log_path.exists():
        log_path.unlink()

    broker = MockBroker(cash=1_000_000, quotes={"510300.SH": 4.05})
    broker.connect()
    broker.subscribe_updates(lambda event_type, payload: print(f"UPDATE {event_type}: {payload}"))

    service = ExecutionService(broker=broker, risk_engine=RiskEngine(), logger=ExecutionLogger(log_path))

    ctx = RiskContext(
        account_total_asset=1_000_000,
        risk_freeze_level=None,
        p0_manual_takeover=False,
        equity_position_limit=0.80,
        single_etf_position_limit=0.20,
        sector_exposure_limit=0.35,
        current_equity_weight=0.0,
        current_single_weight_by_code={},
        current_sector_weight_by_sector={"宽基": 0.0},
        code_to_sector={"510300.SH": "宽基"},
        last_price_by_code={"510300.SH": 4.05},
        avg_daily_turnover_by_code={"510300.SH": 1_000_000_000},
        trading_time_valid=True,
        positions_synced=True,
        manual_confirm_required=True,
    )

    intent = OrderIntent(
        trade_date="2026-05-20",
        action=Action.BUY,
        code="510300.SH",
        name="沪深300ETF",
        target_weight=0.10,
        target_amount=100_000,
        quantity=24_600,
        price_type=PriceType.LIMIT,
        limit_price=4.05,
        reason="模拟：右侧趋势确认，综合分第一",
        source_signal="aetfv2.entry.rank_momentum_acceleration.v1",
        risk_level="R1",
        manual_confirmed=True,
    )

    order = service.submit_intent(intent, ctx)
    positions = service.sync_positions()
    print("ORDER", order)
    print("POSITIONS", [p.__dict__ for p in positions])
    print("ACCOUNT", broker.get_account().__dict__)
    print("LOG_PATH", str(log_path))
    print("LOG_LINES")
    print(log_path.read_text(encoding="utf-8"))

    # 冻结场景：R3/R4 应当拦截，不触达 broker。
    frozen = OrderIntent(
        trade_date="2026-05-20",
        action=Action.BUY,
        code="510300.SH",
        name="沪深300ETF",
        target_weight=0.10,
        target_amount=100_000,
        quantity=24_600,
        price_type=PriceType.LIMIT,
        limit_price=4.05,
        reason="模拟：R3冻结下不允许下单",
        source_signal="aetfv2.entry.rank_momentum_acceleration.v1",
        risk_level="R3",
        manual_confirmed=True,
    )
    frozen_ctx = RiskContext(**{**ctx.__dict__, "risk_freeze_level": "R3"})
    service.submit_intent(frozen, frozen_ctx)
    print("FROZEN_STATUS", frozen.status.value)


if __name__ == "__main__":
    main()
