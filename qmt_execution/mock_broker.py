from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from .broker_adapter import BrokerAdapter
from .contracts import AccountSnapshot, Action, BrokerOrder, BrokerTrade, OrderIntent, PositionSnapshot


class MockBroker(BrokerAdapter):
    """用于第一阶段验收的模拟 broker。

    行为：通过风控和人工确认后的订单会立即全部成交，便于联调 OrderIntent -> 风控 -> 下单 -> 回报 -> 持仓同步。
    """

    def __init__(self, account_id: str = "MOCK-001", cash: float = 1_000_000.0, quotes: Optional[Dict[str, float]] = None):
        self.account_id = account_id
        self.cash = cash
        self.quotes = quotes or {}
        self.positions: Dict[str, PositionSnapshot] = {}
        self.orders: Dict[str, BrokerOrder] = {}
        self.trades: List[BrokerTrade] = []
        self.callbacks: List[Callable[[str, object], None]] = []
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe_updates(self, callback: Callable[[str, object], None]) -> None:
        self.callbacks.append(callback)

    def _emit(self, event_type: str, payload: object) -> None:
        for cb in self.callbacks:
            cb(event_type, payload)

    def get_account(self) -> AccountSnapshot:
        market_value = sum(p.market_value for p in self.positions.values())
        return AccountSnapshot(
            account_id=self.account_id,
            cash=round(self.cash, 2),
            total_asset=round(self.cash + market_value, 2),
            market_value=round(market_value, 2),
            update_time=datetime.now().isoformat(timespec="seconds"),
        )

    def get_positions(self) -> List[PositionSnapshot]:
        self._mark_to_market()
        return list(self.positions.values())

    def get_orders(self) -> List[BrokerOrder]:
        return list(self.orders.values())

    def get_trades(self) -> List[BrokerTrade]:
        return list(self.trades)

    def place_order(self, intent: OrderIntent) -> BrokerOrder:
        if not self.connected:
            raise RuntimeError("MockBroker is not connected")

        price = intent.limit_price or self.quotes.get(intent.code)
        if not price or price <= 0:
            order = self._new_order(intent, "FAILED", 0, None, "missing valid price")
            self._emit("order", order)
            return order

        cost = price * intent.quantity
        if intent.action == Action.BUY and cost > self.cash:
            order = self._new_order(intent, "FAILED", 0, None, "insufficient cash")
            self._emit("order", order)
            return order

        if intent.action == Action.SELL:
            pos = self.positions.get(intent.code)
            if pos is None or pos.available_quantity < intent.quantity:
                order = self._new_order(intent, "FAILED", 0, None, "insufficient position")
                self._emit("order", order)
                return order

        broker_order_id = f"MOCK-O-{uuid4().hex[:10]}"
        order = BrokerOrder(
            broker_order_id=broker_order_id,
            code=intent.code,
            action=intent.action,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            status="FILLED",
            filled_quantity=intent.quantity,
            avg_price=price,
        )
        self.orders[broker_order_id] = order
        self._apply_fill(intent, broker_order_id, price)
        self._emit("order", order)
        return order

    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        order = self.orders.get(broker_order_id)
        if order is None:
            return BrokerOrder(broker_order_id=broker_order_id, code="", action=Action.BUY, quantity=0, limit_price=None, status="FAILED", error_message="order not found")
        if order.status == "FILLED":
            return BrokerOrder(**{**order.__dict__, "status": "CANCEL_REJECTED", "error_message": "already filled"})
        order.status = "CANCELED"
        self._emit("order", order)
        return order

    def _new_order(self, intent: OrderIntent, status: str, filled_quantity: int, avg_price: Optional[float], error: Optional[str]) -> BrokerOrder:
        broker_order_id = f"MOCK-O-{uuid4().hex[:10]}"
        order = BrokerOrder(
            broker_order_id=broker_order_id,
            code=intent.code,
            action=intent.action,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            status=status,
            filled_quantity=filled_quantity,
            avg_price=avg_price,
            error_message=error,
        )
        self.orders[broker_order_id] = order
        return order

    def _apply_fill(self, intent: OrderIntent, broker_order_id: str, price: float) -> None:
        trade = BrokerTrade(
            broker_trade_id=f"MOCK-T-{uuid4().hex[:10]}",
            broker_order_id=broker_order_id,
            code=intent.code,
            action=intent.action,
            quantity=intent.quantity,
            price=price,
            trade_time=datetime.now().isoformat(timespec="seconds"),
        )
        self.trades.append(trade)
        self._emit("trade", trade)

        old = self.positions.get(intent.code)
        if intent.action == Action.BUY:
            self.cash -= price * intent.quantity
            if old is None:
                qty = intent.quantity
                cost_price = price
            else:
                qty = old.quantity + intent.quantity
                cost_price = ((old.cost_price * old.quantity) + price * intent.quantity) / qty
            self.positions[intent.code] = self._position(intent.code, intent.name, qty, qty, cost_price, price)
        else:
            assert old is not None
            self.cash += price * intent.quantity
            qty = old.quantity - intent.quantity
            if qty <= 0:
                self.positions.pop(intent.code, None)
            else:
                self.positions[intent.code] = self._position(intent.code, old.name, qty, qty, old.cost_price, price)

    def _position(self, code: str, name: str, quantity: int, available: int, cost_price: float, last_price: float) -> PositionSnapshot:
        mv = quantity * last_price
        pnl = quantity * (last_price - cost_price)
        pnl_ratio = (last_price / cost_price - 1) if cost_price else 0.0
        return PositionSnapshot(
            code=code,
            name=name,
            quantity=quantity,
            available_quantity=available,
            cost_price=round(cost_price, 4),
            last_price=round(last_price, 4),
            market_value=round(mv, 2),
            pnl=round(pnl, 2),
            pnl_ratio=round(pnl_ratio, 6),
            update_time=datetime.now().isoformat(timespec="seconds"),
        )

    def _mark_to_market(self) -> None:
        for code, pos in list(self.positions.items()):
            last = self.quotes.get(code, pos.last_price)
            self.positions[code] = self._position(code, pos.name, pos.quantity, pos.available_quantity, pos.cost_price, last)
