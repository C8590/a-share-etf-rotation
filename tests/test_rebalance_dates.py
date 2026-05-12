from __future__ import annotations

import unittest

import pandas as pd

from main import load_strategy_settings
from strategy.etf_rotation import get_rebalance_dates


class RebalanceDatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dates = pd.bdate_range("2024-01-02", "2024-02-29")

    def test_monthly_month_end_keeps_existing_weekly_month_end_logic(self) -> None:
        self.assertEqual(
            get_rebalance_dates(self.dates, frequency="monthly", signal_weekday=4, rebalance_timing="month_end"),
            [pd.Timestamp("2024-01-26"), pd.Timestamp("2024-02-23")],
        )

    def test_monthly_month_start_uses_first_available_trading_day(self) -> None:
        self.assertEqual(
            get_rebalance_dates(self.dates, frequency="monthly", rebalance_timing="month_start"),
            [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-02-01")],
        )

    def test_monthly_nth_trading_day_uses_configured_day(self) -> None:
        self.assertEqual(
            get_rebalance_dates(
                self.dates,
                frequency="monthly",
                rebalance_timing="nth_trading_day",
                rebalance_day=5,
            ),
            [pd.Timestamp("2024-01-08"), pd.Timestamp("2024-02-07")],
        )

    def test_monthly_day_of_month_rolls_to_next_trading_day(self) -> None:
        dates = pd.bdate_range("2024-06-03", "2024-07-31")
        self.assertEqual(
            get_rebalance_dates(
                dates,
                frequency="monthly",
                rebalance_timing="day_of_month",
                rebalance_day_of_month=15,
                rebalance_roll="next",
            ),
            [pd.Timestamp("2024-06-17"), pd.Timestamp("2024-07-15")],
        )

    def test_monthly_day_of_month_rolls_to_previous_trading_day(self) -> None:
        dates = pd.bdate_range("2024-06-03", "2024-07-31")
        self.assertEqual(
            get_rebalance_dates(
                dates,
                frequency="monthly",
                rebalance_timing="day_of_month",
                rebalance_day_of_month=15,
                rebalance_roll="previous",
            ),
            [pd.Timestamp("2024-06-14"), pd.Timestamp("2024-07-15")],
        )

    def test_monthly_day_of_month_rolls_to_nearest_trading_day(self) -> None:
        dates = pd.bdate_range("2024-06-03", "2024-07-31")
        self.assertEqual(
            get_rebalance_dates(
                dates,
                frequency="monthly",
                rebalance_timing="day_of_month",
                rebalance_day_of_month=15,
                rebalance_roll="nearest",
            ),
            [pd.Timestamp("2024-06-14"), pd.Timestamp("2024-07-15")],
        )

    def test_monthly_day_of_month_31_uses_calendar_month_end_before_roll(self) -> None:
        dates = pd.bdate_range("2024-06-03", "2024-07-31")
        self.assertEqual(
            get_rebalance_dates(
                dates,
                frequency="monthly",
                rebalance_timing="day_of_month",
                rebalance_day_of_month=31,
                rebalance_roll="next",
            ),
            [pd.Timestamp("2024-07-01"), pd.Timestamp("2024-07-31")],
        )

    def test_weekly_and_biweekly_ignore_monthly_timing(self) -> None:
        self.assertEqual(
            get_rebalance_dates(self.dates, frequency="weekly", signal_weekday=4),
            get_rebalance_dates(
                self.dates,
                frequency="weekly",
                signal_weekday=4,
                rebalance_timing="day_of_month",
                rebalance_day_of_month=15,
                rebalance_roll="previous",
            ),
        )
        self.assertEqual(
            get_rebalance_dates(self.dates, frequency="biweekly", signal_weekday=4),
            get_rebalance_dates(
                self.dates,
                frequency="biweekly",
                signal_weekday=4,
                rebalance_timing="day_of_month",
                rebalance_day_of_month=15,
                rebalance_roll="nearest",
            ),
        )

    def test_monthly_configs_load_valid_rebalance_settings(self) -> None:
        for config_path in [
            "config/strategy_equal_weight_monthly.yaml",
            "config/strategy_reduced_equal_weight_monthly.yaml",
        ]:
            with self.subTest(config_path=config_path):
                _, _, strategy_config = load_strategy_settings(config_path)
                self.assertEqual(strategy_config.rebalance_frequency, "monthly")
                self.assertIn(strategy_config.rebalance_timing, {"month_end", "month_start", "nth_trading_day", "day_of_month"})
                if strategy_config.rebalance_timing == "nth_trading_day":
                    self.assertIsNotNone(strategy_config.rebalance_day)
                if strategy_config.rebalance_timing == "day_of_month":
                    self.assertIsNotNone(strategy_config.rebalance_day_of_month)
                    self.assertGreaterEqual(int(strategy_config.rebalance_day_of_month), 1)
                    self.assertLessEqual(int(strategy_config.rebalance_day_of_month), 31)
                    self.assertIn(strategy_config.rebalance_roll, {"next", "previous", "nearest"})


if __name__ == "__main__":
    unittest.main()
