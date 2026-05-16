from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import main
from data.index_readiness import (
    build_007b_readiness_check,
    build_index_unlock_plan,
    classify_007b_blocker,
    summarize_007b_readiness,
    write_007b_readiness_report,
)
from data.schema import validate_output_file_schema


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _base_qa_report(output: Path) -> None:
    report = {
        "schema_version": "1.0",
        "data_schema_version": "1.0",
        "data_layer": {
            "passed": False,
            "effective_etf_count": 2,
            "latest_date": "2026-05-13",
            "reasons": ["data quality failed for 1 ETF(s)"],
            "coverage_report": "output/data_coverage_report.csv",
            "quality_report": "output/data_quality_report.csv",
            "trading_calendar_report": "output/trading_calendar_audit.csv",
            "trading_calendar": {
                "calendar_file": "data/calendar/a_share_trading_calendar.csv",
                "status": "ok",
                "source": "unit",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "latest_open_day": "2026-05-15",
                "coverage_gap_days": 0,
                "used_fallback": False,
                "reason": "unit",
            },
            "failure_summary_report": "output/data_failure_summary.csv",
            "failure_summary": {"total_failed": 0, "failure_type_counts": {}, "severe_failed": 0, "warning_failed": 0, "top_examples": []},
            "cache_metadata_audit_report": "output/cache_metadata_audit.csv",
            "cache_metadata_audit": {
                "total_cache_files": 0,
                "metadata_exists_count": 0,
                "legacy_cache_without_metadata_count": 0,
                "unknown_adjustment_count": 0,
                "metadata_cache_mismatch_count": 0,
                "top_examples": [],
            },
            "adjustment_audit_report": "output/adjustment_audit.csv",
            "adjustment_audit": {
                "total_checked": 0,
                "unknown_adjustment_count": 0,
                "fallback_used_count": 0,
                "abnormal_return_symbols": [],
                "possible_adjustment_issue_count": 0,
                "top_examples": [],
            },
            "index_data": {
                "status": "ok",
                "index_map_report": "output/index_map.csv",
                "index_data_coverage_report": "output/index_data_coverage.csv",
                "total_index_mappings": 2,
                "index_cache_written_count": 0,
                "usable_benchmark_count": 0,
                "fetch_success_count": 0,
                "fetch_failed_count": 1,
                "csindex_success_count": 0,
                "eastmoney_failure_count": 1,
                "schema_invalid_count": 1,
                "manual_review_required_count": 1,
                "low_coverage_indexes": ["000300"],
                "top_examples": [],
            },
            "etf_metrics": {
                "status": "ok",
                "etf_metrics_report": "output/etf_metrics.csv",
                "etf_metrics_coverage_report": "output/etf_metrics_coverage.csv",
                "total_etfs": 2,
                "metrics_computable_count": 0,
                "tracking_error_computable_count": 0,
                "relative_return_computable_count": 0,
                "discount_premium_available_count": 0,
                "no_index_cache_count": 2,
                "missing_benchmark_count": 0,
                "insufficient_overlap_count": 0,
                "source_unavailable_count": 0,
                "top_examples": [],
            },
        },
        "strategy_layer": {"passed": True},
        "output_layer": {"passed": True},
        "allow_small_observation": False,
        "blocking_reasons": ["unit"],
        "recommended_for_observation": [],
        "not_recommended": [],
        "defensive_only": [],
        "risk_note": "unit",
    }
    (output / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _base_status(output: Path) -> None:
    status = {
        "generated_at": "2026-05-16T00:00:00+08:00",
        "qa_exit_status": "failed",
        "data_quality_failed_count": 1,
        "end_date_coverage_gap_days": 0,
        "candidate_total": 2,
        "candidate_eligible_count": 0,
        "candidate_blocked_count": 2,
        "blocked_short_history_count": 1,
        "blocked_manual_review_count": 0,
        "blocked_no_used_factors_count": 0,
        "observation_pool_count": 1,
        "very_short_history_count": 0,
        "estimated_eligible_within_20d_count": 0,
        "estimated_eligible_within_60d_count": 0,
        "manual_review_count": 0,
        "factor_gate_status": "blocked_for_strategy_use",
        "allowed_to_enter_008b": False,
        "allowed_to_enter_007b": False,
        "next_recommended_action": "unit",
        "blocking_reasons": ["unit"],
        "report_paths": {},
    }
    (output / "data_governance_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_fixture_reports(root: Path, *, fake_benchmark: bool = False) -> None:
    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    _base_qa_report(output)
    _base_status(output)
    _write_csv(
        output / "index_map.csv",
        [
            {
                "symbol": "510300",
                "etf_name": "沪深300ETF",
                "category": "ETF",
                "sub_category": "指数型-股票",
                "tracking_index_name": "沪深300",
                "tracking_index_code": "000300",
                "index_source": "config/index_map.yaml",
                "mapping_method": "config_manual",
                "confidence": 0.95,
                "requires_manual_review": False,
                "usable_as_benchmark": True,
                "notes": "unit",
            },
            {
                "symbol": "159999",
                "etf_name": "名称推断ETF",
                "category": "ETF",
                "sub_category": "指数型-股票",
                "tracking_index_name": "推断指数",
                "tracking_index_code": "399999",
                "index_source": "name_inference",
                "mapping_method": "name_inferred",
                "confidence": 0.6,
                "requires_manual_review": True,
                "usable_as_benchmark": False,
                "notes": "not hard",
            },
        ],
    )
    _write_csv(
        output / "index_data_coverage.csv",
        [
            {
                "tracking_index_code": "000300",
                "tracking_index_name": "沪深300",
                "index_source": "akshare.stock_zh_index_hist_csindex",
                "api_name": "akshare.stock_zh_index_hist_csindex",
                "source_family": "csindex",
                "fetch_success": False,
                "schema_valid": False,
                "start_date": "",
                "end_date": "",
                "row_count": 0,
                "latest_expected_date": "2026-05-14",
                "end_date_gap_days": 0,
                "missing_required_columns": "",
                "missing_values_count": 0,
                "duplicate_dates_count": 0,
                "abnormal_return_count": 0,
                "quality_status": "failed",
                "usable_as_benchmark": False,
                "requires_manual_review": True,
                "failure_reason": "HTTPSConnectionPool proxy_error",
                "notes": "eastmoney_failures=1",
            }
        ],
    )
    _write_csv(
        output / "index_source_diagnostics.csv",
        [
            {
                "run_id": "unit",
                "checked_at": "2026-05-16T00:00:00+08:00",
                "index_code": "000300",
                "index_name": "沪深300",
                "api_name": "akshare.index_zh_a_hist",
                "source_family": "eastmoney",
                "call_success": False,
                "status_code": "",
                "row_count": 0,
                "start_date": "",
                "end_date": "",
                "latest_expected_date": "2026-05-14",
                "end_date_gap_days": 0,
                "schema_valid": False,
                "missing_required_columns": "",
                "missing_values_count": 0,
                "duplicate_dates_count": 0,
                "abnormal_return_count": 0,
                "failure_type": "proxy_error",
                "failure_reason": "Unable to connect to proxy",
                "elapsed_ms": 1,
                "usable_as_index_source": False,
                "requires_manual_review": True,
                "suggested_action": "fix proxy",
                "notes": "unit",
            }
        ],
    )
    _write_csv(
        output / "etf_metrics.csv",
        [
            {
                "symbol": "510300",
                "name": "沪深300ETF",
                "category": "ETF",
                "sub_category": "指数型-股票",
                "tracking_index_code": "000300",
                "tracking_index_name": "沪深300",
                "benchmark_available": fake_benchmark,
                "benchmark_status": "ok" if fake_benchmark else "no_index_cache",
                "metric_status": "unable_to_compute",
                "tracking_error": "",
                "tracking_error_status": "no_index_cache",
                "relative_return_20d": "",
                "relative_return_60d": "",
                "relative_return_120d": "",
                "benchmark_return_20d": "",
                "benchmark_return_60d": "",
                "benchmark_return_120d": "",
                "etf_return_20d": "",
                "etf_return_60d": "",
                "etf_return_120d": "",
                "discount_premium": "",
                "discount_premium_status": "source_unavailable",
                "fund_size": "",
                "management_fee": "",
                "custody_fee": "",
                "latest_amount": "",
                "computed_at": "2026-05-16T00:00:00+08:00",
                "data_start_date": "",
                "data_end_date": "",
                "benchmark_start_date": "",
                "benchmark_end_date": "",
                "failure_reason": "missing index cache",
                "notes": "unit",
            }
        ],
    )
    _write_csv(
        output / "etf_metrics_coverage.csv",
        [
            {"metric_name": "tracking_error", "total_count": 2, "computable_count": 0, "unable_count": 2, "coverage_ratio": 0.0, "main_failure_reason": "missing_benchmark", "dependency": "ETF cache + confirmed benchmark index cache", "importance": "P1", "notes": ""},
            {"metric_name": "relative_return_60d", "total_count": 2, "computable_count": 0, "unable_count": 2, "coverage_ratio": 0.0, "main_failure_reason": "missing_benchmark", "dependency": "ETF cache + confirmed benchmark index cache", "importance": "P1", "notes": ""},
        ],
    )


class Index007BReadinessTest(unittest.TestCase):
    def test_classify_007b_blockers(self) -> None:
        self.assertEqual(classify_007b_blocker("usable_benchmark_count")["blocker_type"], "index_cache_missing")
        self.assertEqual(classify_007b_blocker("index_cache_schema_valid")["blocker_type"], "schema_invalid")
        self.assertEqual(classify_007b_blocker("no_fake_benchmark_guard")["blocker_type"], "fake_benchmark_guard")

    def test_blocked_conditions_and_unlock_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root)
            rows = build_007b_readiness_check(output_dir=root / "output", index_cache_dir=root / "data" / "index_cache")
            by_item = {row["readiness_item"]: row for row in rows}
            self.assertTrue(by_item["usable_benchmark_count"]["blocking"])
            self.assertTrue(by_item["index_cache_exists"]["blocking"])
            self.assertTrue(by_item["index_cache_schema_valid"]["blocking"])
            self.assertTrue(by_item["tracking_error_computable_count"]["blocking"])
            self.assertTrue(by_item["relative_return_computable_count"]["blocking"])
            self.assertTrue(by_item["index_source_network_available"]["can_be_resolved_by_network"])
            self.assertTrue(by_item["no_fake_benchmark_guard"]["passed"])
            summary = summarize_007b_readiness(rows)
            self.assertFalse(summary["allowed_to_enter_007b"])
            self.assertEqual(summary["readiness_status"], "blocked")

            plan = build_index_unlock_plan(output_dir=root / "output", index_cache_dir=root / "data" / "index_cache")
            by_symbol = {row["symbol"]: row for row in plan}
            self.assertEqual(by_symbol["510300"]["unlock_priority"], "P0_get_index_cache")
            self.assertTrue(by_symbol["510300"]["eligible_for_007b_after_unlock"])
            self.assertEqual(by_symbol["159999"]["unlock_priority"], "P3_manual_review")
            self.assertFalse(by_symbol["159999"]["usable_as_benchmark"])

            report, unlock, summary_path = write_007b_readiness_report(
                rows,
                plan,
                report_path=root / "output" / "index_007b_readiness.csv",
                unlock_plan_path=root / "output" / "index_007b_unlock_plan.csv",
                summary_path=root / "output" / "index_007b_readiness_summary.csv",
            )
            validate_output_file_schema(report, "index_007b_readiness")
            validate_output_file_schema(unlock, "index_007b_unlock_plan")
            validate_output_file_schema(summary_path, "index_007b_readiness_summary")

    def test_invalid_index_cache_blocks_schema_validity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root)
            _write_csv(root / "data" / "index_cache" / "000300.csv", [{"date": "2026-05-15", "close": 100.0, "index_code": "000300"}])
            rows = build_007b_readiness_check(output_dir=root / "output", index_cache_dir=root / "data" / "index_cache")
            by_item = {row["readiness_item"]: row for row in rows}
            self.assertTrue(by_item["index_cache_exists"]["passed"])
            self.assertTrue(by_item["index_cache_schema_valid"]["blocking"])
            plan = build_index_unlock_plan(output_dir=root / "output", index_cache_dir=root / "data" / "index_cache")
            self.assertEqual({row["symbol"]: row for row in plan}["510300"]["unlock_priority"], "P1_fix_index_schema")

    def test_no_fake_benchmark_guard_blocks_invalid_available_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root, fake_benchmark=True)
            rows = build_007b_readiness_check(output_dir=root / "output", index_cache_dir=root / "data" / "index_cache")
            by_item = {row["readiness_item"]: row for row in rows}
            self.assertTrue(by_item["no_fake_benchmark_guard"]["blocking"])
            self.assertIn("510300", by_item["no_fake_benchmark_guard"]["notes"])

    def test_command_merges_qa_report_and_is_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root)
            watched = [
                root / "data" / "cache" / "510300.csv",
                root / "data" / "index_cache" / "000300.csv",
                root / "output" / "compare_signal.csv",
                root / "output" / "compare_signal.txt",
                root / "output" / "equity_curve.csv",
                root / "output" / "performance.json",
            ]
            for path in watched:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"sentinel {path.name}\n", encoding="utf-8")
                os.utime(path, (1_700_000_000, 1_700_000_000))
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                summary = main.command_check_index_007b_readiness()
            finally:
                os.chdir(old_cwd)
            self.assertFalse(summary["allowed_to_enter_007b"])
            self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}, before)
            validate_output_file_schema(root / "output" / "qa_report.json", "qa_report")
            validate_output_file_schema(root / "output" / "data_governance_status.json", "data_governance_status")
            merged = json.loads((root / "output" / "qa_report.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["data_layer"]["index_007b_readiness"]["readiness_status"], "blocked")
            self.assertFalse(merged["data_layer"]["index_007b_readiness"]["allowed_to_enter_007b"])


if __name__ == "__main__":
    unittest.main()
