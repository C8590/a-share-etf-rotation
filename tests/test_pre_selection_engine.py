from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from contracts.signal_schema import PRE_SELECTION_RESULT_FIELDS
from signal.pre_selection import OUTPUT_FILE, PreSelectionConfig, PreSelectionEngine


def _frame(
    periods: int = 130,
    start: float = 100.0,
    drift: float = 0.001,
    amount: float = 5_000_000.0,
    abnormal_last: bool = False,
) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=periods)
    day = np.arange(periods, dtype=float)
    close = start * np.power(1.0 + drift, day)
    if abnormal_last:
        close[-1] *= 1.25
    open_ = close * 0.995
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(periods, 1_000_000.0),
            "amount": np.full(periods, amount),
        }
    )


def _pool() -> list[dict[str, str]]:
    return [
        {"symbol": "510300", "name": "沪深300ETF", "sector": "沪深300"},
        {"symbol": "512480", "name": "半导体ETF", "sector": "半导体"},
        {"symbol": "159995", "name": "芯片ETF", "sector": "半导体"},
        {"symbol": "159928", "name": "消费ETF", "sector": "消费"},
        {"symbol": "512000", "name": "证券ETF", "sector": "证券"},
        {"symbol": "512800", "name": "银行ETF", "sector": "银行"},
        {"symbol": "512200", "name": "地产ETF", "sector": "地产"},
    ]


def _market_data() -> dict[str, pd.DataFrame]:
    return {
        "510300": _frame(drift=0.0012),
        "512480": _frame(drift=0.0030),
        "159995": _frame(drift=0.0025),
        "159928": _frame(drift=0.0005),
        "512000": _frame(periods=50, drift=0.0020),
        "512800": _frame(drift=0.0020, amount=100_000.0),
        "512200": _frame(drift=0.0010, abnormal_last=True),
    }


class PreSelectionEngineTest(unittest.TestCase):
    def test_run_writes_candidate_rows_and_contract_fields(self) -> None:
        config = PreSelectionConfig(min_trading_days=80, min_avg_amount=1_000_000.0, max_candidates=2)
        engine = PreSelectionEngine(etf_pool=_pool(), market_data=_market_data(), config=config)

        with tempfile.TemporaryDirectory() as tmp:
            rows = engine.run(output_dir=tmp)
            output = Path(tmp) / OUTPUT_FILE
            self.assertTrue(output.exists())
            saved = pd.read_csv(output, dtype={"symbol": str})

        self.assertEqual(list(saved.columns), list(PRE_SELECTION_RESULT_FIELDS))
        self.assertEqual(set(rows[0].keys()), set(PRE_SELECTION_RESULT_FIELDS))
        self.assertTrue(all(str(row["reason"]).strip() for row in rows))
        self.assertEqual({row["market_state"] for row in rows}, {"进攻"})
        self.assertLessEqual(sum(bool(row["selected"]) for row in rows), 2)
        self.assertNotIn("buy_action", rows[0])
        self.assertTrue(any(row["selected"] and "入选" in row["reason"] for row in rows))

    def test_filter_reasons_are_chinese_and_specific(self) -> None:
        config = PreSelectionConfig(min_trading_days=80, min_avg_amount=1_000_000.0, max_candidates=2)
        with tempfile.TemporaryDirectory() as tmp:
            rows = PreSelectionEngine(etf_pool=_pool(), market_data=_market_data(), config=config).run(output_dir=tmp)
        by_symbol = {row["symbol"]: row for row in rows}

        self.assertIn("上市或可用交易日不足", by_symbol["512000"]["reason"])
        self.assertIn("成交额不足", by_symbol["512800"]["reason"])
        self.assertIn("单日涨跌幅超过", by_symbol["512200"]["reason"])
        self.assertFalse(by_symbol["512000"]["selected"])
        self.assertFalse(by_symbol["512800"]["selected"])
        self.assertFalse(by_symbol["512200"]["selected"])

    def test_defense_market_outputs_no_candidates(self) -> None:
        pool = _pool()[:3]
        market_data = {item["symbol"]: _frame(drift=-0.0020) for item in pool}
        config = PreSelectionConfig(min_trading_days=80, min_avg_amount=1_000_000.0, max_candidates=2)

        with tempfile.TemporaryDirectory() as tmp:
            rows = PreSelectionEngine(etf_pool=pool, market_data=market_data, config=config).run(output_dir=tmp)

        self.assertEqual({row["market_state"] for row in rows}, {"防守"})
        self.assertFalse(any(row["selected"] for row in rows))
        self.assertTrue(any("市场状态为防守" in row["reason"] for row in rows))


if __name__ == "__main__":
    unittest.main()
