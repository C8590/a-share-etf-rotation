from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from app import build_etf_pool_view
from ui.components import localize_columns
from ui.signal_parser import DashboardData


def _dashboard_data(coverage: pd.DataFrame, rankings: pd.DataFrame) -> DashboardData:
    return DashboardData(
        overview={},
        signals=pd.DataFrame(),
        rankings=rankings,
        coverage=coverage,
        universe_raw=pd.DataFrame(),
        universe_snapshot=pd.DataFrame(),
        qa_report={},
        strategy_review=pd.DataFrame(),
        etf_names={},
        output_mtimes={},
    )


class EtfPoolUiReviewTest(unittest.TestCase):
    def test_nan_reason_is_replaced_in_pool_view(self) -> None:
        coverage = pd.DataFrame(
            [
                {"symbol": "510300", "name": "沪深300ETF", "status": "up_to_date", "success": True, "filter_reason": float("nan"), "latest_date": "2026-05-19"},
                {"symbol": "159999", "name": "样本ETF", "status": "up_to_date", "success": True, "filter_reason": None, "latest_date": "2026-05-19"},
            ]
        )
        rankings = pd.DataFrame([{"symbol": "510300", "selected": True}])

        view = build_etf_pool_view(_dashboard_data(coverage, rankings))

        self.assertNotIn("nan", set(view["reason"].astype(str).str.lower()))
        self.assertEqual(view.loc[view["symbol"] == "510300", "reason"].iloc[0], "纳入策略观察池")
        self.assertEqual(view.loc[view["symbol"] == "159999", "reason"].iloc[0], "不在当前策略观察池")

    def test_status_values_are_localized(self) -> None:
        localized = localize_columns(pd.DataFrame([{"status": "up_to_date"}, {"status": "outdated"}, {"status": "failed"}]))

        self.assertEqual(localized["状态"].tolist(), ["行情已是最新", "需要更新", "更新失败"])

    def test_excluded_wording_is_removed_from_app_copy(self) -> None:
        source = Path("app.py").read_text(encoding="utf-8")

        self.assertNotIn("被排除", source)
        self.assertIn("暂不参与本策略的 ETF 数", source)

    def test_candidate_pool_and_buy_plan_copy_are_distinct(self) -> None:
        source = Path("app.py").read_text(encoding="utf-8")

        self.assertIn("今日候选池", source)
        self.assertIn("候选池不等于买入计划", source)
        self.assertIn("买入动作", source)
        self.assertNotIn("今日 V2 候选池", source)

    def test_pool_view_uses_sector_mapping_fields(self) -> None:
        coverage = pd.DataFrame([{"symbol": "512480", "status": "up_to_date", "success": True, "latest_date": "2026-05-19"}])
        rankings = pd.DataFrame([{"symbol": "512480", "selected": True}])

        view = build_etf_pool_view(_dashboard_data(coverage, rankings))

        self.assertEqual(view.iloc[0]["name"], "国联安中证全指半导体ETF")
        self.assertEqual(view.iloc[0]["sector"], "科技成长")
        self.assertEqual(view.iloc[0]["theme"], "半导体")
        self.assertEqual(view.iloc[0]["risk_group"], "半导体")


if __name__ == "__main__":
    unittest.main()
