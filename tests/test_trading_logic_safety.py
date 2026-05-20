
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

from signal.daily_signal import build_signal_trade_plan, generate_daily_signal_text
from signal.trade_policy import build_sell_execution_plan, validate_risk_trigger_price, validate_sell_prices
from strategy.etf_rotation import StrategyConfig


MA60_SOURCE = "60\u65e5\u5747\u7ebf"
DATA_ABNORMAL_SOURCE = "\u6570\u636e\u5f02\u5e38"
ABNORMAL_HIDDEN = "\u5f02\u5e38\uff0c\u5df2\u9690\u85cf"


class StaticDailyStrategy:
    def __init__(self, close: pd.DataFrame, etf_info: dict[str, dict[str, str]], target: list[str] | None = None):
        self.close = close
        self.etf_info = etf_info
        self.config = StrategyConfig(momentum_period=20, ma_period=60, max_positions=max(len(target or []), 1))
        self.target = target if target is not None else list(close.columns)

    def generate_target(self, signal_date: pd.Timestamp, current_holdings: list[str]) -> dict[str, object]:
        ranks = pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "name": self.etf_info.get(symbol, {}).get("name", symbol),
                    "close": float(self.close.loc[signal_date, symbol]),
                    "rank": idx + 1,
                    "eligible": symbol in self.target,
                    "selected": symbol in self.target,
                    "data_quality_passed": True,
                    "above_ma": True,
                    "momentum_passed": True,
                    "momentum": 0.05,
                    "ma": float(self.close.loc[signal_date, symbol]),
                    "score": 1.0,
                    "final_signal": "selected" if symbol in self.target else "filtered_out",
                }
                for idx, symbol in enumerate(self.close.columns)
            ]
        )
        return {
            "signal_date": signal_date,
            "target": list(self.target),
            "ranks": ranks,
            "eligible": ranks[ranks["eligible"]],
            "buy_reasons": {symbol: "daily momentum and trend passed" for symbol in self.target if symbol not in current_holdings},
            "sell_reasons": {symbol: "target removed" for symbol in current_holdings if symbol not in self.target},
            "keep_reasons": {symbol: "target retained" for symbol in current_holdings if symbol in self.target},
            "market_filter_passed": True,
        }


