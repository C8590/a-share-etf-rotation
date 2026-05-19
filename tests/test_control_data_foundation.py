from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from signal.control_data_foundation import (
    SIGNAL_CASE_FIELDS,
    SIGNAL_CASE_REVIEW_FIELDS,
    V1_V2_COMPARISON_FIELDS,
    build_v1_v2_comparison_row,
    classify_hindsight,
    post_924_regime,
    write_signal_cases,
    write_v1_v2_comparison,
)


class ControlDataFoundationTest(unittest.TestCase):
    def _market_frame(self, closes: list[float], start: str = "2024-09-23") -> pd.DataFrame:
        return pd.DataFrame({"date": pd.bdate_range(start=start, periods=len(closes)), "close": closes})

    def test_signal_cases_csv_generates_required_fields_and_regime(self) -> None:
        pre_rows = [
            {
                "trade_date": "2024-09-23",
                "symbol": "159915",
                "name": "创业板ETF",
                "sector": "成长",
                "market_state": "均衡",
                "rank": 1,
                "score": 82.5,
                "selected": True,
                "reason": "入选候选",
            },
            {
                "trade_date": "2024-09-24",
                "symbol": "510300",
                "name": "沪深300ETF",
                "sector": "宽基",
                "market_state": "防守",
                "rank": 2,
                "score": 40.0,
                "selected": False,
                "reason": "市场状态限制",
            },
        ]
        entry_rows = [
            {
                "trade_date": "2024-09-23",
                "symbol": "159915",
                "name": "创业板ETF",
                "market_state": "均衡",
                "buy_action": "观察",
                "buy_price": "",
                "position_size": 0,
                "confidence": 0.32,
                "entry_reason": "趋势成熟度：启动期；买点质量：普通确认；理由：继续观察",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            rows = write_signal_cases(pre_rows, entry_rows, output_dir=tmp)
            saved = pd.read_csv(Path(tmp) / "signal_cases.csv", dtype=str).fillna("")

        self.assertEqual(list(saved.columns), list(SIGNAL_CASE_FIELDS))
        self.assertEqual(len(rows), 2)
        self.assertEqual(saved.iloc[0]["post_924_regime"], "pre_20240924")
        self.assertEqual(saved.iloc[1]["post_924_regime"], "post_20240924")
        self.assertEqual(saved.iloc[0]["trend_maturity"], "启动期")
        self.assertEqual(saved.iloc[0]["entry_quality"], "普通确认")
        self.assertEqual(saved.iloc[0]["hindsight_label"], "样本不足")

    def test_post_924_regime_boundary(self) -> None:
        self.assertEqual(post_924_regime("2024-09-23"), "pre_20240924")
        self.assertEqual(post_924_regime("2024-09-24"), "post_20240924")

    def test_hindsight_marks_missed_opportunity_when_watch_rises_with_small_drawdown(self) -> None:
        pre_rows = [
            {
                "trade_date": "2024-09-23",
                "symbol": "159915",
                "name": "创业板ETF",
                "sector": "成长",
                "market_state": "进攻",
                "rank": 1,
                "score": 80,
                "selected": True,
                "reason": "入选",
            }
        ]
        entry_rows = [
            {
                "trade_date": "2024-09-23",
                "symbol": "159915",
                "buy_action": "观察",
                "position_size": 0,
                "confidence": 0.32,
                "entry_reason": "趋势成熟度：启动期；买点质量：普通确认",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            write_signal_cases(
                pre_rows,
                entry_rows,
                output_dir=tmp,
                market_data={"159915": self._market_frame([1.00, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06, 1.05, 1.06, 1.07, 1.08])},
            )
            saved = pd.read_csv(Path(tmp) / "signal_cases.csv", dtype=str).fillna("")
            review = pd.read_csv(Path(tmp) / "signal_case_review.csv", dtype=str).fillna("")

        self.assertEqual(saved.iloc[0]["hindsight_label"], "可能错过机会")
        self.assertEqual(list(review.columns), list(SIGNAL_CASE_REVIEW_FIELDS))
        self.assertEqual(review.iloc[0]["missed_opportunity_count"], "1")

    def test_hindsight_marks_correct_observation_when_watch_falls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_signal_cases(
                [
                    {
                        "trade_date": "2024-09-24",
                        "symbol": "510300",
                        "name": "沪深300ETF",
                        "sector": "宽基",
                        "market_state": "均衡",
                        "rank": 1,
                        "score": 70,
                        "selected": True,
                        "reason": "入选",
                    }
                ],
                [
                    {
                        "trade_date": "2024-09-24",
                        "symbol": "510300",
                        "buy_action": "观察",
                        "position_size": 0,
                        "confidence": 0.32,
                        "entry_reason": "趋势成熟度：启动期；买点质量：普通确认",
                    }
                ],
                output_dir=tmp,
                market_data={"510300": self._market_frame([1.00, 0.99, 0.98, 0.97, 0.96, 0.95, 0.96], start="2024-09-24")},
            )
            saved = pd.read_csv(Path(tmp) / "signal_cases.csv", dtype=str).fillna("")

        self.assertEqual(saved.iloc[0]["hindsight_label"], "观察正确")
        self.assertEqual(saved.iloc[0]["post_924_regime"], "post_20240924")

    def test_hindsight_marks_avoided_chase_risk_after_rise_and_large_drawdown(self) -> None:
        label, reason = classify_hindsight(
            entry_action="观察",
            ret_5d=0.04,
            ret_10d=-0.02,
            max_gain_10d=0.08,
            max_drawdown_10d=-0.07,
        )

        self.assertEqual(label, "追高风险被避免")
        self.assertIn("回撤", reason)

    def test_hindsight_insufficient_future_data_does_not_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_signal_cases(
                [
                    {
                        "trade_date": "2026-05-18",
                        "symbol": "159915",
                        "name": "创业板ETF",
                        "sector": "成长",
                        "market_state": "进攻",
                        "rank": 1,
                        "score": 80,
                        "selected": True,
                        "reason": "入选",
                    }
                ],
                [{"trade_date": "2026-05-18", "symbol": "159915", "buy_action": "观察", "position_size": 0}],
                output_dir=tmp,
                market_data={"159915": self._market_frame([1.00, 1.01], start="2026-05-18")},
            )
            saved = pd.read_csv(Path(tmp) / "signal_cases.csv", dtype=str).fillna("")

        self.assertEqual(saved.iloc[0]["hindsight_label"], "样本不足")
        self.assertEqual(saved.iloc[0]["ret_5d"], "")

    def test_v1_v2_comparison_outputs_all_watch_no_buy_reason(self) -> None:
        modular_pipeline = {
            "pre_selection": [
                {
                    "trade_date": "2026-05-18",
                    "symbol": "159915",
                    "name": "创业板ETF",
                    "sector": "成长",
                    "market_state": "防守",
                    "selected": True,
                }
            ],
            "entry": [
                {
                    "trade_date": "2026-05-18",
                    "symbol": "159915",
                    "buy_action": "观察",
                    "position_size": 0,
                    "confidence": 0.2,
                    "entry_reason": "市场状态为防守，趋势成熟度：启动期；买点质量：普通确认；价格等待确认",
                }
            ],
        }
        v1_summary = {"target_symbols": "510300", "effective_signal_date": "2026-05-18"}
        v2_summary = {
            "effective_signal_date": "2026-05-18",
            "v2_market_state": "防守",
            "v2_selected_sectors": "成长",
        }

        row = build_v1_v2_comparison_row(v1_summary, v2_summary, modular_pipeline)

        self.assertEqual(set(row), set(V1_V2_COMPARISON_FIELDS))
        self.assertEqual(row["v2_actual_buy_etfs"], "无")
        self.assertIn("置信度不足", row["v2_no_buy_reason"])
        self.assertIn("趋势成熟度不足", row["v2_no_buy_reason"])
        self.assertIn("买点质量不足", row["v2_no_buy_reason"])
        self.assertIn("市场状态限制", row["v2_no_buy_reason"])
        self.assertIn("价格校验限制", row["v2_no_buy_reason"])

    def test_v1_v2_comparison_csv_generates(self) -> None:
        modular_pipeline = {
            "pre_selection": [{"trade_date": "2026-05-18", "symbol": "159915", "sector": "成长", "selected": True}],
            "entry": [{"trade_date": "2026-05-18", "symbol": "159915", "buy_action": "STANDARD_BUY", "position_size": 0.6}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            write_v1_v2_comparison(
                {"target_symbols": "159915", "effective_signal_date": "2026-05-18"},
                {"effective_signal_date": "2026-05-18", "v2_market_state": "进攻", "v2_selected_sectors": "成长"},
                modular_pipeline,
                output_dir=tmp,
            )
            saved = pd.read_csv(Path(tmp) / "v1_v2_comparison.csv", dtype=str).fillna("")

        self.assertEqual(list(saved.columns), list(V1_V2_COMPARISON_FIELDS))
        self.assertEqual(saved.iloc[0]["v2_actual_buy_etfs"], "159915")


if __name__ == "__main__":
    unittest.main()
