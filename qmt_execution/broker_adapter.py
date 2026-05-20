from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterable, List

from .contracts import AccountSnapshot, BrokerOrder, BrokerTrade, OrderIntent, PositionSnapshot


class BrokerAdapter(ABC):
    """交易执行适配器统一接口。"""

    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> List[PositionSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_orders(self) -> List[BrokerOrder]:
        raise NotImplementedError

    @abstractmethod
    def get_trades(self) -> List[BrokerTrade]:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, intent: OrderIntent) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    def subscribe_updates(self, callback: Callable[[str, object], None]) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError
