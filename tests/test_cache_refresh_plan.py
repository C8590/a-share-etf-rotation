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
from data.cache_refresh import (
    build_refresh_plan,
    classify_refresh_candidate,
    compare_cache_before_after,
    summarize_refresh_plan,
    write_refresh_plan,
)
from data.downloader import DataStatus


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


class CacheRefreshPlanTest(unittest.TestCase):
    def test_missing_cache_is_p0_missing_cache(self) -> None:
        row = classify_refresh_candidate(
            {
                "cache_exists": False,
                "metadata_status": "error_missing_cache",
                "metadata_exists": False,
                "current_adjust": "",
                "primary_failure_type": "download_failed",
            }
        )
        self.assertEqual(row["refresh_priority"], "P0_missing_cache")
        self.assertIn("missing_cache", row["refresh_reason"])
        self.assertIn("download_failed", row["refresh_reason"])
        self.assertFalse(row["requires_backup"])

    def test_legacy_cache_without_metadata_is_p1_legacy_unknown(self) -> None:
        row = classify_refresh_candidate(
            {
                "cache_exists": True,
                "metadata_exists": False,
                "metadata_status": "warning_legacy_cache_without_metadata",
                "current_adjust": "unknown",
                "adjustment_audit_status": "warning_unknown_adjustment",
            }
        )
        self.assertEqual(row["refresh_priority"], "P1_legacy_unknown_adjustment")
        self.assertIn("legacy_cache_without_metadata", row["refresh_reason"])
        self.assertTrue(row["requires_backup"])
        self.assertTrue(row["safe_to_auto_refresh"])

    def test_stale_end_date_is_p0_stale(self) -> None:
        row = classify_refresh_candidate(
            {
                "cache_exists": True,
                "metadata_exists": True,
                "current_adjust": "qfq",
                "failure_types": "stale_end_date",
                "end_date_gap_days": 14,
            }
        )
        self.assertEqual(row["refresh_priority"], "P0_stale_end_date")
        self.assertIn("stale_end_date", row["refresh_reason"])
        self.assertTrue(row["requires_backup"])

    def test_data_quality_failed_is_p0_quality_failed(self) -> None:
        row = classify_refresh_candidate(
            {
                "cache_exists": True,
                "metadata_exists": True,
                "current_adjust": "qfq",
                "quality_failed": True,
                "primary_failure_type": "invalid_ohlc",
            }
        )
        self.assertEqual(row["refresh_priority"], "P0_quality_failed")
        self.assertIn("data_quality_failed", row["refresh_reason"])
        self.assertTrue(row["requires_manual_review"])
        self.assertFalse(row["safe_to_auto_refresh"])

    def test_possible_adjustment_issue_is_p1_and_not_auto_safe(self) -> None:
        row = classify_refresh_candidate(
            {
                "cache_exists": True,
                "metadata_exists": False,
                "current_adjust": "unknown",
                "possible_adjustment_issue": True,
            }
        )
        self.assertEqual(row["refresh_priority"], "P1_possible_adjustment_issue")
        self.assertIn("possible_adjustment_issue", row["refresh_reason"])
        self.assertTrue(row["requires_manual_review"])
        self.assertFalse(row["safe_to_auto_refresh"])

    def test_build_refresh_plan_distinguishes_candidate_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            cache = root / "cache"
            meta = root / "meta"
            cache.mkdir()
            (cache / "510300.csv").write_text("date,close\n2026-05-12,10\n", encoding="utf-8")
            (cache / "588000.csv").write_text("date,close\n2026-05-12,10\n", encoding="utf-8")
            _write_csv(
                output / "cache_metadata_audit.csv",
                [
                    {
                        "symbol": "159032",
                        "name": "Missing",
                        "cache_file": str(cache / "159032.csv"),
                        "metadata_file": str(meta / "159032.json"),
                        "metadata_exists": False,
                        "source": "",
                        "adjust": "",
                        "status": "error_missing_cache",
                    },
                    {
                        "symbol": "510300",
                        "name": "Legacy",
                        "cache_file": str(cache / "510300.csv"),
                        "metadata_file": str(meta / "510300.json"),
                        "metadata_exists": False,
                        "source": "",
                        "adjust": "unknown",
                        "status": "warning_legacy_cache_without_metadata",
                    },
                    {
                        "symbol": "588000",
                        "name": "Issue",
                        "cache_file": str(cache / "588000.csv"),
                        "metadata_file": str(meta / "588000.json"),
                        "metadata_exists": False,
                        "source": "",
                        "adjust": "unknown",
                        "status": "warning_legacy_cache_without_metadata",
                    },
                ],
            )
            _write_csv(
                output / "adjustment_audit.csv",
                [
                    {"symbol": "510300", "name": "Legacy", "adjust": "unknown", "end_date": "2026-05-12", "audit_status": "warning_unknown_adjustment", "possible_adjustment_issue": False},
                    {"symbol": "588000", "name": "Issue", "adjust": "unknown", "end_date": "2026-05-12", "audit_status": "warning_abnormal_return", "possible_adjustment_issue": True},
                ],
            )
            _write_csv(
                output / "data_quality_report.csv",
                [
                    {"symbol": "159032", "name": "Missing", "status": "failed", "end_date": "", "primary_failure_type": "download_failed"},
                    {"symbol": "510300", "name": "Legacy", "status": "passed", "end_date": "2026-05-12", "primary_failure_type": ""},
                    {"symbol": "588000", "name": "Issue", "status": "failed", "end_date": "2026-05-12", "primary_failure_type": "abnormal_return"},
                ],
            )
            _write_csv(
                output / "data_failure_summary.csv",
                [
                    {"symbol": "159032", "failure_type": "download_failed", "end_date_gap_days": 0, "latest_expected_date": "2026-05-14"},
                    {"symbol": "510300", "failure_type": "stale_end_date", "end_date_gap_days": 14, "latest_expected_date": "2026-05-14"},
                    {"symbol": "588000", "failure_type": "abnormal_return", "end_date_gap_days": 0, "latest_expected_date": "2026-05-14"},
                ],
            )
            rows = build_refresh_plan(
                [
                    {"symbol": "159032", "name": "Missing"},
                    {"symbol": "510300", "name": "Legacy"},
                    {"symbol": "588000", "name": "Issue"},
                ],
                output_dir=output,
                cache_dir=cache,
                cache_meta_dir=meta,
            )
            by_symbol = {row["symbol"]: row for row in rows}
            self.assertEqual(by_symbol["159032"]["refresh_priority"], "P0_missing_cache")
            self.assertEqual(by_symbol["510300"]["refresh_priority"], "P0_stale_end_date")
            self.assertEqual(by_symbol["588000"]["refresh_priority"], "P0_quality_failed")
            self.assertIn("possible_adjustment_issue", by_symbol["588000"]["refresh_reason"])
            self.assertGreater(len({row["refresh_reason"] for row in rows}), 1)

    def test_dry_run_write_does_not_modify_cache_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_file = root / "cache" / "510300.csv"
            cache_file.parent.mkdir()
            original = "date,close\n2026-05-12,10\n"
            cache_file.write_text(original, encoding="utf-8")
            rows = [
                {
                    "symbol": "510300",
                    "name": "Legacy",
                    "refresh_reason": "legacy_cache_without_metadata",
                    "refresh_priority": "P1_legacy_unknown_adjustment",
                }
            ]
            write_refresh_plan(rows, root / "output" / "cache_refresh_plan.csv")
            self.assertEqual(cache_file.read_text(encoding="utf-8"), original)

    def test_compare_cache_before_after(self) -> None:
        before = pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "close": [10.0, 10.0]})
        after = pd.DataFrame({"date": ["2024-01-02", "2024-01-03", "2024-01-04"], "close": [10.0, 10.2, 10.3]})
        diff = compare_cache_before_after(before, after)
        self.assertEqual(diff["row_count_delta"], 1)
        self.assertEqual(diff["overlap_row_count"], 2)
        self.assertGreater(diff["max_close_abs_diff"], 0)

    def test_qa_report_contains_cache_refresh_plan_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                status = DataStatus(symbol="510300", name="ETF A", success=True, rows=3, status="passed")
                data_gate = SimpleNamespace(
                    allow_formal=True,
                    effective_etf_count=1,
                    latest_date="2026-05-14",
                    reasons=[],
                    failure_summary=[],
                )
                refresh_rows = [
                    {
                        "symbol": "510300",
                        "name": "ETF A",
                        "refresh_reason": "legacy_cache_without_metadata",
                        "refresh_priority": "P1_legacy_unknown_adjustment",
                        "recommended_action": "batch pilot refresh to create metadata sidecar",
                        "requires_manual_review": False,
                        "safe_to_auto_refresh": True,
                    }
                ]
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
                    patch.object(main, "build_refresh_plan", return_value=refresh_rows),
                    patch.object(main, "_strategy_qa_rows", return_value=([], [])),
                    patch.object(main, "_ensure_output_file", side_effect=touch_output),
                    patch.object(main, "build_strategy_review", return_value=review),
                ):
                    main.command_qa_check()

                report = json.loads(Path("output/qa_report.json").read_text(encoding="utf-8"))
                self.assertIn("cache_refresh_plan_report", report["data_layer"])
                self.assertIn("cache_refresh_plan", report["data_layer"])
                self.assertEqual(report["data_layer"]["cache_refresh_plan"]["total_candidates"], 1)
            finally:
                os.chdir(old_cwd)

    def test_summarize_refresh_plan_counts(self) -> None:
        rows = [
            {"symbol": "159032", "name": "A", "refresh_reason": "missing_cache;download_failed", "refresh_priority": "P0_missing_cache", "recommended_action": "x", "requires_manual_review": True, "safe_to_auto_refresh": False},
            {"symbol": "510300", "name": "B", "refresh_reason": "legacy_cache_without_metadata;unknown_adjustment", "refresh_priority": "P1_legacy_unknown_adjustment", "recommended_action": "x", "requires_manual_review": False, "safe_to_auto_refresh": True},
        ]
        summary = summarize_refresh_plan(rows)
        self.assertEqual(summary["total_candidates"], 2)
        self.assertEqual(summary["reason_counts"]["missing_cache"], 1)
        self.assertEqual(summary["safe_to_auto_refresh_count"], 1)


if __name__ == "__main__":
    unittest.main()
