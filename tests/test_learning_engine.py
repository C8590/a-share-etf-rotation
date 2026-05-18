from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from contracts.signal_schema import LEARNING_REPORT_FIELDS
from signal.learning.engine import HEALTH_LEVELS, LearningEngine


class LearningEngineTest(unittest.TestCase):
    def test_learning_report_uses_contract_fields_and_writes_csv(self) -> None:
        engine = LearningEngine()
        engine.record_buy_snapshot(
            "T-001",
            {
                "market_state": "进攻",
                "momentum_stage": "early",
                "rank": 1,
                "sector": "科技",
            },
        )
        engine.record_sell_snapshot("T-001", {"market_state": "进攻", "reason": "分批止盈"})

        closed_trades = [
            {
                "trade_id": "T-001",
                "symbol": "159915",
                "name": "创业板ETF",
                "buy_date": "2026-05-01",
                "sell_date": "2026-05-12",
                "buy_price": 1.0,
                "sell_price": 1.08,
                "shares": 1000,
                "holding_days": 7,
                "buy_future_prices": [1.02, 1.03, 1.04, 1.06, 1.07, 1.08, 1.09, 1.10, 1.11, 1.12],
                "sell_future_prices": [1.09, 1.10, 1.12, 1.11, 1.13, 1.14, 1.15, 1.15, 1.16, 1.17],
                "source_file": "unit-test",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            rows = engine.run(closed_trades, output_dir=tmp)
            report_path = Path(tmp) / "learning_report.csv"
            self.assertTrue(report_path.exists())
            with report_path.open(encoding="utf-8-sig", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))

        self.assertEqual(list(rows[0].keys()), list(LEARNING_REPORT_FIELDS))
        self.assertEqual(list(csv_rows[0].keys()), list(LEARNING_REPORT_FIELDS))
        self.assertEqual(rows[0]["trade_id"], "T-001")
        self.assertEqual(rows[0]["failure_attribution"], "卖早")
        self.assertEqual(rows[0]["return_pct"], 0.08)
        self.assertIn("买后收益：1日2.00%", rows[0]["lesson"])
        self.assertIn("3日4.00%", rows[0]["lesson"])
        self.assertIn("5日7.00%", rows[0]["lesson"])
        self.assertIn("10日12.00%", rows[0]["lesson"])
        self.assertIn("卖后走势：1日0.93%", rows[0]["lesson"])
        self.assertIn("买入快照", rows[0]["lesson"])
        self.assertIn("策略健康度", rows[0]["adjustment"])
        self.assertIn("仅给出建议，不自动修改交易参数", rows[0]["adjustment"])

    def test_failure_attribution_prefers_market_defense_for_losing_trade(self) -> None:
        engine = LearningEngine()
        rows = engine.run(
            [
                {
                    "trade_id": "T-002",
                    "symbol": "510300",
                    "name": "沪深300ETF",
                    "buy_date": "2026-05-01",
                    "sell_date": "2026-05-10",
                    "buy_price": 1.0,
                    "sell_price": 0.95,
                    "shares": 1000,
                    "holding_days": 6,
                    "sell_market_state": "防守",
                    "buy_future_prices": [0.995, 0.99, 0.985, 0.98, 0.975],
                    "sell_future_prices": [0.948, 0.946, 0.944],
                }
            ],
            output_dir=None,
        )

        self.assertEqual(rows[0]["failure_attribution"], "市场转防守")
        self.assertIn("增强市场状态过滤", rows[0]["adjustment"])

    def test_late_stage_and_poor_entry_are_classified_from_snapshots_and_forward_returns(self) -> None:
        engine = LearningEngine()
        rows = engine.run(
            [
                {
                    "trade_id": "T-003",
                    "symbol": "588000",
                    "name": "科创ETF",
                    "buy_date": "2026-05-01",
                    "sell_date": "2026-05-20",
                    "buy_price": 1.0,
                    "sell_price": 0.98,
                    "shares": 1000,
                    "holding_days": 12,
                    "buy_snapshot": {"momentum_stage": "late"},
                    "buy_future_prices": [0.99, 0.985, 0.975],
                    "sell_future_prices": [0.975, 0.97, 0.965],
                },
                {
                    "trade_id": "T-004",
                    "symbol": "515050",
                    "name": "通信ETF",
                    "buy_date": "2026-05-01",
                    "sell_date": "2026-05-08",
                    "buy_price": 1.0,
                    "sell_price": 0.99,
                    "shares": 1000,
                    "holding_days": 5,
                    "buy_future_prices": [0.97, 0.965, 0.96],
                    "sell_future_prices": [0.99, 0.988, 0.985],
                },
            ],
            output_dir=None,
        )

        self.assertEqual(rows[0]["failure_attribution"], "买在尾段")
        self.assertEqual(rows[1]["failure_attribution"], "买点太差")

    def test_strategy_health_can_become_failed_but_remains_advice_only(self) -> None:
        engine = LearningEngine()
        rows = engine.run(
            [
                {
                    "trade_id": f"T-00{i}",
                    "symbol": f"51030{i}",
                    "name": "ETF",
                    "buy_date": "2026-05-01",
                    "sell_date": "2026-05-12",
                    "buy_price": 1.0,
                    "sell_price": 0.9,
                    "shares": 1000,
                    "holding_days": 8,
                    "sell_future_prices": [0.89, 0.88, 0.87],
                }
                for i in range(3)
            ],
            output_dir=None,
        )

        self.assertTrue(any(level in rows[0]["lesson"] for level in HEALTH_LEVELS))
        self.assertIn("策略健康度：失效", rows[0]["adjustment"])
        self.assertIn("仅给出建议，不自动修改交易参数", rows[0]["adjustment"])

    def test_empty_input_still_writes_header_only_report(self) -> None:
        engine = LearningEngine()
        with tempfile.TemporaryDirectory() as tmp:
            rows = engine.run(output_dir=tmp)
            report_path = Path(tmp) / "learning_report.csv"
            with report_path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader)
                remaining = list(reader)

        self.assertEqual(rows, [])
        self.assertEqual(header, list(LEARNING_REPORT_FIELDS))
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
