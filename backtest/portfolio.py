from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class FeeConfig:
    commission_rate: float = 0.00005
    min_commission: float = 0.1
    stamp_tax_rate: float = 0.0
    slippage_rate: float = 0.00005


@dataclass
class Position:
    shares: float
    avg_cost: float


@dataclass
class Portfolio:
    cash: float
    fee_config: FeeConfig
    positions: dict[str, Position] = field(default_factory=dict)

    def current_symbols(self) -> list[str]:
        return [symbol for symbol, pos in self.positions.items() if pos.shares > 1e-10]

    def market_value(self, prices: pd.Series) -> float:
        value = 0.0
        for symbol, pos in self.positions.items():
            price = prices.get(symbol)
            if pd.notna(price):
                value += pos.shares * float(price)
        return value

    def total_equity(self, prices: pd.Series) -> float:
        return self.cash + self.market_value(prices)

    def format_positions(self, etf_info: dict[str, dict[str, str]]) -> str:
        if not self.current_symbols():
            return "空仓"
        parts = []
        for symbol in self.current_symbols():
            name = etf_info.get(symbol, {}).get("name", symbol)
            shares = self.positions[symbol].shares
            parts.append(f"{symbol}{name}:{shares:.4f}")
        return "; ".join(parts)


def calculate_trade_cost(notional: float, side: str, fee_config: FeeConfig) -> dict[str, float]:
    if notional <= 0:
        return {"commission": 0.0, "stamp_tax": 0.0, "slippage": 0.0, "total_cost": 0.0}

    commission = max(notional * fee_config.commission_rate, fee_config.min_commission)
    stamp_tax = notional * fee_config.stamp_tax_rate if side.lower() == "sell" else 0.0
    slippage = notional * fee_config.slippage_rate
    return {
        "commission": float(commission),
        "stamp_tax": float(stamp_tax),
        "slippage": float(slippage),
        "total_cost": float(commission + stamp_tax + slippage),
    }


def affordable_buy_notional(target_notional: float, available_cash: float, fee_config: FeeConfig) -> float:
    if target_notional <= 0 or available_cash <= fee_config.min_commission:
        return 0.0

    low = 0.0
    high = min(target_notional, available_cash)
    for _ in range(40):
        mid = (low + high) / 2
        cost = calculate_trade_cost(mid, "buy", fee_config)["total_cost"]
        if mid + cost <= available_cash:
            low = mid
        else:
            high = mid
    return low
