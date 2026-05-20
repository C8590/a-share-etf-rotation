from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PriceType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    BEST_5 = "BEST_5"


class IntentStatus(str, Enum):
    DRAFT = "DRAFT"
    RISK_REJECTED = "RISK_REJECTED"
    WAITING_MANUAL_CONFIRM = "WAITING_MANUAL_CONFIRM"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    FAILED = "FAILED"


@dataclass
class OrderIntent:
    """统一交易意图合同。

    entry / exit 只允许创建这个对象，不允许直接触达 broker / QMT。
    第一阶段默认 requires_manual_confirm=True。
    """

    trade_date: str
    action: Action
    code: str
    name: str
    target_weight: float
    target_amount: float
    quantity: int
    price_type: PriceType
    limit_price: Optional[float]
    reason: str
    source_signal: str
    risk_level: str
    risk_checked: bool = False
    requires_manual_confirm: bool = True
    status: IntentStatus = IntentStatus.DRAFT
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    manual_confirmed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        data["price_type"] = self.price_type.value
        data["status"] = self.status.value
        return data


@dataclass
class AccountSnapshot:
    account_id: str
    cash: float
    total_asset: float
    market_value: float
    update_time: str


@dataclass
class PositionSnapshot:
    code: str
    name: str
    quantity: int
    available_quantity: int
    cost_price: float
    last_price: float
    market_value: float
    pnl: float
    pnl_ratio: float
    update_time: str


@dataclass
class BrokerOrder:
    broker_order_id: str
    code: str
    action: Action
    quantity: int
    limit_price: Optional[float]
    status: str
    filled_quantity: int = 0
    avg_price: Optional[float] = None
    error_message: Optional[str] = None


@dataclass
class BrokerTrade:
    broker_trade_id: str
    broker_order_id: str
    code: str
    action: Action
    quantity: int
    price: float
    trade_time: str
