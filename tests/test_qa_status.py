from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import main
from data.qa_status import (
    build_qa_status_breakdown,
    merge_qa_status_into_qa_report,
    summarize_qa_status,
    write_qa_status_report,
)
from data.schema import validate_output_file_schema


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _base_report(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "1.0",
        "data_schema_version": "1.0",
        "data_layer": {
            "passed": False,
            "effective_etf_count": 10,
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
            "failure_summary": {"total_failed": 2, "failure_type_counts": {}, "severe_failed": 2, "warning_failed": 0, "top_examples": []},
            "cache_metadata_audit_report": "output/cache_metadata_audit.csv",
            "cache_metadata_audit": {"total_cache_files": 0, "metadata_exists_count": 0, "legacy_cache_without_metadata_count": 0, "unknown_adjustment_count": 0, "metadata_cache_mismatch_count": 0, "top_examples": []},
            "adjustment_audit_report": "output/adjustment_audit.csv",
            "adjustment_audit": {"total_checked": 0, "unknown_adjustment_count": 0, "fallback_used_count": 0, "abnormal_return_symbols": [], "possible_adjustment_issue_count": 0, "top_examples": []},
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
        "strategy_layer": {"passed": True},
        "output_layer": {"passed": True},
        "allow_small_observation": False,
        "blocking_reasons": ["data quality failed for 2 ETF(s)", "ETF end-date coverage gap is 15 days"],
        "recommended_for_observation": [],
        "not_recommended": [],
        "defensive_only": [],
        "risk_note": "unit",
    }
    (output / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    status = {
        "generated_at": "2026-05-16T00:00:00+08:00",
        "qa_exit_status": "failed",
        "data_quality_failed_count": 2,
        "end_date_coverage_gap_days": 15,
        "candidate_total": 3,
        "candidate_eligible_count": 0,
        "candidate_blocked_count": 3,
        "blocked_short_history_count": 2,
        "blocked_manual_review_count": 1,
        "blocked_no_used_factors_count": 0,
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


def _write_fixture_reports(root: Path) -> None:
    output = root / "output"
    _base_report(output)
    coverage_rows = [
        {"symbol": f"51030{i}", "name": f"ETF {i}", "latest_date": "2026-05-13", "success": True, "source": "local_cache", "start_date": "2025-01-01", "end_date": "2026-05-13", "rows": 300, "status": "passed", "failure_reason": ""}
        for i in range(10)
    ]
    _write_csv(output / "data_coverage_report.csv", coverage_rows)
    diagnosis_rows = [
        {
            "symbol": "159231",
            "name": "ETF A",
            "category": "unknown",
            "sub_category": "",
            "failure_type": "insufficient_rows",
            "primary_failure_type": "new_etf_short_history",
            "secondary_failure_type": "insufficient_rows",
            "row_count": 249,
            "min_required_rows": 250,
            "first_date": "2025-04-29",
            "last_date": "2026-05-12",
            "latest_expected_date": "2026-05-15",
            "end_date_gap_days": 3,
            "history_status": "short_history",
            "cache_status": "fresh",
            "liquidity_status": "ok",
            "price_quality_status": "ok",
            "metadata_status": "warning",
            "strategy_eligibility": "blocked_short_history",
            "remediation_priority": "P1_short_history_observe",
            "recommended_action": "keep observation_only",
            "requires_refresh": False,
            "requires_manual_review": False,
            "exclude_from_candidate_pool": True,
            "reason": "too few rows",
            "notes": "short history",
        },
        {
            "symbol": "560320",
            "name": "ETF B",
            "category": "unknown",
            "sub_category": "",
            "failure_type": "insufficient_rows;abnormal_return",
            "primary_failure_type": "new_etf_short_history",
            "secondary_failure_type": "abnormal_return",
            "row_count": 1,
            "min_required_rows": 250,
            "first_date": "2026-05-12",
            "last_date": "2026-05-12",
            "latest_expected_date": "2026-05-15",
            "end_date_gap_days": 3,
            "history_status": "very_short_history",
            "cache_status": "fresh",
            "liquidity_status": "ok",
            "price_quality_status": "requires_review",
            "metadata_status": "warning",
            "strategy_eligibility": "blocked_manual_review",
            "remediation_priority": "P0_manual_review",
            "recommended_action": "manual review",
            "requires_refresh": False,
            "requires_manual_review": True,
            "exclude_from_candidate_pool": True,
            "reason": "too few rows",
            "notes": "manual",
        },
    ]
    _write_csv(output / "data_quality_diagnosis.csv", diagnosis_rows)
    _write_csv(
        output / "data_quality_diagnosis_summary.csv",
        [{"diagnosis_item": "short_history", "count": 2, "ratio": 1, "severity": "high", "suggested_action": "observe", "examples": "159231;560320", "notes": ""}],
    )
    _write_csv(
        output / "candidate_gate.csv",
        [
            {"symbol": "159231", "name": "ETF A", "candidate_status": "blocked_short_history", "blocked": True, "block_reason": "short_history"},
            {"symbol": "560320", "name": "ETF B", "candidate_status": "blocked_manual_review", "blocked": True, "block_reason": "manual_review_required"},
            {"symbol": "159001", "name": "ETF C", "candidate_status": "blocked_no_used_factors", "blocked": True, "block_reason": "no_used_factors"},
        ],
    )
    _write_csv(output / "short_history_observation_pool.csv", diagnosis_rows)
    _write_csv(output / "manual_review_list.csv", [diagnosis_rows[1]])
    _write_csv(output / "factor_score_gate.csv", [{"gate_item": "min_computable_ratio", "status": "blocked", "severity": "high", "threshold": ">=0.8", "actual_value": "0", "passed": False, "blocking": True, "finding": "blocked", "suggested_action": "do not enter 008B", "notes": ""}])
    _write_csv(output / "index_data_coverage.csv", [{"tracking_index_code": "000300", "usable_as_benchmark": False, "quality_status": "failed"}])
    _write_csv(output / "data_failure_summary.csv", [{"symbol": "560000", "name": "Stale", "failure_type": "stale_end_date", "end_date_gap_days": 15}])


class QaStatusTest(unittest.TestCase):
    def test_actionability_classification_and_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root)
            rows = build_qa_status_breakdown(output_dir=root / "output")
            by_item = {row["qa_item"]: row for row in rows}
            self.assertEqual(by_item["data_quality_failed"]["actionability"], "wait_for_history")
            self.assertEqual(by_item["data_quality_failed"]["can_be_fixed_by_refresh"], "false")
            self.assertEqual(by_item["end_date_coverage_gap"]["actionability"], "refresh_needed")
            self.assertEqual(by_item["end_date_coverage_gap"]["can_be_fixed_by_refresh"], "maybe")
            self.assertEqual(by_item["manual_review_required"]["actionability"], "manual_review")
            self.assertTrue(by_item["manual_review_required"]["requires_manual_review"])
            self.assertTrue(by_item["factor_gate_status"]["blocks_008b"])
            self.assertTrue(by_item["usable_benchmark_count"]["blocks_007b"])

            breakdown, summary = write_qa_status_report(rows, breakdown_path=root / "output" / "qa_status_breakdown.csv", summary_path=root / "output" / "qa_status_summary.csv")
            validate_output_file_schema(breakdown, "qa_status_breakdown")
            validate_output_file_schema(summary, "qa_status_summary")

    def test_qa_report_merge_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root)
            rows = build_qa_status_breakdown(output_dir=root / "output")
            write_qa_status_report(rows, breakdown_path=root / "output" / "qa_status_breakdown.csv", summary_path=root / "output" / "qa_status_summary.csv")
            summary = summarize_qa_status(rows)
            self.assertEqual(summary["wait_for_history_count"], 2)
            self.assertEqual(summary["manual_review_action_count"], 1)
            self.assertTrue(summary["blocks_007b"])
            self.assertTrue(summary["blocks_008b"])
            self.assertTrue(merge_qa_status_into_qa_report(root / "output" / "qa_report.json", summary=summary))
            validate_output_file_schema(root / "output" / "qa_report.json", "qa_report")
            merged = json.loads((root / "output" / "qa_report.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["data_layer"]["qa_status"]["wait_for_history_count"], 2)

    def test_summarize_qa_status_does_not_modify_cache_or_formal_outputs(self) -> None:
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
                main.command_summarize_qa_status()
            finally:
                os.chdir(old_cwd)
            after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
