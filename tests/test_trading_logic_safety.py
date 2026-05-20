from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from data.downloader import normalize_source_frame
from data.quality import analyze_single_etf
from data.quotes import get_exchange_prefix, validate_quote_price
from data.trading_calendar import get_market_phase, get_next_trading_day, resolve_signal_context
from main import _resolve_effective_signal_date
from signal.trade_policy import build_sell_execution_plan, validate_risk_trigger_price, validate_sell_prices
from signal.daily_signal import build_signal_trade_plan, generate_daily_signal_text
from strategy.etf_rotation import StrategyConfig
from ui.signal_parser import parse_sell_execution_table


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
    }
    data.update(overrides)
    return pd.DataFrame(data)


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
            "buy_reasons": {symbol: "日频动量和趋势确认通过" for symbol in self.target if symbol not in current_holdings},
            "sell_reasons": {symbol: "趋势失效，建议减仓或退出" for symbol in current_holdings if symbol not in self.target},
            "keep_reasons": {symbol: "趋势仍保持，继续持有" for symbol in current_holdings if symbol in self.target},
            "market_filter_passed": True,
        }


class TradingLogicSafetyTest(unittest.TestCase):
    def test_etf_exchange_prefix_is_exact(self) -> None:
        self.assertEqual(get_exchange_prefix("159583")["sina_code"], "sz159583")
        self.assertEqual(get_exchange_prefix("159583")["eastmoney_sec_id"], "0.159583")
        self.assertEqual(get_exchange_prefix("515050")["sina_code"], "sh515050")
        self.assertEqual(get_exchange_prefix("515050")["eastmoney_sec_id"], "1.515050")

    def test_validate_quote_rejects_wrong_515050_price(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        result = validate_quote_price(
            "515050",
            {
                "code": "515050",
                "latest_price": 3.330,
                "prev_close": 1.110,
                "open": 1.088,
                "high": 1.158,
                "low": 1.088,
                "quote_date": today,
                "quote_time": "15:00:00",
            },
            pd.DataFrame(),
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["price_status"], "价格异常，已停用")

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

    def test_weekend_selected_signal_date_does_not_roll_back(self) -> None:
        dates = pd.DatetimeIndex(["2026-05-07", "2026-05-08", "2026-05-11"])
        with self.assertRaisesRegex(ValueError, "不能自动回退"):
            _resolve_effective_signal_date(dates, "2026-05-10")

    def test_selected_signal_date_later_than_latest_data_raises(self) -> None:
        dates = pd.DatetimeIndex(["2026-05-07", "2026-05-08", "2026-05-11"])
        with self.assertRaisesRegex(ValueError, "本地日线数据只更新到 2026-05-11"):
            _resolve_effective_signal_date(dates, "2026-05-12")

    def test_execute_date_must_be_later_than_effective_signal_date(self) -> None:
        dates = pd.DatetimeIndex(["2026-05-07", "2026-05-08", "2026-05-11"])
        effective, _, execute = _resolve_effective_signal_date(dates, "2026-05-08")
        self.assertGreater(pd.Timestamp(execute), effective)
        effective, _, execute = _resolve_effective_signal_date(dates, "2026-05-11")
        self.assertEqual(effective, pd.Timestamp("2026-05-11"))
        self.assertEqual(execute, "2026-05-12")

    def test_market_phase_after_close(self) -> None:
        calendar = pd.DatetimeIndex(["2026-05-13", "2026-05-14"])
        now = datetime(2026, 5, 13, 22, 44, 58, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(get_market_phase(now, calendar), "已收盘")

    def test_after_close_context_uses_today_and_next_trading_day(self) -> None:
        calendar = pd.DatetimeIndex(["2026-05-12", "2026-05-13", "2026-05-14"])
        now = datetime(2026, 5, 13, 22, 44, 58, tzinfo=ZoneInfo("Asia/Shanghai"))
        context = resolve_signal_context(
            selected_signal_date="2026-05-13",
            mode="manual_selected_date",
            now=now,
            data_cutoff_date="2026-05-13",
            trading_calendar=calendar,
        )
        self.assertEqual(context.actual_signal_date.isoformat(), "2026-05-13")
        self.assertEqual(context.execution_date.isoformat(), "2026-05-14")
        self.assertEqual(get_next_trading_day(pd.Timestamp("2026-05-13"), calendar).isoformat(), "2026-05-14")

    def test_manual_context_blocks_stale_local_data(self) -> None:
        calendar = pd.DatetimeIndex(["2026-05-12", "2026-05-13", "2026-05-14"])
        now = datetime(2026, 5, 13, 22, 44, 58, tzinfo=ZoneInfo("Asia/Shanghai"))
        with self.assertRaisesRegex(ValueError, "本地日线数据只更新到 2026-05-12"):
            resolve_signal_context(
                selected_signal_date="2026-05-13",
                mode="manual_selected_date",
                now=now,
                data_cutoff_date="2026-05-12",
                trading_calendar=calendar,
            )

    def test_observation_cash_does_not_change_target_etf(self) -> None:
        close = pd.DataFrame({"510300": [10.0]}, index=pd.DatetimeIndex(["2026-05-08"]))
        strategy = StaticDailyStrategy(close, {"510300": {"name": "沪深300ETF"}})
        equity = pd.DataFrame({"equity": [1000.0]}, index=close.index)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            text_small = generate_daily_signal_text(
                strategy=strategy,
                equity_curve=equity,
                etf_info={"510300": {"name": "沪深300ETF"}},
                signal_weekday=4,
                output_path=tmp_path / "small.txt",
                current_position_path=tmp_path / "current_position.yaml",
                signal_date=pd.Timestamp("2026-05-08"),
                observation_cash=1000.0,
            )
            text_large = generate_daily_signal_text(
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
        strategy = StaticDailyStrategy(close, {"510300": {"name": "沪深300ETF"}})
        equity = pd.DataFrame({"equity": [1000.0]}, index=close.index)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            position_path = tmp_path / "current_position.yaml"
            position_path.write_text(yaml.safe_dump({"cash": 1000, "current_empty": True, "holdings": []}, allow_unicode=True), encoding="utf-8")
            text = generate_daily_signal_text(
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
        strategy = StaticDailyStrategy(close, {"510300": {"name": "沪深300ETF"}})
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
        strategy = StaticDailyStrategy(close, {"510300": {"name": "沪深300ETF"}})
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
        strategy = StaticDailyStrategy(close, {"510300": {"name": "沪深300ETF"}, "511880": {"name": "银华日利货币ETF"}}, target=["510300"])
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

    def test_sell_execution_table_hides_internal_english_headers(self) -> None:
        row = pd.Series(
            {
                "sell_execution_plan": json.dumps(
                    [
                        {
                            "symbol": "515050",
                            "name": "测试ETF",
                            "current_shares": 1000,
                            "target_shares": 0,
                            "current_price": 1.156,
                            "sell_type": "trend_break",
                            "sell_ratio": 0.5,
                            "suggested_sell_shares": 500,
                            "first_sell_price": 1.156,
                            "second_sell_price": 1.245,
                            "third_sell_price": 1.302,
                            "first_take_profit_price": 1.199,
                            "second_take_profit_price": 1.245,
                            "third_take_profit_price": 1.302,
                            "risk_trigger_price": 1.827,
                            "risk_limit_price": 2.557,
                            "execution_note": "测试执行说明",
                            "risk_note": "测试风险提示，旧文案风控卖出价应被替换",
                        }
                    ],
                    ensure_ascii=False,
                )
            }
        )
        table = parse_sell_execution_table(row)
        forbidden = {
            "current_shares",
            "target_shares",
            "current_price",
            "sell_type",
            "sell_ratio",
            "first_sell_price",
            "second_sell_price",
            "third_sell_price",
        }
        self.assertFalse(forbidden & set(table.columns))
        self.assertIn("当前持有份额", table.columns)
        self.assertIn("建议卖出比例", table.columns)
        self.assertEqual(table.loc[0, "卖出类型"], "轮动调仓卖出")
        self.assertEqual(table.loc[0, "建议卖出比例"], "100%")
        self.assertEqual(table.loc[0, "第二卖出价"], "1.150")
        self.assertIn("触发价来源", table.columns)
        self.assertIn("触发价/当前价", table.columns)
        self.assertEqual(table.loc[0, "风控触发价"], "异常，已隐藏")
        self.assertNotIn("风控卖出价", table.to_string())

    def test_validate_trend_break_risk_limit_not_above_current_price(self) -> None:
        row = validate_sell_prices(
            {
                "symbol": "515050",
                "current_price": 1.156,
                "sell_type": "trend_break",
                "sell_ratio": 1.0,
                "suggested_sell_shares": 1000000,
                "risk_limit_price": 2.557,
            }
        )
        self.assertLessEqual(float(row["risk_limit_price"]), 1.156 * 1.02)
        self.assertEqual(row["first_sell_price"], 1.156)
        self.assertEqual(row["second_sell_price"], 1.150)
        self.assertEqual(row["third_sell_price"], 1.144)
        self.assertIn("风控挂单价异常，已按当前参考价修正。", row["warning"])

    def test_validate_stop_loss_risk_limit_not_above_current_price(self) -> None:
        row = validate_sell_prices(
            {
                "symbol": "515050",
                "current_price": 1.000,
                "sell_type": "stop_loss",
                "sell_ratio": 1.0,
                "suggested_sell_shares": 1000,
                "risk_limit_price": 1.500,
            }
        )
        self.assertLessEqual(float(row["risk_limit_price"]), 1.02)
        self.assertEqual(row["first_sell_price"], 1.000)
        self.assertIn("风控挂单价异常", row["warning"])

    def test_take_profit_prices_can_be_above_current_without_risk_sell_label(self) -> None:
        row = pd.Series(
            {
                "sell_execution_plan": json.dumps(
                    [
                        validate_sell_prices(
                            {
                                "symbol": "515050",
                                "name": "测试ETF",
                                "current_shares": 1000,
                                "current_price": 1.156,
                                "sell_type": "take_profit",
                                "sell_ratio": 0.3,
                                "suggested_sell_shares": 300,
                                "first_take_profit_price": 1.199,
                                "second_take_profit_price": 1.245,
                                "third_take_profit_price": 1.302,
                            }
                        )
                    ],
                    ensure_ascii=False,
                )
            }
        )
        table = parse_sell_execution_table(row)
        self.assertIn("第一止盈价", table.columns)
        self.assertNotIn("风控卖出价", table.columns)
        self.assertEqual(table.loc[0, "第一止盈价"], "1.199")
        self.assertEqual(table.loc[0, "卖出类型"], "止盈卖出")

    def test_hold_does_not_generate_sell_shares_or_prices(self) -> None:
        row = validate_sell_prices(
            {
                "symbol": "515050",
                "current_price": 1.156,
                "sell_type": "hold",
                "sell_ratio": 0.5,
                "suggested_sell_shares": 500,
                "first_sell_price": 1.156,
            }
        )
        self.assertEqual(row["suggested_sell_shares"], 0.0)
        self.assertEqual(row["sell_ratio"], 0.0)
        self.assertIsNone(row["first_sell_price"])

    def test_current_price_missing_skips_sell_execution_prices(self) -> None:
        row = validate_sell_prices(
            {
                "symbol": "515050",
                "current_price": None,
                "sell_type": "trend_break",
                "sell_ratio": 1.0,
                "suggested_sell_shares": 1000,
                "risk_limit_price": 2.557,
            }
        )
        self.assertIsNone(row["first_sell_price"])
        self.assertEqual(row["suggested_sell_shares"], 0.0)
        self.assertIn("价格缺失，不能生成卖出计划", row["风险提示"])

    def test_sell_execution_uses_symbol_aligned_current_prices(self) -> None:
        dates = pd.date_range("2026-01-01", periods=70)
        frame_a = pd.DataFrame({"close": [1.50] * 69 + [1.00], "high": [1.52] * 70, "low": [0.98] * 70}, index=dates)
        frame_b = pd.DataFrame({"close": [2.50] * 69 + [2.00], "high": [2.52] * 70, "low": [1.98] * 70}, index=dates)
        plan = build_sell_execution_plan(
            positions={
                "111111": {"name": "A", "shares": 1000, "average_buy_price": 1.0},
                "222222": {"name": "B", "shares": 1000, "average_buy_price": 2.0},
            },
            market_frames={"111111": frame_a, "222222": frame_b},
            target_symbols=set(),
            quote_map={
                "222222": {"latest_price": 2.0, "price_actionable": True},
                "111111": {"latest_price": 1.0, "price_actionable": True},
            },
        )
        by_symbol = {item["symbol"]: item for item in plan}
        self.assertEqual(by_symbol["111111"]["current_price"], 1.0)
        self.assertEqual(by_symbol["111111"]["first_sell_price"], 1.0)
        self.assertEqual(by_symbol["222222"]["current_price"], 2.0)
        self.assertEqual(by_symbol["222222"]["first_sell_price"], 2.0)

    def test_internal_fields_only_available_before_normal_display_mapping(self) -> None:
        raw = {
            "symbol": "515050",
            "name": "测试ETF",
            "current_shares": 1000,
            "current_price": 1.156,
            "sell_type": "trend_break",
            "sell_ratio": 0.5,
            "suggested_sell_shares": 500,
        }
        table = parse_sell_execution_table(pd.Series({"sell_execution_plan": json.dumps([raw], ensure_ascii=False)}))
        self.assertIn("current_shares", raw)
        self.assertNotIn("current_shares", table.columns)

    def test_trend_break_abnormal_risk_trigger_is_hidden(self) -> None:
        row = validate_risk_trigger_price(
            {
                "symbol": "515050",
                "current_price": 1.156,
                "sell_type": "trend_break",
                "risk_trigger_price": 2.557,
                "risk_trigger_source": "60日均线",
            }
        )
        self.assertIsNone(row["risk_trigger_price"])
        self.assertEqual(row["risk_trigger_display"], "异常，已隐藏")
        self.assertEqual(row["raw_risk_trigger_price"], 2.557)
        self.assertIn("偏离过大", row["risk_trigger_warning"])

    def test_abnormal_risk_trigger_not_shown_on_normal_page(self) -> None:
        raw = {
            "symbol": "515050",
            "name": "UC通信ETF华夏",
            "current_shares": 1000000,
            "current_price": 1.156,
            "sell_type": "trend_break",
            "sell_ratio": 1.0,
            "suggested_sell_shares": 1000000,
            "risk_trigger_price": 2.557,
            "risk_trigger_source": "60日均线",
        }
        table = parse_sell_execution_table(pd.Series({"sell_execution_plan": json.dumps([raw], ensure_ascii=False)}))
        self.assertEqual(table.loc[0, "风控触发价"], "异常，已隐藏")
        self.assertNotIn("2.557", table.to_string())

    def test_abnormal_risk_trigger_cannot_drive_trend_break_decision(self) -> None:
        dates = pd.date_range("2026-01-01", periods=70)
        frame = pd.DataFrame({"close": [3.0] * 69 + [1.156], "high": [3.05] * 69 + [1.17], "low": [2.95] * 69 + [1.14]}, index=dates)
        plan = build_sell_execution_plan(
            positions={"515050": {"name": "UC通信ETF华夏", "shares": 1000000, "average_buy_price": 1.142}},
            market_frames={"515050": frame},
            target_symbols={"515050"},
            quote_map={"515050": {"latest_price": 1.156, "price_actionable": True}},
        )
        self.assertEqual(plan[0]["sell_type"], "hold")
        self.assertEqual(plan[0]["suggested_sell_shares"], 0.0)
        self.assertEqual(plan[0]["risk_trigger_display"], "异常，已隐藏")
        self.assertEqual(plan[0]["risk_trigger_source"], "数据异常")

    def test_risk_trigger_must_include_source(self) -> None:
        row = validate_risk_trigger_price(
            {
                "symbol": "515050",
                "current_price": 1.156,
                "sell_type": "trend_break",
                "risk_trigger_price": 1.180,
                "risk_trigger_source": "60日均线",
            }
        )
        self.assertEqual(row["risk_trigger_source"], "60日均线")
        self.assertAlmostEqual(float(row["risk_trigger_ratio_to_current"]), 1.020761, places=5)

    def test_ma60_is_calculated_independently_by_symbol_for_risk_trigger(self) -> None:
        dates = pd.date_range("2026-01-01", periods=70)
        frame_a = pd.DataFrame({"close": [1.2] * 60 + [1.0] * 10, "high": [1.22] * 70, "low": [0.98] * 70}, index=dates)
        frame_b = pd.DataFrame({"close": [2.2] * 60 + [2.0] * 10, "high": [2.22] * 70, "low": [1.98] * 70}, index=dates)
        plan = build_sell_execution_plan(
            positions={
                "111111": {"name": "A", "shares": 1000, "average_buy_price": 1.0},
                "222222": {"name": "B", "shares": 1000, "average_buy_price": 2.0},
            },
            market_frames={"222222": frame_b, "111111": frame_a},
            target_symbols={"111111", "222222"},
            quote_map={
                "222222": {"latest_price": 2.0, "price_actionable": True},
                "111111": {"latest_price": 1.0, "price_actionable": True},
            },
        )
        by_symbol = {item["symbol"]: item for item in plan}
        self.assertAlmostEqual(float(by_symbol["111111"]["raw_risk_trigger_price"]), frame_a["close"].tail(60).mean(), places=3)
        self.assertAlmostEqual(float(by_symbol["222222"]["raw_risk_trigger_price"]), frame_b["close"].tail(60).mean(), places=3)

    def test_515050_abnormal_trigger_case_is_hidden(self) -> None:
        row = validate_sell_prices(
            {
                "symbol": "515050",
                "name": "UC通信ETF华夏",
                "current_price": 1.156,
                "sell_type": "trend_break",
                "sell_ratio": 1.0,
                "suggested_sell_shares": 1000000,
                "risk_trigger_price": 2.557,
                "risk_trigger_source": "60日均线",
            }
        )
        self.assertEqual(row["risk_trigger_display"], "异常，已隐藏")
        self.assertIsNone(row["risk_trigger_price"])
        self.assertEqual(row["sell_type"], "hold")
        self.assertIsNone(row["first_sell_price"])

    def test_price_basis_jump_marks_data_abnormal(self) -> None:
        dates = pd.date_range("2026-01-01", periods=70)
        frame = pd.DataFrame({"close": [3.0] * 69 + [1.156], "high": [3.05] * 69 + [1.17], "low": [2.95] * 69 + [1.14]}, index=dates)
        plan = build_sell_execution_plan(
            positions={"515050": {"name": "UC通信ETF华夏", "shares": 1000000, "average_buy_price": 1.142}},
            market_frames={"515050": frame},
            target_symbols={"515050"},
            quote_map={"515050": {"latest_price": 1.156, "price_actionable": True}},
        )
        self.assertFalse(plan[0]["price_basis_consistent"])
        self.assertEqual(plan[0]["risk_trigger_source"], "数据异常")
        self.assertIn("复权口径", plan[0]["price_basis_warning"])


if __name__ == "__main__":
    unittest.main()
