from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from contracts.signal_schema import BuyAction, MarketState
from signal.entry.engine import EntryEngine, OUTPUT_FILE, REQUIRED_OUTPUT_FIELDS


def _row(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "trade_date": "2026-05-18",
        "symbol": "510300",
        "name": "沪深300ETF",
        "sector": "宽基权益",
        "market_state": MarketState.BALANCED.value,
        "score": 82,
        "rank": 1,
        "selected": True,
        "reason": "动量排名靠前",
        "generated_at": "2026-05-18T15:10:00",
        "close": 4.2,
    }
    data.update(overrides)
    return data


class EntryEngineTest(unittest.TestCase):
    def test_run_writes_contract_fields_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows = EntryEngine(generated_at="2026-05-18T16:00:00").run(
                [
                    _row(
                        market_state=MarketState.BALANCED.value,
                        score=78,
                        momentum_20=0.04,
                        momentum_60=0.06,
                        breakout_confirmed=True,
                    )
                ],
                output_dir=tmp,
            )
            output_path = Path(tmp) / OUTPUT_FILE
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                written = list(csv.DictReader(file))

        self.assertEqual(list(rows[0].keys()), list(REQUIRED_OUTPUT_FIELDS))
        self.assertEqual(list(written[0].keys()), list(REQUIRED_OUTPUT_FIELDS))
        self.assertEqual(rows[0]["buy_action"], BuyAction.STANDARD_BUY.value)
        self.assertEqual(rows[0]["buy_price"], "4.200")
        self.assertIn("理由：", rows[0]["entry_reason"])
        self.assertIn("警示：", rows[0]["entry_reason"])

    def test_defensive_market_forbids_active_equity_buy(self) -> None:
        rows = EntryEngine(generated_at="fixed").run(
            [
                _row(
                    market_state=MarketState.DEFENSE.value,
                    score=92,
                    momentum_20=0.08,
                    momentum_60=0.12,
                    pullback_confirmed=True,
                )
            ],
            output_dir=tempfile.mkdtemp(),
        )

        self.assertEqual(rows[0]["buy_action"], BuyAction.FORBID_BUY.value)
        self.assertEqual(rows[0]["position_size"], 0.0)
        self.assertIn("市场状态为防守", rows[0]["entry_reason"])
        self.assertIn("禁止新开权益仓位", rows[0]["entry_reason"])

    def test_main_uptrend_pullback_can_add_to_target_weight(self) -> None:
        row = EntryEngine(first_buy_weight=0.25, target_weight=0.80, generated_at="fixed").run(
            [
                _row(
                    market_state=MarketState.ATTACK.value,
                    score=92,
                    momentum_20=0.08,
                    momentum_60=0.13,
                    momentum_120=0.10,
                    days_above_ma60=30,
                    pullback_confirmed=True,
                    distance_ma20=0.01,
                )
            ],
            output_dir=tempfile.mkdtemp(),
        )[0]

        self.assertEqual(row["buy_action"], BuyAction.ADD_BUY.value)
        self.assertEqual(row["position_size"], 0.8)
        self.assertIn("趋势成熟度：主升期", row["entry_reason"])
        self.assertIn("买点质量：回踩确认", row["entry_reason"])
        self.assertIn("目标权重：80%", row["entry_reason"])

    def test_overheated_trend_waits_for_pullback_without_heavy_new_position(self) -> None:
        row = EntryEngine(first_buy_weight=0.30, target_weight=0.90, generated_at="fixed").run(
            [
                _row(
                    market_state=MarketState.ATTACK.value,
                    score=94,
                    momentum_20=0.12,
                    momentum_60=0.18,
                    momentum_120=0.11,
                    distance_ma20=0.085,
                    pct_chg=0.03,
                    consecutive_up_days=3,
                )
            ],
            output_dir=tempfile.mkdtemp(),
        )[0]

        self.assertEqual(row["buy_action"], BuyAction.WAIT_PULLBACK.value)
        self.assertEqual(row["position_size"], 0.0)
        self.assertIn("趋势成熟度：过热期", row["entry_reason"])
        self.assertIn("不允许新开重仓", row["entry_reason"])

    def test_forbid_chasing_after_excessive_surge(self) -> None:
        row = EntryEngine(generated_at="fixed").run(
            [
                _row(
                    market_state=MarketState.ATTACK.value,
                    score=96,
                    momentum_20=0.18,
                    momentum_60=0.16,
                    distance_ma20=0.12,
                    pct_chg=0.075,
                )
            ],
            output_dir=tempfile.mkdtemp(),
        )[0]

        self.assertEqual(row["buy_action"], BuyAction.FORBID_BUY.value)
        self.assertEqual(row["position_size"], 0.0)
        self.assertIn("买点质量：禁止追高", row["entry_reason"])

    def test_reads_pre_selection_csv_when_rows_are_not_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "pre_selection_result.csv"
            with input_path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(_row().keys()))
                writer.writeheader()
                writer.writerow(_row(selected=False, score=55))

            rows = EntryEngine(generated_at="fixed").run(output_dir=tmp)

        self.assertEqual(rows[0]["buy_action"], BuyAction.WATCH.value)
        self.assertIn("未进入预选候选池", rows[0]["entry_reason"])


if __name__ == "__main__":
    unittest.main()
