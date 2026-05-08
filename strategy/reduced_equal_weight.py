from __future__ import annotations

import pandas as pd

from strategy.equal_weight import EqualWeightMonthlyStrategy


class ReducedEqualWeightMonthlyStrategy(EqualWeightMonthlyStrategy):
    """Equal-weight monthly rebalance on a smaller configured ETF basket."""

    strategy_name = "reduced_equal_weight_monthly"

    def __init__(
        self,
        close: pd.DataFrame,
        etf_info: dict[str, dict[str, str]],
        selected_symbols: list[str] | tuple[str, ...] | None = None,
    ):
        default_symbols = ("510300", "510500", "510880", "518880", "511880")
        super().__init__(close, etf_info, selected_symbols=selected_symbols or default_symbols)
