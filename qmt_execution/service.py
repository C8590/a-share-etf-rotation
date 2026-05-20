from __future__ import annotations

from pathlib import Path
from typing import List

from .broker_adapter import BrokerAdapter
from .contracts import BrokerOrder, IntentStatus, OrderIntent, PositionSnapshot
from .logger import ExecutionLogRecord, ExecutionLogger
from .risk import RiskContext, RiskEngine


class ExecutionService:
    """OrderIntent -> 风控 -> 人工确认 -> broker -> 日志 -> 持仓同步。"""

    def __init__(self, broker: BrokerAdapter, risk_engine: RiskEngine, logger: ExecutionLogger):
        self.broker = broker
        self.risk_engine = risk_engine
        self.logger = logger

    def submit_intent(self, intent: OrderIntent, ctx: RiskContext) -> BrokerOrder | None:
        risk_result = self.risk_engine.check(intent, ctx)
        intent.risk_checked = True

        if not risk_result.passed:
            if "manual_confirmation" in risk_result.failed_codes() and len(risk_result.failed_codes()) == 1:
                intent.status = IntentStatus.WAITING_MANUAL_CONFIRM
                status = intent.status.value
                error = "manual confirmation required"
            else:
                intent.status = IntentStatus.RISK_REJECTED
                status = intent.status.value
                error = ",".join(risk_result.failed_codes())
            self._log(intent, status=status, broker_order_id=None, filled_quantity=0, avg_price=None, error_message=error, risk_check_result=risk_result.to_dict())
            return None

        intent.status = IntentStatus.READY_TO_SUBMIT
        try:
            order = self.broker.place_order(intent)
            if order.status == "FILLED":
                intent.status = IntentStatus.FILLED
            elif order.status == "PARTIAL_FILLED":
                intent.status = IntentStatus.PARTIAL_FILLED
            elif order.status == "FAILED":
                intent.status = IntentStatus.FAILED
            else:
                intent.status = IntentStatus.SUBMITTED
            if order.status != "FAILED":
                ctx.recent_order_keys.add(self.risk_engine.order_key(intent))
            self._log(
                intent,
                status=order.status,
                broker_order_id=order.broker_order_id,
                filled_quantity=order.filled_quantity,
                avg_price=order.avg_price,
                error_message=order.error_message,
                risk_check_result=risk_result.to_dict(),
            )
            return order
        except Exception as exc:  # noqa: BLE001
            intent.status = IntentStatus.FAILED
            self._log(intent, status="FAILED", broker_order_id=None, filled_quantity=0, avg_price=None, error_message=str(exc), risk_check_result=risk_result.to_dict())
            return None

    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        return self.broker.cancel_order(broker_order_id)

    def sync_positions(self) -> List[PositionSnapshot]:
        return self.broker.get_positions()

    def _log(self, intent: OrderIntent, status: str, broker_order_id: str | None, filled_quantity: int, avg_price: float | None, error_message: str | None, risk_check_result: dict) -> None:
        self.logger.append(
            ExecutionLogRecord(
                order_intent_id=intent.id,
                submit_time=ExecutionLogger.now(),
                code=intent.code,
                action=intent.action.value,
                quantity=intent.quantity,
                limit_price=intent.limit_price,
                status=status,
                broker_order_id=broker_order_id,
                filled_quantity=filled_quantity,
                avg_price=avg_price,
                error_message=error_message,
                risk_check_result=risk_check_result,
                manual_confirmed=intent.manual_confirmed,
            )
        )
