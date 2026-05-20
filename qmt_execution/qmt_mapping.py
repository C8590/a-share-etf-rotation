from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from .contracts import Action, PositionSnapshot


@dataclass
class OrderUpdate:
    broker_order_id: str
    code: str
    action: Action
    quantity: int
    limit_price: Optional[float]
    status: str
    filled_quantity: int = 0
    avg_price: Optional[float] = None
    update_time: str = ""
    error_message: Optional[str] = None
    raw_status: Optional[str] = None


@dataclass
class TradeUpdate:
    broker_trade_id: str
    broker_order_id: str
    code: str
    action: Action
    quantity: int
    price: float
    trade_time: str


def map_qmt_position(raw: Any, default_update_time: str | None = None) -> PositionSnapshot:
    quantity = int(_first(raw, "quantity", "volume", "totalAmt", default=0) or 0)
    available = int(_first(raw, "available_quantity", "can_use_volume", "enableAmount", default=0) or 0)
    cost_price = float(_first(raw, "cost_price", "open_price", default=0.0) or 0.0)
    last_price = float(_first(raw, "last_price", "lastPrice", default=0.0) or 0.0)
    market_value = float(_first(raw, "market_value", "marketValue", default=quantity * last_price) or 0.0)
    pnl = float(_first(raw, "pnl", "profit", default=market_value - quantity * cost_price) or 0.0)
    base_cost = quantity * cost_price
    pnl_ratio = float(_first(raw, "pnl_ratio", "profit_ratio", default=(pnl / base_cost if base_cost else 0.0)) or 0.0)

    return PositionSnapshot(
        code=str(_first(raw, "code", "stock_code", "m_strInstrumentID", default="")),
        name=str(_first(raw, "name", "stock_name", "m_strInstrumentName", default="")),
        quantity=quantity,
        available_quantity=available,
        cost_price=cost_price,
        last_price=last_price,
        market_value=market_value,
        pnl=pnl,
        pnl_ratio=pnl_ratio,
        update_time=str(_first(raw, "update_time", "mtime", default=default_update_time or _now())),
    )


def map_qmt_order_update(raw: Any, default_update_time: str | None = None) -> OrderUpdate:
    action = _map_action(_first(raw, "action", "order_type", "entrust_bs", "direction", default=Action.BUY.value))
    return OrderUpdate(
        broker_order_id=str(_first(raw, "broker_order_id", "order_id", "order_sysid", "entrust_no", default="")),
        code=str(_first(raw, "code", "stock_code", "m_strInstrumentID", default="")),
        action=action,
        quantity=int(_first(raw, "quantity", "order_volume", "entrust_amount", default=0) or 0),
        limit_price=_optional_float(_first(raw, "limit_price", "price", "entrust_price", default=None)),
        status=str(_first(raw, "status", "order_status", "entrust_status", default="UNKNOWN")),
        filled_quantity=int(_first(raw, "filled_quantity", "traded_volume", "business_amount", default=0) or 0),
        avg_price=_optional_float(_first(raw, "avg_price", "traded_price", "business_price", default=None)),
        update_time=str(_first(raw, "update_time", "mtime", default=default_update_time or _now())),
        error_message=_optional_str(_first(raw, "error_message", "status_msg", "error_msg", default=None)),
        raw_status=_optional_str(_first(raw, "order_status", "entrust_status", default=None)),
    )


def map_qmt_trade_update(raw: Any, default_trade_time: str | None = None) -> TradeUpdate:
    action = _map_action(_first(raw, "action", "order_type", "entrust_bs", "direction", default=Action.BUY.value))
    return TradeUpdate(
        broker_trade_id=str(_first(raw, "broker_trade_id", "trade_id", "business_id", "deal_no", default="")),
        broker_order_id=str(_first(raw, "broker_order_id", "order_id", "order_sysid", "entrust_no", default="")),
        code=str(_first(raw, "code", "stock_code", "m_strInstrumentID", default="")),
        action=action,
        quantity=int(_first(raw, "quantity", "traded_volume", "business_amount", default=0) or 0),
        price=float(_first(raw, "price", "traded_price", "business_price", default=0.0) or 0.0),
        trade_time=str(_first(raw, "trade_time", "business_time", "mtime", default=default_trade_time or _now())),
    )


def _first(raw: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(raw, dict) and name in raw:
            return raw[name]
        if hasattr(raw, name):
            return getattr(raw, name)
    return default


def _map_action(value: Any) -> Action:
    if isinstance(value, Action):
        return value
    text = str(value).upper()
    if text in {"SELL", "S", "24", "48", "2"}:
        return Action.SELL
    return Action.BUY


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
