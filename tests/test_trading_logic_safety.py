from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

from data.downloader import normalize_source_frame
from data.quality import analyze_single_etf
from main import _resolve_effective_signal_date
from signal.weekly_signal import build_signal_trade_plan, generate_weekly_signal_text
from strategy.reduced_equal_weight import ReducedEqualWeightMonthlyStrategy


def _source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": ["2024-01-02", "2024-01-03"],
            "开盘": [10.0, 10.2],
            "最高": [10.5, 10.6],
            "最低": [9.9, 10.0],
            "收盘": [10.3, 10.4],
            "成交量": [1000, 1100],
            "成交额": [10000, 11440],
        }
    )


def _quality_frame(**overrides: list[float] | list[str]) -> pd.DataFrame:
    data: dict[str, list[float] | list[str]] = {
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "open": [10.0, 10.2, 10.3],
        "high": [10.5, 10.6, 10.7],
        "low": [9.9, 10.0, 10.1],
        "close": [10.3, 10.4, 10.5],
        "volume": [1000, 1100, 1200],
        "amount": [10000, 11440, 12600],
        "symbol": ["510300", "510300", "510300"],
        "name": ["test", "test", "test"],
        "source": ["unit-test", "unit-test", "unit-test"],
    }
    data.update(overrides)
    return pd.DataFrame(data)


