from __future__ import annotations

from typing import Any

import pandas as pd


class BaseStrategy:
    strategy_name = "base"

    def generate_rebalance_dates(
        self,
        dates: pd.DatetimeIndex,
        frequency: str = "weekly",
        signal_weekday: int = 4,
    ) -> list[pd.Timestamp]:
        from strategy.etf_rotation import get_rebalance_dates

        return get_rebalance_dates(dates, frequency=frequency, signal_weekday=signal_weekday)

    def calculate_signals(self, signal_date: pd.Timestamp, current_holdings: list[str]) -> dict[str, Any]:
        return self.generate_target(signal_date, current_holdings)

    def generate_target_positions(
        self,
        signal_date: pd.Timestamp,
        execute_date: pd.Timestamp | None,
        current_holdings: list[str],
        strategy_name: str | None = None,
    ) -> dict[str, Any]:
        signal = self.calculate_signals(signal_date, current_holdings)
        target = list(signal.get("target", []))
        weights = {symbol: (1.0 / len(target) if target else 0.0) for symbol in target}
        buy_list = [symbol for symbol in target if symbol not in current_holdings]
        sell_list = [symbol for symbol in current_holdings if symbol not in target]
        hold_list = [symbol for symbol in current_holdings if symbol in target]
        return {
            "signal_date": str(pd.Timestamp(signal_date).date()),
            "execute_date": str(pd.Timestamp(execute_date).date()) if execute_date is not None else "",
            "strategy_name": strategy_name or self.strategy_name,
            "target_positions": target,
            "target_weights": weights,
            "buy_list": buy_list,
            "sell_list": sell_list,
            "hold_list": hold_list,
            "reason": self.explain_decision(signal),
        }

    def explain_decision(self, signal: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ["buy_reasons", "sell_reasons", "keep_reasons"]:
            values = signal.get(key, {})
            if isinstance(values, dict):
                parts.extend(str(value) for value in values.values())
        if not parts and not signal.get("target"):
            return "No target positions passed the configured filters."
        return " | ".join(parts[:8])
