from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from contracts.signal_schema import EXIT_SIGNAL_FIELDS, MarketState, SellAction
from signal.exit.engine import ExitConfig, ExitEngine, OUTPUT_FILE


def _holding(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trade_date": "2026-05-18",
        "symbol": "510300",
        "name": "沪深300ETF",
        "sector": "宽基",
        "market_state": MarketState.BALANCED.value,
        "current_price": 1.0,
        "peak_price": 1.04,
        "trend_line": 0.95,
        "current_score": 70.0,
        "data_quality_passed": True,
        "liquidity_ok": True,
    }
    base.update(overrides)
    return base


def _run_one(holding: dict[str, object], **kwargs: object) -> dict[str, object]:
    engine = kwargs.pop("engine", ExitEngine())
    kwargs.setdefault("generated_at", "2026-05-18T15:30:00+08:00")
    with tempfile.TemporaryDirectory() as tmp:
        return engine.run([holding], output_dir=tmp, **kwargs)[0]


class ExitEngineTest(unittest.TestCase):
    def test_run_writes_contract_csv_fields(self) -> None:
        engine = ExitEngine()
        with tempfile.TemporaryDirectory() as tmp:
            rows = engine.run([_holding()], output_dir=tmp, generated_at="2026-05-18T15:30:00+08:00")
            output_path = Path(tmp) / OUTPUT_FILE
            self.assertTrue(output_path.exists())
            with output_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(tuple(reader.fieldnames or ()), EXIT_SIGNAL_FIELDS)
                csv_rows = list(reader)

        self.assertEqual(set(rows[0]), set(EXIT_SIGNAL_FIELDS))
        self.assertEqual(csv_rows[0]["sell_action"], SellAction.HOLD.value)
        self.assertIn("继续持有", csv_rows[0]["exit_reason"])

    def test_data_abnormal_clears_and_cools_down(self) -> None:
        row = _run_one(
            _holding(
                data_quality_passed=False,
                data_warning="成交额缺失，价格不可用",
            )
        )

        self.assertEqual(row["sell_action"], SellAction.CLEAR.value)
        self.assertEqual(row["reduce_ratio"], 1.0)
        self.assertEqual(row["cool_down_days"], 10)
        self.assertIn("数据异常退出", row["exit_reason"])
        self.assertIn("成交额缺失", row["exit_reason"])

    def test_market_defense_with_drawdown_reduces_half(self) -> None:
        row = _run_one(_holding(market_state=MarketState.DEFENSE.value, current_price=0.94, peak_price=1.0, trend_line=0.90))

        self.assertEqual(row["sell_action"], SellAction.REDUCE_HALF.value)
        self.assertEqual(row["reduce_ratio"], 0.5)
        self.assertIn("市场转防守", row["exit_reason"])

    def test_trend_line_break_clears_position(self) -> None:
        row = _run_one(_holding(current_price=0.93, trend_line=0.95, peak_price=0.96))

        self.assertEqual(row["sell_action"], SellAction.CLEAR.value)
        self.assertEqual(row["cool_down_days"], 5)
        self.assertIn("跌破趋势线", row["exit_reason"])

    def test_single_trend_decay_reduces_one_third(self) -> None:
        row = _run_one(_holding(acceleration_series=[0.02, -0.01, -0.03]))

        self.assertEqual(row["sell_action"], SellAction.REDUCE_ONE_THIRD.value)
        self.assertAlmostEqual(float(row["reduce_ratio"]), 1.0 / 3.0)
        self.assertIn("加速度连续 2 天转负", row["exit_reason"])

    def test_multiple_trend_decay_signals_reduce_half(self) -> None:
        row = _run_one(
            _holding(
                acceleration_negative_days=3,
                prev_sector_rank=1,
                sector_rank=5,
                prev_sector_breadth=0.70,
                sector_breadth=0.48,
            )
        )

        self.assertEqual(row["sell_action"], SellAction.REDUCE_HALF.value)
        self.assertIn("板块排名从第 1 名降至第 5 名", row["exit_reason"])
        self.assertIn("板块广度从 70% 降至 48%", row["exit_reason"])

    def test_replacement_requires_better_candidate_quality_and_different_sector(self) -> None:
        candidates = [
            {"symbol": "510500", "name": "中证500ETF", "sector": "宽基", "score": 95, "buy_action": "标准买入"},
            {"symbol": "159915", "name": "创业板ETF", "sector": "成长", "score": 92, "buy_quality": 0.72},
            {"symbol": "588000", "name": "科创ETF", "sector": "科技", "score": 99, "buy_quality": 0.30},
        ]

        row = _run_one(_holding(current_score=70), candidates=candidates)

        self.assertEqual(row["sell_action"], SellAction.REDUCE_HALF.value)
        self.assertIn("机会替换退出", row["exit_reason"])
        self.assertIn("创业板ETF", row["exit_reason"])
        self.assertNotIn("中证500ETF", row["exit_reason"])
        self.assertNotIn("科创ETF", row["exit_reason"])

    def test_replacement_plus_decay_clears_with_cooldown(self) -> None:
        candidates = [{"symbol": "159915", "name": "创业板ETF", "sector": "成长", "score": 92, "buy_action": "标准买入"}]
        row = _run_one(
            _holding(current_score=70, acceleration_negative_days=2),
            candidates=candidates,
        )

        self.assertEqual(row["sell_action"], SellAction.CLEAR.value)
        self.assertEqual(row["reduce_ratio"], 1.0)
        self.assertEqual(row["cool_down_days"], 5)
        self.assertIn("买点质量合格", row["exit_reason"])

    def test_cooldown_action_blocks_new_exit_evaluation(self) -> None:
        row = _run_one(_holding(cooldown_remaining=3, current_price=0.8, peak_price=1.0, trend_line=0.95))

        self.assertEqual(row["sell_action"], SellAction.COOL_DOWN.value)
        self.assertEqual(row["reduce_ratio"], 0.0)
        self.assertEqual(row["cool_down_days"], 3)
        self.assertIn("仍处于冷却期", row["exit_reason"])

    def test_thresholds_are_configurable(self) -> None:
        engine = ExitEngine(ExitConfig(max_drawdown_pct=0.03, hard_drawdown_pct=0.20))
        row = _run_one(_holding(current_price=0.96, peak_price=1.0, trend_line=0.90), engine=engine)

        self.assertEqual(row["sell_action"], SellAction.REDUCE_HALF.value)
        self.assertIn("个体回撤 4.0%", row["exit_reason"])


if __name__ == "__main__":
    unittest.main()