class TradingLogicSafetyTest(unittest.TestCase):
    def test_close_missing_cannot_pass_normalize_source_frame(self) -> None:
        raw = _source_frame().drop(columns=["收盘"])
        with self.assertRaisesRegex(ValueError, "missing close"):
            normalize_source_frame("510300", raw)

    def test_high_missing_cannot_pass_normalize_source_frame(self) -> None:
        raw = _source_frame().drop(columns=["最高"])
        with self.assertRaisesRegex(ValueError, "missing high"):
            normalize_source_frame("510300", raw)

    def test_high_lower_than_close_is_quality_error(self) -> None:
        result = analyze_single_etf("510300", "test", _quality_frame(high=[10.1, 10.2, 10.3]), min_rows=1)
        self.assertIn("high is lower than close", result.errors)
        self.assertEqual(result.status, "failed")

    def test_low_higher_than_close_is_quality_error(self) -> None:
        result = analyze_single_etf("510300", "test", _quality_frame(low=[10.4, 10.5, 10.6]), min_rows=1)
        self.assertIn("low is higher than close", result.errors)
        self.assertEqual(result.status, "failed")

    def test_close_equal_high_ratio_triggers_warning(self) -> None:
        result = analyze_single_etf("510300", "test", _quality_frame(close=[10.5, 10.6, 10.7]), min_rows=1)
        self.assertIn("close equals high at an unusually high ratio", result.warnings)
        self.assertEqual(result.status, "warning")

    def test_weekend_selected_signal_date_rolls_back_to_previous_trading_day(self) -> None:
        dates = pd.DatetimeIndex(["2026-05-07", "2026-05-08", "2026-05-11"])
        effective, requested, execute = _resolve_effective_signal_date(dates, "2026-05-10")
        self.assertEqual(requested, "2026-05-10")
        self.assertEqual(effective, pd.Timestamp("2026-05-08"))
        self.assertEqual(execute, "2026-05-11")

    def test_selected_signal_date_later_than_latest_data_raises(self) -> None:
        dates = pd.DatetimeIndex(["2026-05-07", "2026-05-08", "2026-05-11"])
        with self.assertRaisesRegex(ValueError, "later than latest data date"):
            _resolve_effective_signal_date(dates, "2026-05-12")

    def test_execute_date_must_be_later_than_effective_signal_date(self) -> None:
        dates = pd.DatetimeIndex(["2026-05-07", "2026-05-08", "2026-05-11"])
        effective, _, execute = _resolve_effective_signal_date(dates, "2026-05-08")
        self.assertGreater(pd.Timestamp(execute), effective)
        effective, _, execute = _resolve_effective_signal_date(dates, "2026-05-11")
        self.assertEqual(effective, pd.Timestamp("2026-05-11"))
        self.assertEqual(execute, "下一交易日，待数据确认")

    def test_observation_cash_does_not_change_target_etf(self) -> None:
        close = pd.DataFrame({"510300": [10.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = ReducedEqualWeightMonthlyStrategy(close, {"510300": {"name": "沪深300ETF"}})
        equity = pd.DataFrame({"equity": [1000.0]}, index=close.index)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            text_small = generate_weekly_signal_text(
                strategy=strategy,
                equity_curve=equity,
                etf_info={"510300": {"name": "沪深300ETF"}},
                signal_weekday=4,
                output_path=tmp_path / "small.txt",
                current_position_path=tmp_path / "current_position.yaml",
                signal_date=pd.Timestamp("2026-05-08"),
                observation_cash=1000.0,
            )
            text_large = generate_weekly_signal_text(
                strategy=strategy,
                equity_curve=equity,
                etf_info={"510300": {"name": "沪深300ETF"}},
                signal_weekday=4,
                output_path=tmp_path / "large.txt",
                current_position_path=tmp_path / "current_position.yaml",
                signal_date=pd.Timestamp("2026-05-08"),
                observation_cash=100000.0,
            )

        target_prefix = "- 510300 沪深300ETF: 目标权重 100%"
        self.assertIn(target_prefix, text_small)
        self.assertIn(target_prefix, text_large)

    def test_small_cash_outputs_clear_insufficient_one_lot_message(self) -> None:
        close = pd.DataFrame({"510300": [20.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = ReducedEqualWeightMonthlyStrategy(close, {"510300": {"name": "沪深300ETF"}})
        equity = pd.DataFrame({"equity": [1000.0]}, index=close.index)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            position_path = tmp_path / "current_position.yaml"
            position_path.write_text(yaml.safe_dump({"cash": 1000, "current_empty": True, "holdings": []}, allow_unicode=True), encoding="utf-8")
            text = generate_weekly_signal_text(
                strategy=strategy,
                equity_curve=equity,
                etf_info={"510300": {"name": "沪深300ETF"}},
                signal_weekday=4,
                output_path=tmp_path / "signal.txt",
                current_position_path=position_path,
                signal_date=pd.Timestamp("2026-05-08"),
                observation_cash=1000.0,
            )
        self.assertIn("当前可用现金不足以买入一手", text)
        self.assertIn("一手所需资金", text)

    def test_missing_position_file_does_not_generate_full_trade_plan(self) -> None:
        close = pd.DataFrame({"510300": [10.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = ReducedEqualWeightMonthlyStrategy(close, {"510300": {"name": "沪深300ETF"}})
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_signal_trade_plan(
                strategy=strategy,
                etf_info={"510300": {"name": "沪深300ETF"}},
                signal_date=pd.Timestamp("2026-05-08"),
                current_position_path=Path(tmp) / "missing.yaml",
                observation_cash=3000.0,
            )
        self.assertFalse(plan["current_position"]["position_configured"])
        self.assertEqual(plan["buy_plan"], [])
        self.assertIn("未找到当前持仓文件", plan["no_action_reasons"][0])

    def test_current_empty_generates_buy_plan_without_sell_plan(self) -> None:
        close = pd.DataFrame({"510300": [10.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = ReducedEqualWeightMonthlyStrategy(close, {"510300": {"name": "沪深300ETF"}})
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            position_path.write_text(yaml.safe_dump({"cash": 3000, "current_empty": True, "holdings": []}, allow_unicode=True), encoding="utf-8")
            plan = build_signal_trade_plan(
                strategy=strategy,
                etf_info={"510300": {"name": "沪深300ETF"}},
                signal_date=pd.Timestamp("2026-05-08"),
                current_position_path=position_path,
                observation_cash=3000.0,
            )
        self.assertTrue(plan["current_position"]["current_empty"])
        self.assertTrue(plan["buy_plan"])
        self.assertEqual(plan["sell_plan"], [])

    def test_holding_outside_target_generates_sell_plan(self) -> None:
        close = pd.DataFrame({"510300": [10.0], "511880": [100.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = ReducedEqualWeightMonthlyStrategy(close, {"510300": {"name": "沪深300ETF"}, "511880": {"name": "银华日利货币ETF"}}, selected_symbols=("510300",))
        with tempfile.TemporaryDirectory() as tmp:
            position_path = Path(tmp) / "current_position.yaml"
            position_path.write_text(
                yaml.safe_dump({"cash": 3000, "current_empty": False, "holdings": [{"symbol": "511880", "shares": 100}]}, allow_unicode=True),
                encoding="utf-8",
            )
            plan = build_signal_trade_plan(
                strategy=strategy,
                etf_info={"510300": {"name": "沪深300ETF"}, "511880": {"name": "银华日利货币ETF"}},
                signal_date=pd.Timestamp("2026-05-08"),
                current_position_path=position_path,
            )
        self.assertEqual(plan["sell_plan"][0]["ETF代码"], "511880")
        self.assertEqual(plan["sell_plan"][0]["建议卖出份额"], 100)


if __name__ == "__main__":
    unittest.main()
