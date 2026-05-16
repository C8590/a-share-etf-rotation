from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import main
from data.downloader import DataStatus
from data.etf_metadata import (
    build_etf_metadata_coverage,
    fetch_etf_metadata,
    infer_etf_tags_from_name,
    normalize_etf_metadata,
    summarize_etf_metadata,
    update_etf_metadata,
    write_etf_metadata,
)
from data.schema import validate_output_file_schema


def _spot_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "代码": ["510300", "159915", "518880"],
            "名称": ["沪深300ETF华泰柏瑞", "创业板ETF易方达", "黄金ETF华安"],
            "最新价": [4.1, 2.2, 6.3],
            "成交额": [100000000.0, 80000000.0, 50000000.0],
            "数据日期": ["2026-05-14", "2026-05-14", "2026-05-14"],
        }
    )


def _fund_name_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "基金代码": ["510300", "159915", "518880"],
            "基金简称": ["沪深300ETF华泰柏瑞", "创业板ETF易方达", "黄金ETF华安"],
            "基金类型": ["ETF-场内", "ETF-场内", "商品型"],
        }
    )


class EtfMetadataTest(unittest.TestCase):
    def test_fetch_and_normalize_metadata(self) -> None:
        ak = SimpleNamespace(fund_etf_spot_em=_spot_frame, fund_name_em=_fund_name_frame)
        raw = fetch_etf_metadata(ak_module=ak)
        frame = normalize_etf_metadata(raw, updated_at="2026-05-14T12:00:00+08:00")
        self.assertEqual(len(frame), 3)
        self.assertEqual(frame.loc[frame["symbol"].eq("510300"), "exchange"].iloc[0], "SH")
        self.assertEqual(frame.loc[frame["symbol"].eq("159915"), "exchange"].iloc[0], "SZ")
        self.assertIn("akshare.fund_etf_spot_em", frame["metadata_source"].iloc[0])

    def test_missing_real_fields_are_not_fabricated(self) -> None:
        frame = normalize_etf_metadata(fetch_etf_metadata(ak_module=SimpleNamespace(fund_etf_spot_em=_spot_frame, fund_name_em=_fund_name_frame)))
        row = frame[frame["symbol"].eq("510300")].iloc[0]
        self.assertEqual(row["fund_company"], "unable_to_confirm")
        self.assertEqual(row["tracking_index_code"], "unable_to_confirm")
        self.assertEqual(row["management_fee"], "unable_to_confirm")
        self.assertEqual(row["asset_class"], "unknown")

    def test_name_inference_stays_in_inferred_fields(self) -> None:
        tags = infer_etf_tags_from_name("创业板人工智能ETF")
        self.assertEqual(tags["inferred_category"], "broad_based")
        self.assertIn("chinext", tags["inferred_tags"])
        frame = normalize_etf_metadata(fetch_etf_metadata(ak_module=SimpleNamespace(fund_etf_spot_em=_spot_frame, fund_name_em=_fund_name_frame)))
        row = frame[frame["symbol"].eq("159915")].iloc[0]
        self.assertIn("chinext", row["inferred_tags"])
        self.assertEqual(row["is_chinext"], "unknown")

    def test_coverage_ratio_and_importance(self) -> None:
        frame = normalize_etf_metadata(fetch_etf_metadata(ak_module=SimpleNamespace(fund_etf_spot_em=_spot_frame, fund_name_em=_fund_name_frame)))
        coverage = pd.DataFrame(build_etf_metadata_coverage(frame))
        symbol = coverage[coverage["field_name"].eq("symbol")].iloc[0]
        fund_company = coverage[coverage["field_name"].eq("fund_company")].iloc[0]
        inferred = coverage[coverage["field_name"].eq("inferred_tags")].iloc[0]
        self.assertEqual(float(symbol["coverage_ratio"]), 1.0)
        self.assertEqual(symbol["importance"], "required")
        self.assertEqual(float(fund_company["coverage_ratio"]), 0.0)
        self.assertEqual(fund_company["importance"], "recommended")
        self.assertEqual(inferred["importance"], "optional")

    def test_metadata_and_coverage_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = normalize_etf_metadata(fetch_etf_metadata(ak_module=SimpleNamespace(fund_etf_spot_em=_spot_frame, fund_name_em=_fund_name_frame)))
            metadata_path, coverage_path = write_etf_metadata(
                frame,
                metadata_path=Path(tmp) / "etf_metadata.csv",
                coverage_path=Path(tmp) / "etf_metadata_coverage.csv",
            )
            validate_output_file_schema(metadata_path, "etf_metadata")
            validate_output_file_schema(coverage_path, "etf_metadata_coverage")
            summary = summarize_etf_metadata(metadata_path, coverage_path)
            self.assertEqual(summary["total_etfs"], 3)
            self.assertIn("fund_company", summary["low_coverage_fields"])

    def test_summary_not_run_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = summarize_etf_metadata(Path(tmp) / "missing.csv", Path(tmp) / "missing_coverage.csv")
            self.assertEqual(summary["status"], "not_run")
            self.assertEqual(summary["total_etfs"], 0)

    def test_qa_report_etf_metadata_not_run_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                status = DataStatus(symbol="510300", name="ETF A", success=True, rows=3, status="passed")
                data_gate = SimpleNamespace(allow_formal=True, effective_etf_count=1, latest_date="2026-05-14", reasons=[], failure_summary=[])
                review = pd.DataFrame([{"strategy_name": "unit", "strategy_status": "recommended_for_observation"}])

                def touch_output(path: str, _builder: object) -> None:
                    output = Path(path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text("ok", encoding="utf-8")

                with (
                    patch.object(main, "load_etf_pool", return_value=[{"symbol": "510300", "name": "ETF A"}]),
                    patch.object(main, "audit_trading_calendar", return_value={"status": "ok"}),
                    patch.object(main, "build_data_coverage_report", return_value=[status]),
                    patch.object(main, "run_data_quality_checks", return_value=data_gate),
                    patch.object(main, "audit_cache_metadata", return_value=[]),
                    patch.object(main, "build_adjustment_audit", return_value=[]),
                    patch.object(main, "build_refresh_plan", return_value=[]),
                    patch.object(main, "_strategy_qa_rows", return_value=([], [])),
                    patch.object(main, "_ensure_output_file", side_effect=touch_output),
                    patch.object(main, "build_strategy_review", return_value=review),
                ):
                    main.command_qa_check()

                report = json.loads(Path("output/qa_report.json").read_text(encoding="utf-8"))
                self.assertIn("etf_metadata_report", report["data_layer"])
                self.assertEqual(report["data_layer"]["etf_metadata"]["status"], "not_run")
            finally:
                os.chdir(old_cwd)

    def test_update_etf_metadata_does_not_modify_price_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "data" / "cache"
            cache_dir.mkdir(parents=True)
            cache_file = cache_dir / "510300.csv"
            original = "date,open,high,low,close,volume,amount,symbol,name,source\n2024-01-02,1,1,1,1,1,1,510300,ETF,local_cache\n"
            cache_file.write_text(original, encoding="utf-8")
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                ak = SimpleNamespace(fund_etf_spot_em=_spot_frame, fund_name_em=_fund_name_frame)
                _frame, metadata_path, coverage_path = update_etf_metadata(ak_module=ak)
                self.assertTrue(metadata_path.exists())
                self.assertTrue(coverage_path.exists())
                self.assertEqual(cache_file.read_text(encoding="utf-8"), original)
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