class TradingLogicSafetyTest(unittest.TestCase):
    def test_observation_cash_does_not_change_target_etf(self) -> None:
        close = pd.DataFrame({"510300": [10.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = StaticDailyStrategy(close, {"510300": {"name": "ETF A"}})
        equity = pd.DataFrame({"equity": [1000.0]}, index=close.index)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            text_small = generate_daily_signal_text(
                strategy=strategy,
                equity_curve=equity,
                etf_info={"510300": {"name": "ETF A"}},
                signal_weekday=4,
                output_path=tmp_path / "small.txt",
                current_position_path=tmp_path / "current_position.yaml",
                signal_date=pd.Timestamp("2026-05-08"),
                observation_cash=1000.0,
            )
            text_large = generate_daily_signal_text(
                strategy=strategy,
                equity_curve=equity,
                etf_info={"510300": {"name": "ETF A"}},
                signal_weekday=4,
                output_path=tmp_path / "large.txt",
                current_position_path=tmp_path / "current_position.yaml",
                signal_date=pd.Timestamp("2026-05-08"),
                observation_cash=100000.0,
            )

        self.assertIn("510300", text_small)
        self.assertIn("510300", text_large)
        self.assertIn("100%", text_small)
        self.assertIn("100%", text_large)

    def test_missing_position_file_does_not_generate_full_trade_plan(self) -> None:
        close = pd.DataFrame({"510300": [10.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = StaticDailyStrategy(close, {"510300": {"name": "ETF A"}})
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_signal_trade_plan(
                strategy=strategy,
                etf_info={"510300": {"name": "ETF A"}},
                signal_date=pd.Timestamp("2026-05-08"),
                current_position_path=Path(tmp) / "missing.yaml",
                observation_cash=3000.0,
            )
        self.assertFalse(plan["current_position"]["position_configured"])
        self.assertEqual(plan["buy_plan"], [])
        self.assertTrue(plan["no_action_reasons"])

    def test_current_empty_generates_buy_plan_without_sell_plan(self) -> None:
        close = pd.DataFrame({"510300": [10.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = StaticDailyStrategy(close, {"510300": {"name": "ETF A"}})
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            position_path.write_text(yaml.safe_dump({"cash": 3000, "current_empty": True, "holdings": []}), encoding="utf-8")
            plan = build_signal_trade_plan(
                strategy=strategy,
                etf_info={"510300": {"name": "ETF A"}},
                signal_date=pd.Timestamp("2026-05-08"),
                current_position_path=position_path,
                observation_cash=3000.0,
            )
        self.assertTrue(plan["current_position"]["current_empty"])
        self.assertTrue(plan["buy_plan"])
        self.assertEqual(plan["sell_plan"], [])

    def test_holding_outside_target_generates_sell_plan(self) -> None:
        close = pd.DataFrame({"510300": [10.0], "511880": [100.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = StaticDailyStrategy(close, {"510300": {"name": "ETF A"}, "511880": {"name": "Cash ETF"}}, target=["510300"])
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            position_path.write_text(
                yaml.safe_dump({"cash": 3000, "current_empty": False, "holdings": [{"symbol": "511880", "shares": 100}]}),
                encoding="utf-8",
            )
            plan = build_signal_trade_plan(
                strategy=strategy,
                etf_info={"510300": {"name": "ETF A"}, "511880": {"name": "Cash ETF"}},
                signal_date=pd.Timestamp("2026-05-08"),
                current_position_path=position_path,
            )
        values = set(plan["sell_plan"][0].values())
        self.assertIn("511880", values)
        self.assertIn(100, values)

    def test_validate_trend_break_risk_limit_not_above_current_price(self) -> None:
        row = validate_sell_prices({"symbol": "515050", "current_price": 1.156, "sell_type": "trend_break", "sell_ratio": 1.0, "suggested_sell_shares": 1000000, "risk_limit_price": 2.557})
        self.assertLessEqual(float(row["risk_limit_price"]), 1.156 * 1.02)
        self.assertEqual(row["first_sell_price"], 1.156)
        self.assertEqual(row["second_sell_price"], 1.150)
        self.assertEqual(row["third_sell_price"], 1.144)

    def test_validate_stop_loss_risk_limit_not_above_current_price(self) -> None:
        row = validate_sell_prices({"symbol": "515050", "current_price": 1.000, "sell_type": "stop_loss", "sell_ratio": 1.0, "suggested_sell_shares": 1000, "risk_limit_price": 1.500})
        self.assertLessEqual(float(row["risk_limit_price"]), 1.02)
        self.assertEqual(row["first_sell_price"], 1.000)

    def test_hold_does_not_generate_sell_shares_or_prices(self) -> None:
        row = validate_sell_prices({"symbol": "515050", "current_price": 1.156, "sell_type": "hold", "sell_ratio": 0.5, "suggested_sell_shares": 500, "first_sell_price": 1.156})
        self.assertEqual(row["suggested_sell_shares"], 0.0)
        self.assertEqual(row["sell_ratio"], 0.0)
        self.assertIsNone(row["first_sell_price"])

    def test_current_price_missing_skips_sell_execution_prices(self) -> None:
        row = validate_sell_prices({"symbol": "515050", "current_price": None, "sell_type": "trend_break", "sell_ratio": 1.0, "suggested_sell_shares": 1000, "risk_limit_price": 2.557})
        self.assertIsNone(row["first_sell_price"])
        self.assertEqual(row["suggested_sell_shares"], 0.0)

    def test_sell_execution_uses_symbol_aligned_current_prices(self) -> None:
        dates = pd.date_range("2026-01-01", periods=70)
        frame_a = pd.DataFrame({"close": [1.50] * 69 + [1.00], "high": [1.52] * 70, "low": [0.98] * 70}, index=dates)
        frame_b = pd.DataFrame({"close": [2.50] * 69 + [2.00], "high": [2.52] * 70, "low": [1.98] * 70}, index=dates)
        plan = build_sell_execution_plan(
            positions={"111111": {"name": "A", "shares": 1000, "average_buy_price": 1.0}, "222222": {"name": "B", "shares": 1000, "average_buy_price": 2.0}},
            market_frames={"111111": frame_a, "222222": frame_b},
            target_symbols=set(),
            quote_map={"222222": {"latest_price": 2.0, "price_actionable": True}, "111111": {"latest_price": 1.0, "price_actionable": True}},
        )
        by_symbol = {item["symbol"]: item for item in plan}
        self.assertEqual(by_symbol["111111"]["current_price"], 1.0)
        self.assertEqual(by_symbol["111111"]["first_sell_price"], 1.0)
        self.assertEqual(by_symbol["222222"]["current_price"], 2.0)
        self.assertEqual(by_symbol["222222"]["first_sell_price"], 2.0)

    def test_abnormal_risk_trigger_cannot_drive_trend_break_decision(self) -> None:
        dates = pd.date_range("2026-01-01", periods=70)
        frame = pd.DataFrame({"close": [3.0] * 69 + [1.156], "high": [3.05] * 69 + [1.17], "low": [2.95] * 69 + [1.14]}, index=dates)
        plan = build_sell_execution_plan(positions={"515050": {"name": "ETF", "shares": 1000000, "average_buy_price": 1.142}}, market_frames={"515050": frame}, target_symbols={"515050"}, quote_map={"515050": {"latest_price": 1.156, "price_actionable": True}})
        self.assertEqual(plan[0]["sell_type"], "hold")
        self.assertEqual(plan[0]["suggested_sell_shares"], 0.0)
        self.assertEqual(plan[0]["risk_trigger_source"], DATA_ABNORMAL_SOURCE)

    def test_risk_trigger_must_include_source(self) -> None:
        row = validate_risk_trigger_price({"symbol": "515050", "current_price": 1.156, "sell_type": "trend_break", "risk_trigger_price": 1.180, "risk_trigger_source": MA60_SOURCE})
        self.assertEqual(row["risk_trigger_source"], MA60_SOURCE)
        self.assertAlmostEqual(float(row["risk_trigger_ratio_to_current"]), 1.020761, places=5)

    def test_ma60_is_calculated_independently_by_symbol_for_risk_trigger(self) -> None:
        dates = pd.date_range("2026-01-01", periods=70)
        frame_a = pd.DataFrame({"close": [1.2] * 60 + [1.0] * 10, "high": [1.22] * 70, "low": [0.98] * 70}, index=dates)
        frame_b = pd.DataFrame({"close": [2.2] * 60 + [2.0] * 10, "high": [2.22] * 70, "low": [1.98] * 70}, index=dates)
        plan = build_sell_execution_plan(
            positions={"111111": {"name": "A", "shares": 1000, "average_buy_price": 1.0}, "222222": {"name": "B", "shares": 1000, "average_buy_price": 2.0}},
            market_frames={"222222": frame_b, "111111": frame_a},
            target_symbols={"111111", "222222"},
            quote_map={"222222": {"latest_price": 2.0, "price_actionable": True}, "111111": {"latest_price": 1.0, "price_actionable": True}},
        )
        by_symbol = {item["symbol"]: item for item in plan}
        self.assertAlmostEqual(float(by_symbol["111111"]["raw_risk_trigger_price"]), frame_a["close"].tail(60).mean(), places=3)
        self.assertAlmostEqual(float(by_symbol["222222"]["raw_risk_trigger_price"]), frame_b["close"].tail(60).mean(), places=3)

    def test_515050_abnormal_trigger_case_is_hidden(self) -> None:
        row = validate_sell_prices({"symbol": "515050", "name": "ETF", "current_price": 1.156, "sell_type": "trend_break", "sell_ratio": 1.0, "suggested_sell_shares": 1000000, "risk_trigger_price": 2.557, "risk_trigger_source": MA60_SOURCE})
        self.assertEqual(row["risk_trigger_display"], ABNORMAL_HIDDEN)
        self.assertIsNone(row["risk_trigger_price"])
        self.assertEqual(row["sell_type"], "hold")
        self.assertIsNone(row["first_sell_price"])

    def test_price_basis_jump_marks_data_abnormal(self) -> None:
        dates = pd.date_range("2026-01-01", periods=70)
        frame = pd.DataFrame({"close": [3.0] * 69 + [1.156], "high": [3.05] * 69 + [1.17], "low": [2.95] * 69 + [1.14]}, index=dates)
        plan = build_sell_execution_plan(positions={"515050": {"name": "ETF", "shares": 1000000, "average_buy_price": 1.142}}, market_frames={"515050": frame}, target_symbols={"515050"}, quote_map={"515050": {"latest_price": 1.156, "price_actionable": True}})
        self.assertFalse(plan[0]["price_basis_consistent"])
        self.assertEqual(plan[0]["risk_trigger_source"], DATA_ABNORMAL_SOURCE)


if __name__ == "__main__":
    unittest.main()
