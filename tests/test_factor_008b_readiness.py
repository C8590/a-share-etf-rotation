from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import main
from data.schema import validate_output_file_schema
from strategy.factor_readiness import (
    build_008b_readiness_check,
    classify_008b_blocker,
    summarize_008b_readiness,
    write_008b_readiness_report,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _base_qa_report(output: Path) -> None:
    factor_score = {
        "status": "ok",
        "factor_score_report": "output/factor_score_report.csv",
        "factor_score_detail_report": "output/factor_score_detail.csv",
        "factor_score_audit_report": "output/factor_score_audit.csv",
        "factor_score_gate_report": "output/factor_score_gate.csv",
        "total_symbols": 4,
        "score_computable_count": 1,
        "unable_to_score_count": 3,
        "enabled_factor_count": 10,
        "used_factor_counts": {},
        "skipped_factor_counts": {},
        "missing_required_factor_count": 0,
        "audit_status": "blocked",
        "high_severity_findings": [],
        "warning_findings": [],
        "computable_ratio": 0.25,
        "top_blocking_reasons": [],
        "gate_status": "blocked_for_strategy_use",
        "blocking_findings": [],
        "passed_gate_count": 0,
        "failed_gate_count": 5,
        "top_examples": [],
    }
    report = {
        "schema_version": "1.0",
        "data_schema_version": "1.0",
        "data_layer": {
            "passed": False,
            "effective_etf_count": 4,
            "latest_date": "2026-05-13",
            "reasons": ["data quality failed for 2 ETF(s)", "ETF end-date coverage gap is 15 days"],
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
                "total_index_mappings": 1,
                "index_cache_written_count": 0,
                "usable_benchmark_count": 0,
                "fetch_success_count": 0,
                "fetch_failed_count": 1,
                "csindex_success_count": 0,
                "eastmoney_failure_count": 1,
                "schema_invalid_count": 1,
                "manual_review_required_count": 1,
                "low_coverage_indexes": [],
                "top_examples": [],
            },
        },
        "strategy_layer": {"passed": True, "factor_score": factor_score},
        "output_layer": {"passed": True},
        "allow_small_observation": False,
        "blocking_reasons": ["data quality failed for 2 ETF(s)", "ETF end-date coverage gap is 15 days"],
        "recommended_for_observation": [],
        "not_recommended": [],
        "defensive_only": [],
        "risk_note": "unit",
    }
    (output / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_fixture_reports(root: Path) -> None:
    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    _base_qa_report(output)
    _write_csv(
        output / "factor_score_gate.csv",
        [
            {"gate_item": "min_computable_ratio", "status": "blocked", "severity": "high", "threshold": ">= 0.80", "actual_value": "0.2500 (1/4)", "passed": False, "blocking": True, "finding": "low", "suggested_action": "raise coverage", "notes": ""},
            {"gate_item": "max_unable_to_score_ratio", "status": "blocked", "severity": "high", "threshold": "<= 0.20", "actual_value": "0.7500 (3/4)", "passed": False, "blocking": True, "finding": "high", "suggested_action": "fix no_used_factors", "notes": "no_used_factors=3"},
            {"gate_item": "no_short_history_bias", "status": "blocked", "severity": "high", "threshold": "count = 0", "actual_value": "1/1 (1.0000)", "passed": False, "blocking": True, "finding": "short history", "suggested_action": "wait", "notes": "insufficient_rows=1"},
            {"gate_item": "benchmark_dependency_available", "status": "blocked", "severity": "high", "threshold": "available", "actual_value": "tracking_error, relative_return_60d", "passed": False, "blocking": True, "finding": "missing benchmark", "suggested_action": "fix index cache", "notes": ""},
            {"gate_item": "nav_iopv_dependency_available", "status": "blocked", "severity": "high", "threshold": "available", "actual_value": "discount_premium", "passed": False, "blocking": True, "finding": "missing nav", "suggested_action": "add NAV/IOPV", "notes": ""},
            {"gate_item": "factor_coverage_minimum", "status": "blocked", "severity": "high", "threshold": ">= 30 and >= 0.80", "actual_value": "score_computable_count=1", "passed": False, "blocking": True, "finding": "low coverage", "suggested_action": "raise coverage", "notes": ""},
        ],
    )
    _write_csv(
        output / "factor_score_audit.csv",
        [
            {"audit_item": "unable_to_score_count", "status": "warning", "severity": "warning", "count": 3, "ratio": 0.75, "affected_symbols": "159001,159002,159003", "finding": "3 cannot score", "suggested_action": "inspect", "notes": "no_used_factors=3"},
            {"audit_item": "factor_coverage_by_name", "status": "blocked", "severity": "high", "count": 0, "ratio": 0, "affected_symbols": "159001", "finding": "tracking_error is used for 0 of 4 symbols.", "suggested_action": "fix benchmark", "notes": "source_unavailable=4"},
            {"audit_item": "factor_coverage_by_name", "status": "blocked", "severity": "high", "count": 0, "ratio": 0, "affected_symbols": "159001", "finding": "relative_return_60d is used for 0 of 4 symbols.", "suggested_action": "fix benchmark", "notes": "source_unavailable=4"},
            {"audit_item": "factor_coverage_by_name", "status": "blocked", "severity": "high", "count": 0, "ratio": 0, "affected_symbols": "159001", "finding": "discount_premium is used for 0 of 4 symbols.", "suggested_action": "add NAV", "notes": "source_unavailable=4"},
            {"audit_item": "factor_coverage_by_name", "status": "disabled", "severity": "warning", "count": 0, "ratio": 0, "affected_symbols": "159001", "finding": "fund_size is used for 0 of 4 symbols.", "suggested_action": "metadata", "notes": "disabled=4"},
            {"audit_item": "factor_coverage_by_name", "status": "disabled", "severity": "warning", "count": 0, "ratio": 0, "affected_symbols": "159001", "finding": "management_fee is used for 0 of 4 symbols.", "suggested_action": "metadata", "notes": "disabled=4"},
            {"audit_item": "metadata_low_coverage_dependency", "status": "warning", "severity": "warning", "count": 4, "ratio": 1, "affected_symbols": "159001", "finding": "metadata low", "suggested_action": "metadata", "notes": "warning=4"},
        ],
    )
    _write_csv(
        output / "factor_score_report.csv",
        [
            {"symbol": "159001", "name": "ETF A", "score_status": "no_used_factors", "used_factor_count": 0},
            {"symbol": "159002", "name": "ETF B", "score_status": "no_used_factors", "used_factor_count": 0},
            {"symbol": "159003", "name": "ETF C", "score_status": "no_used_factors", "used_factor_count": 0},
            {"symbol": "159004", "name": "ETF D", "score_status": "ok", "used_factor_count": 1},
        ],
    )
    _write_csv(output / "factor_score_detail.csv", [{"symbol": "159001", "factor_name": "tracking_error", "factor_status": "source_unavailable"}])
    _write_csv(output / "candidate_gate.csv", [{"symbol": "159001", "candidate_status": "blocked_no_used_factors", "blocked": True}])
    _write_csv(output / "candidate_unblock_plan.csv", [{"symbol": "159001", "unblock_path": "benchmark_dependency_missing", "still_blocked_after_primary_fix": True}])
    _write_csv(output / "manual_review_list.csv", [{"symbol": "560320", "name": "ETF M"}])
    _write_csv(output / "etf_metrics_coverage.csv", [{"metric_name": "tracking_error", "main_failure_reason": "missing_benchmark"}])
    _write_csv(output / "index_data_coverage.csv", [{"tracking_index_code": "000300", "usable_as_benchmark": False}])
    _write_csv(output / "etf_metadata_coverage.csv", [{"field_name": "fund_size", "coverage_ratio": 0.0}, {"field_name": "management_fee", "coverage_ratio": 0.0}])
    status = {
        "generated_at": "2026-05-16T00:00:00+08:00",
        "qa_exit_status": "failed",
        "data_quality_failed_count": 2,
        "end_date_coverage_gap_days": 15,
        "candidate_total": 4,
        "candidate_eligible_count": 0,
        "candidate_blocked_count": 4,
        "blocked_short_history_count": 1,
        "blocked_manual_review_count": 1,
        "blocked_no_used_factors_count": 1,
        "observation_pool_count": 2,
        "very_short_history_count": 1,
        "estimated_eligible_within_20d_count": 1,
        "estimated_eligible_within_60d_count": 1,
        "manual_review_count": 1,
        "factor_gate_status": "blocked_for_strategy_use",
        "allowed_to_enter_008b": False,
        "allowed_to_enter_007b": False,
        "next_recommended_action": "unit",
        "blocking_reasons": [],
        "report_paths": {},
    }
    (output / "data_governance_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


class Factor008BReadinessTest(unittest.TestCase):
    def test_classify_008b_blockers(self) -> None:
        self.assertEqual(classify_008b_blocker("candidate_eligible_count")["blocker_type"], "candidate_gate")
        self.assertEqual(classify_008b_blocker("tracking_error_dependency")["blocker_type"], "benchmark_dependency")
        self.assertEqual(classify_008b_blocker("discount_premium_dependency")["blocker_type"], "nav_iopv_dependency")

    def test_build_readiness_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root)
            rows = build_008b_readiness_check(output_dir=root / "output")
            by_item = {row["readiness_item"]: row for row in rows}
            self.assertTrue(by_item["candidate_eligible_count"]["blocking"])
            self.assertTrue(by_item["factor_gate_status"]["blocking"])
            self.assertTrue(by_item["min_computable_ratio"]["blocking"])
            self.assertTrue(by_item["short_history_bias"]["blocking"])
            self.assertTrue(by_item["tracking_error_dependency"]["blocking"])
            self.assertTrue(by_item["discount_premium_dependency"]["blocking"])
            self.assertTrue(by_item["no_used_factors"]["blocking"])
            self.assertIn("not bearish", by_item["no_used_factors"]["notes"])
            summary = summarize_008b_readiness(rows)
            self.assertFalse(summary["allowed_to_enter_008b"])
            self.assertEqual(summary["readiness_status"], "blocked")
            report, summary_path = write_008b_readiness_report(
                rows,
                report_path=root / "output" / "factor_008b_readiness.csv",
                summary_path=root / "output" / "factor_008b_readiness_summary.csv",
            )
            validate_output_file_schema(report, "factor_008b_readiness")
            validate_output_file_schema(summary_path, "factor_008b_readiness_summary")

    def test_command_merges_qa_report_and_is_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root)
            watched = [
                root / "data" / "cache" / "159001.csv",
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
                summary = main.command_check_factor_008b_readiness()
            finally:
                os.chdir(old_cwd)
            self.assertFalse(summary["allowed_to_enter_008b"])
            self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}, before)
            validate_output_file_schema(root / "output" / "qa_report.json", "qa_report")
            validate_output_file_schema(root / "output" / "data_governance_status.json", "data_governance_status")
            merged = json.loads((root / "output" / "qa_report.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["strategy_layer"]["factor_score"]["readiness_status"], "blocked")


if __name__ == "__main__":
    unittest.main()
