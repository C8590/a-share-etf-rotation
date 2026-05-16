from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from backtest.engine import BacktestEngine
from backtest.portfolio import FeeConfig
from signal.weekly_signal import build_signal_trade_plan
from strategy.etf_rotation import MomentumRotationMonthlyStrategy, StrategyConfig
from strategy.reduced_equal_weight import ReducedEqualWeightMonthlyStrategy


def _momentum_close() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=90)
    day = np.arange(len(dates), dtype=float)
    steady_winner = 100.0 + day
    late_winner = 100.0 + day * 0.1
    late_winner[70:] += (day[70:] - 69.0) * 3.0
    return pd.DataFrame(
        {
            "510300": steady_winner,
            "510500": late_winner,
            "511880": np.full(len(dates), 100.0),
        },
        index=dates,
    )


def _strategy(max_positions: int = 1) -> MomentumRotationMonthlyStrategy:
    config = StrategyConfig(
        strategy_type="momentum_rotation_monthly",
        momentum_period=20,
        ma_period=60,
        max_positions=max_positions,
        enable_min_momentum_filter=True,
        min_momentum_threshold=0.0,
    )
    return MomentumRotationMonthlyStrategy(
        _momentum_close(),
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


class MomentumRotationMonthlyTest(unittest.TestCase):
    def test_different_signal_dates_can_change_rank_and_target(self) -> None:
        strategy = _strategy()
        first_date = strategy.close.index[65]
        second_date = strategy.close.index[89]

        first_signal = strategy.generate_target(first_date, [])
        second_signal = strategy.generate_target(second_date, [])

        self.assertEqual(first_signal["target"], ["510300"])
        self.assertEqual(second_signal["target"], ["510500"])
        self.assertNotEqual(
            first_signal["ranks"].sort_values("rank")["symbol"].tolist(),
            second_signal["ranks"].sort_values("rank")["symbol"].tolist(),
        )

    def test_observation_cash_does_not_change_target_etf(self) -> None:
        strategy = _strategy(max_positions=2)
        signal_date = strategy.close.index[89]
        etf_info = {
            "510300": {"name": "ETF A"},
            "510500": {"name": "ETF B"},
            "511880": {"name": "Cash ETF"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            position_path.write_text(yaml.safe_dump({"cash": 1000, "current_empty": True, "holdings": []}), encoding="utf-8")
            small = build_signal_trade_plan(strategy, etf_info, signal_date, current_position_path=position_path, observation_cash=1000.0)
            large = build_signal_trade_plan(strategy, etf_info, signal_date, current_position_path=position_path, observation_cash=100000.0)

        self.assertEqual(small["target"], large["target"])

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
                strategy_config=StrategyConfig(strategy_type="momentum_rotation_monthly"),
                fee_config=FeeConfig(),
                initial_cash=10000,
            )

    def test_current_empty_generates_buy_plan_without_sell_plan(self) -> None:
        strategy = _strategy(max_positions=1)
        signal_date = strategy.close.index[89]
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
        signal_date = strategy.close.index[89]
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

    def test_target_holding_generates_hold_plan(self) -> None:
        strategy = _strategy(max_positions=1)
        signal_date = strategy.close.index[89]
        target = strategy.generate_target(signal_date, [])["target"][0]
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            _write_position(position_path, 10000.0, [{"symbol": target, "shares": 100}])
            plan = build_signal_trade_plan(
                strategy,
                {"510300": {"name": "ETF A"}, "510500": {"name": "ETF B"}, "511880": {"name": "Cash ETF"}},
                signal_date,
                current_position_path=position_path,
            )
        self.assertEqual(plan["hold_plan"][0]["ETF代码"], target)
        self.assertIn("原因", plan["hold_plan"][0])

    def test_sell_reason_when_holding_drops_outside_sell_rank_threshold(self) -> None:
        strategy = _strategy(max_positions=1)
        strategy.config = StrategyConfig(
            strategy_type="momentum_rotation_monthly",
            momentum_period=20,
            ma_period=60,
            max_positions=1,
            sell_rank_threshold=1,
            enable_min_momentum_filter=True,
            min_momentum_threshold=0.0,
        )
        signal_date = strategy.close.index[89]
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            _write_position(position_path, 3000.0, [{"symbol": "510300", "shares": 100}])
            plan = build_signal_trade_plan(
                strategy,
                {"510300": {"name": "ETF A"}, "510500": {"name": "ETF B"}, "511880": {"name": "Cash ETF"}},
                signal_date,
                current_position_path=position_path,
            )
        self.assertIn("卖出阈值 1", plan["sell_plan"][0]["卖出原因"])

    def test_sell_reason_when_holding_breaks_moving_average(self) -> None:
        dates = pd.bdate_range("2025-01-01", periods=80)
        close = pd.DataFrame(
            {
                "510300": np.linspace(10.0, 30.0, len(dates)),
                "518880": [100.0] * 65 + [50.0] * 15,
            },
            index=dates,
        )
        strategy = MomentumRotationMonthlyStrategy(
            close,
            {"510300": {"name": "Good ETF"}, "518880": {"name": "Bad ETF"}},
            StrategyConfig(
                strategy_type="momentum_rotation_monthly",
                momentum_period=20,
                ma_period=60,
                max_positions=1,
                sell_rank_threshold=5,
                enable_min_momentum_filter=True,
                min_momentum_threshold=0.0,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            _write_position(position_path, 3000.0, [{"symbol": "518880", "shares": 100}])
            plan = build_signal_trade_plan(
                strategy,
                {"510300": {"name": "Good ETF"}, "518880": {"name": "Bad ETF"}},
                close.index[-1],
                current_position_path=position_path,
            )
        self.assertIn("日均线", plan["sell_plan"][0]["卖出原因"])

    def test_small_cash_skips_buy_when_less_than_one_lot(self) -> None:
        strategy = _strategy(max_positions=1)
        signal_date = strategy.close.index[89]
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

    def test_reduced_equal_weight_keeps_fixed_basket_across_dates(self) -> None:
        close = _momentum_close()
        strategy = ReducedEqualWeightMonthlyStrategy(
            close,
            {"510300": {"name": "ETF A"}, "510500": {"name": "ETF B"}, "511880": {"name": "Cash ETF"}},
            selected_symbols=("510300", "511880"),
        )
        first = strategy.generate_target(close.index[65], [])["target"]
        second = strategy.generate_target(close.index[89], [])["target"]
        self.assertEqual(first, second)
        self.assertEqual(first, ["510300", "511880"])


if __name__ == "__main__":
    unittest.main()
