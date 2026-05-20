from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from backtest.engine import BacktestEngine
from backtest.portfolio import FeeConfig
from signal.daily_signal import build_signal_trade_plan
from strategy.etf_rotation import DailyMomentumRotationStrategy, StrategyConfig, get_rebalance_dates


def _momentum_close(periods: int = 150) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=periods)
    day = np.arange(len(dates), dtype=float)
    steady_winner = 100.0 + day
    late_winner = 100.0 + day * 0.1
    late_winner[120:] += (day[120:] - 119.0) * 3.0
    return pd.DataFrame(
        {
            "510300": steady_winner,
            "510500": late_winner,
            "511880": np.full(len(dates), 100.0),
        },
        index=dates,
    )


def _strategy(max_positions: int = 1, close: pd.DataFrame | None = None) -> DailyMomentumRotationStrategy:
    config = StrategyConfig(
        momentum_period=20,
        ma_period=60,
        max_positions=max_positions,
        sell_rank_threshold=5,
        enable_min_momentum_filter=True,
        min_momentum_threshold=0.0,
        min_trading_days=60,
    )
    return DailyMomentumRotationStrategy(
        close if close is not None else _momentum_close(),
        {
            "510300": {"name": "ETF A"},
            "510500": {"name": "ETF B"},
            "511880": {"name": "Cash ETF"},
        },
        config,
    )


def _write_position(path: Path, cash: float, holdings: list[dict[str, object]], current_empty: bool = False) -> None:
    path.write_text(
        yaml.safe_dump({"cash": cash, "current_empty": current_empty, "holdings": holdings}, allow_unicode=True),
        encoding="utf-8",
    )


class DailyMomentumRotationTest(unittest.TestCase):
    def test_daily_rebalance_dates_include_each_trading_day(self) -> None:
        dates = pd.DatetimeIndex(["2026-05-11", "2026-05-12", "2026-05-13"])
        self.assertEqual(get_rebalance_dates(dates), [pd.Timestamp("2026-05-11"), pd.Timestamp("2026-05-12"), pd.Timestamp("2026-05-13")])

    def test_non_daily_frequency_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "只支持日频信号"):
            get_rebalance_dates(pd.DatetimeIndex(["2026-05-11"]), frequency="nondaily")

    def test_different_signal_dates_can_change_rank_and_target(self) -> None:
        strategy = _strategy()
        first_date = strategy.close.index[100]
        second_date = strategy.close.index[149]

        first_signal = strategy.generate_target(first_date, [])
        second_signal = strategy.generate_target(second_date, [])

        self.assertEqual(first_signal["target"], ["510300"])
        self.assertEqual(second_signal["target"], ["510500"])

    def test_observation_cash_does_not_change_target_etf(self) -> None:
        strategy = _strategy(max_positions=2)
        signal_date = strategy.close.index[-1]
        etf_info = {"510300": {"name": "ETF A"}, "510500": {"name": "ETF B"}, "511880": {"name": "Cash ETF"}}
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            _write_position(position_path, 1000.0, [], current_empty=True)
            small = build_signal_trade_plan(strategy, etf_info, signal_date, current_position_path=position_path, observation_cash=1000.0)
            large = build_signal_trade_plan(strategy, etf_info, signal_date, current_position_path=position_path, observation_cash=100000.0)

        self.assertEqual(small["target"], large["target"])

    def test_data_insufficient_does_not_force_buy_signal(self) -> None:
        close = _momentum_close(periods=40)
        strategy = _strategy(close=close)
        signal = strategy.generate_target(close.index[-1], [])
        self.assertEqual(signal["target"], [])
        self.assertTrue(signal["ranks"].empty or not signal["ranks"]["eligible"].any())

    def test_close_missing_errors_instead_of_using_high(self) -> None:
        dates = pd.bdate_range("2025-01-01", periods=65)
        frame = pd.DataFrame(
            {
                "open": np.full(len(dates), 10.0),
                "high": np.full(len(dates), 11.0),
                "low": np.full(len(dates), 9.0),
                "volume": np.full(len(dates), 1000.0),
                "amount": np.full(len(dates), 10000.0),
                "symbol": "510300",
                "name": "ETF A",
                "source": "unit-test",
            },
            index=dates,
        )
        with self.assertRaisesRegex(ValueError, "missing field: close"):
            BacktestEngine(
                market_data={"510300": frame},
                etf_pool=[{"symbol": "510300", "name": "ETF A", "category": "test"}],
                strategy_config=StrategyConfig(),
                fee_config=FeeConfig(),
                initial_cash=10000,
            )

    def test_current_empty_generates_buy_plan_without_sell_plan(self) -> None:
        strategy = _strategy(max_positions=1)
        signal_date = strategy.close.index[-1]
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            _write_position(position_path, 100000.0, [], current_empty=True)
            plan = build_signal_trade_plan(
                strategy,
                {"510300": {"name": "ETF A"}, "510500": {"name": "ETF B"}, "511880": {"name": "Cash ETF"}},
                signal_date,
                current_position_path=position_path,
                observation_cash=100000.0,
            )
        self.assertTrue(plan["buy_plan"])
        self.assertEqual(plan["sell_plan"], [])

    def test_holding_outside_target_generates_sell_plan(self) -> None:
        strategy = _strategy(max_positions=1)
        signal_date = strategy.close.index[-1]
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            _write_position(position_path, 3000.0, [{"symbol": "510300", "shares": 100}])
            plan = build_signal_trade_plan(
                strategy,
                {"510300": {"name": "ETF A"}, "510500": {"name": "ETF B"}, "511880": {"name": "Cash ETF"}},
                signal_date,
                current_position_path=position_path,
            )
        self.assertEqual(plan["sell_plan"][0]["ETF代码"], "510300")
        self.assertIn("不在本次目标组合", plan["sell_plan"][0]["卖出原因"])

    def test_small_cash_skips_buy_when_less_than_one_lot(self) -> None:
        strategy = _strategy(max_positions=1)
        signal_date = strategy.close.index[-1]
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            _write_position(position_path, 50.0, [], current_empty=True)
            plan = build_signal_trade_plan(
                strategy,
                {"510300": {"name": "ETF A"}, "510500": {"name": "ETF B"}, "511880": {"name": "Cash ETF"}},
                signal_date,
                current_position_path=position_path,
                observation_cash=50.0,
            )
        self.assertEqual(plan["buy_plan"], [])
        self.assertTrue(plan["skipped_buy_plan"])
        self.assertIn("不足以买入一手", plan["skipped_buy_plan"][0]["资金不足时的提示"])


if __name__ == "__main__":
    unittest.main()
