from __future__ import annotations

import numpy as np
import pandas as pd


def make_price_data(days=90, codes=("A", "B", "C", "D")):
    dates = pd.bdate_range("2024-09-02", periods=days)
    rows = []
    sectors = {"A": "科技", "B": "科技", "C": "消费", "D": "金融"}
    for ci, code in enumerate(codes):
        base = 1.0 + ci * 0.1
        for i, d in enumerate(dates):
            drift = 0.002 * i if code in {"A", "C"} else -0.0005 * i
            wave = 0.02 * np.sin(i / 5 + ci)
            close = base * (1 + drift + wave)
            rows.append(
                {
                    "date": d,
                    "code": code,
                    "name": f"ETF{code}",
                    "sector": sectors[code],
                    "sector_l1": sectors[code],
                    "open": close * 0.99,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "close": close,
                    "volume": 1000000,
                    "amount": 10000000,
                }
            )
    return pd.DataFrame(rows)
