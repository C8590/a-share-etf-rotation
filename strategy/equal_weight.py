from __future__ import annotations

import pandas as pd

from strategy.base import BaseStrategy


class EqualWeightMonthlyStrategy(BaseStrategy):
    """Hold every ETF with valid data and rebalance to equal weights monthly."""

    strategy_name = "equal_weight_monthly"

    def __init__(
        self,
        close: pd.DataFrame,
        etf_info: dict[str, dict[str, str]],
        selected_symbols: list[str] | tuple[str, ...] | None = None,
    ):
        self.close = close.sort_index()
        self.etf_info = etf_info
        self.selected_symbols = tuple(str(symbol).zfill(6) for symbol in (selected_symbols or []))

    def get_rank_table(self, signal_date: pd.Timestamp) -> pd.DataFrame:
        columns = list(self.close.columns)
        if self.selected_symbols:
            selected = set(self.selected_symbols)
            columns = [symbol for symbol in columns if symbol in selected]
        valid_symbols = [symbol for symbol in columns if pd.notna(self.close.loc[signal_date, symbol])]
        snapshot = pd.DataFrame(
            {
                "symbol": valid_symbols,
                "name": [self.etf_info.get(symbol, {}).get("name", symbol) for symbol in valid_symbols],
                "close": [self.close.loc[signal_date, symbol] for symbol in valid_symbols],
                "momentum": [float("nan")] * len(valid_symbols),
                "ma": [float("nan")] * len(valid_symbols),
                "above_ma": [True] * len(valid_symbols),
            }
        )
        snapshot["rank"] = range(1, len(snapshot) + 1)
        return snapshot

    def generate_target(self, signal_date: pd.Timestamp, current_holdings: list[str]) -> dict[str, object]:
        ranks = self.get_rank_table(signal_date)
        target = ranks["symbol"].tolist()
        buy_reasons = {
            symbol: "Equal-weight monthly strategy target includes every tradable ETF in the configured pool"
            for symbol in target
            if symbol not in current_holdings
        }
        sell_reasons = {
            symbol: "ETF is no longer tradable on signal date, so it is removed from equal-weight target"
            for symbol in current_holdings
            if symbol not in target
        }
        keep_reasons = {
            symbol: "ETF remains in the equal-weight monthly target"
            for symbol in current_holdings
            if symbol in target
        }
        return {
            "signal_date": signal_date,
            "target": target,
            "ranks": ranks,
            "eligible": ranks,
            "buy_reasons": buy_reasons,
            "sell_reasons": sell_reasons,
            "keep_reasons": keep_reasons,
            "market_filter_passed": True,
        }
