from __future__ import annotations

from typing import Callable, List, Optional

from .broker_adapter import BrokerAdapter
from .contracts import AccountSnapshot, Action, BrokerOrder, BrokerTrade, OrderIntent, PositionSnapshot, PriceType
from .qmt_mapping import map_qmt_order_update, map_qmt_position, map_qmt_trade_update


class QmtSafetyError(RuntimeError):
    pass


class QmtAdapter(BrokerAdapter):
    """QMT / miniQMT / XtQuant 适配器骨架。

    第一阶段安全约束：
    - 默认 qmt_submit_enabled=False，只允许生成草稿和查询回读。
    - trading_env='LIVE' 时，place_order 永远拒绝自动下单。
    - 只有 trading_env='SIM' 且 qmt_submit_enabled=True 且 OrderIntent.manual_confirmed=True 时，才允许调用 xt_trader.order_stock。

    真实环境依赖：Windows + MiniQMT/XtMiniQmt 客户端 + xtquant + 券商开通权限。
    """

    def __init__(
        self,
        userdata_mini_path: str,
        account_id: str,
        session_id: int,
        trading_env: str = "SIM",  # SIM / LIVE
        qmt_submit_enabled: bool = False,
        read_only: bool = True,
        allow_place_order: bool = False,
        allow_cancel_order: bool = False,
    ):
        self.userdata_mini_path = userdata_mini_path
        self.account_id = account_id
        self.session_id = session_id
        self.trading_env = trading_env.upper()
        self.qmt_submit_enabled = qmt_submit_enabled
        self.read_only = read_only
        self.allow_place_order = allow_place_order
        self.allow_cancel_order = allow_cancel_order
        self.xt_trader = None
        self.acc = None
        self.callbacks: List[Callable[[str, object], None]] = []

    def connect(self) -> None:
        try:
            from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback  # type: ignore
            from xtquant.xttype import StockAccount  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("xtquant 未安装或当前环境不支持；请在 Windows + MiniQMT 环境中运行") from exc

        adapter = self

        class Callback(XtQuantTraderCallback):  # type: ignore[misc]
            def on_stock_order(self, order):
                adapter._emit("order", order)

            def on_stock_trade(self, trade):
                adapter._emit("trade", trade)

            def on_order_error(self, order_error):
                adapter._emit("order_error", order_error)

            def on_cancel_error(self, cancel_error):
                adapter._emit("cancel_error", cancel_error)

            def on_disconnected(self):
                adapter._emit("disconnected", None)

        self.xt_trader = XtQuantTrader(self.userdata_mini_path, self.session_id)
        self.acc = StockAccount(self.account_id)
        self.xt_trader.register_callback(Callback())
        self.xt_trader.start()
        result = self.xt_trader.connect()
        if result != 0:
            raise RuntimeError(f"QMT connect failed: {result}")
        sub = self.xt_trader.subscribe(self.acc)
        if sub != 0:
            raise RuntimeError(f"QMT subscribe failed: {sub}")

    def disconnect(self) -> None:
        if self.xt_trader is not None:
            self.xt_trader.stop()

    def subscribe_updates(self, callback: Callable[[str, object], None]) -> None:
        self.callbacks.append(callback)

    def _emit(self, event_type: str, payload: object) -> None:
        for cb in self.callbacks:
            cb(event_type, payload)

    def get_account(self) -> AccountSnapshot:
        self._ensure_connected()
        asset = self.xt_trader.query_stock_asset(self.acc)
        if asset is None:
            raise RuntimeError("query_stock_asset returned None")
        return AccountSnapshot(
            account_id=self.account_id,
            cash=float(getattr(asset, "cash", 0.0)),
            total_asset=float(getattr(asset, "total_asset", getattr(asset, "asset_balance", 0.0))),
            market_value=float(getattr(asset, "market_value", 0.0)),
            update_time="",
        )

    def get_positions(self) -> List[PositionSnapshot]:
        self._ensure_connected()
        raw_positions = self.xt_trader.query_stock_positions(self.acc) or []
        return [map_qmt_position(p) for p in raw_positions]

    def get_orders(self) -> List[BrokerOrder]:
        self._ensure_connected()
        raw_orders = self.xt_trader.query_stock_orders(self.acc, False) or []
        return [self._order_update_to_broker_order(map_qmt_order_update(o)) for o in raw_orders]

    def get_trades(self) -> List[BrokerTrade]:
        self._ensure_connected()
        query = getattr(self.xt_trader, "query_stock_trades", None)
        raw_trades = query(self.acc) if query is not None else []
        return [self._trade_update_to_broker_trade(map_qmt_trade_update(t)) for t in (raw_trades or [])]

    def place_order(self, intent: OrderIntent) -> BrokerOrder:
        if self.trading_env == "LIVE":
            raise QmtSafetyError("第一阶段禁止实盘自动下单：LIVE 环境 place_order 被强制拦截")
        if not self.qmt_submit_enabled:
            raise QmtSafetyError("qmt_submit_enabled=False：第一阶段默认只允许订单草稿/半自动，不直连提交")
        if self.read_only:
            raise QmtSafetyError("read_only=True：只读阶段禁止提交 QMT 下单")
        if not self.allow_place_order:
            raise QmtSafetyError("allow_place_order=False：当前配置禁止提交 QMT 下单")
        if not intent.manual_confirmed:
            raise QmtSafetyError("缺少人工确认，禁止提交 QMT")
        self._ensure_connected()

        from xtquant import xtconstant  # type: ignore

        order_type = xtconstant.STOCK_BUY if intent.action == Action.BUY else xtconstant.STOCK_SELL
        price_type = xtconstant.FIX_PRICE if intent.price_type == PriceType.LIMIT else xtconstant.LATEST_PRICE
        price = float(intent.limit_price or 0.0)
        order_id = self.xt_trader.order_stock(
            self.acc,
            intent.code,
            order_type,
            int(intent.quantity),
            price_type,
            price,
            "aetfv2_qmt_execution",
            intent.id,
        )
        if int(order_id) <= 0:
            return BrokerOrder(str(order_id), intent.code, intent.action, intent.quantity, intent.limit_price, "FAILED", error_message="QMT order_stock returned non-positive order_id")
        return BrokerOrder(str(order_id), intent.code, intent.action, intent.quantity, intent.limit_price, "SUBMITTED")

    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        if self.trading_env == "LIVE":
            raise QmtSafetyError("第一阶段禁止实盘自动撤单：LIVE 环境 cancel_order 被强制拦截")
        if self.read_only:
            raise QmtSafetyError("read_only=True：只读阶段禁止提交 QMT 撤单")
        if not self.allow_cancel_order:
            raise QmtSafetyError("allow_cancel_order=False：当前配置禁止提交 QMT 撤单")
        self._ensure_connected()
        result = self.xt_trader.cancel_order_stock(self.acc, int(broker_order_id))
        status = "CANCEL_SUBMITTED" if result == 0 else "FAILED"
        return BrokerOrder(broker_order_id, "", Action.BUY, 0, None, status, error_message=None if result == 0 else "QMT cancel failed")

    def _ensure_connected(self) -> None:
        if self.xt_trader is None or self.acc is None:
            raise RuntimeError("QmtAdapter is not connected")

    @staticmethod
    def _order_update_to_broker_order(update) -> BrokerOrder:
        return BrokerOrder(
            broker_order_id=update.broker_order_id,
            code=update.code,
            action=update.action,
            quantity=update.quantity,
            limit_price=update.limit_price,
            status=update.status,
            filled_quantity=update.filled_quantity,
            avg_price=update.avg_price,
            error_message=update.error_message,
        )

    @staticmethod
    def _trade_update_to_broker_trade(update) -> BrokerTrade:
        return BrokerTrade(
            broker_trade_id=update.broker_trade_id,
            broker_order_id=update.broker_order_id,
            code=update.code,
            action=update.action,
            quantity=update.quantity,
            price=update.price,
            trade_time=update.trade_time,
        )
