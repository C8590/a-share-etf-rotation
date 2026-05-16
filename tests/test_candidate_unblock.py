from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import main
from data.candidate_unblock import (
    build_candidate_unblock_plan,
    classify_unblock_path,
    summarize_candidate_unblock_plan,
    write_candidate_unblock_plan,
)
from data.schema import validate_output_file_schema


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _candidate(symbol: str, name: str, status: str, reason: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "name": name,
        "candidate_status": status,
        "blocked": True,
        "block_reason": reason,
        "observation_reason": "",
        "requires_manual_review": False,
        "liquidity_status": "ok",
        "factor_gate_status": "blocked_for_strategy_use",
    }
    row.update(extra)
    return row


def _base_qa_report(output: Path) -> None:
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


def _write_fixture_reports(root: Path) -> None:
    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    _base_qa_report(output)
    _write_csv(
        output / "candidate_gate.csv",
        [
            _candidate("159007", "ETF A", "blocked_short_history", "short_history"),
            _candidate("159231", "ETF B", "blocked_manual_review", "manual_review_required", requires_manual_review=True),
            _candidate("159001", "ETF C", "blocked_no_used_factors", "no_used_factors"),
            _candidate("510300", "ETF D", "blocked_factor_gate", "factor_score_gate_blocked_for_strategy_use"),
        ],
    )
    diagnosis = [
        {
            "symbol": "159007",
            "name": "ETF A",
            "row_count": 240,
            "min_required_rows": 250,
            "history_status": "short_history",
            "strategy_eligibility": "blocked_short_history",
            "requires_manual_review": False,
        },
        {
            "symbol": "159231",
            "name": "ETF B",
            "row_count": 1,
            "min_required_rows": 250,
            "history_status": "very_short_history",
            "strategy_eligibility": "blocked_manual_review",
            "requires_manual_review": True,
            "secondary_failure_type": "abnormal_return",
        },
    ]
    _write_csv(output / "data_quality_diagnosis.csv", diagnosis)
    _write_csv(
        output / "short_history_observation_pool.csv",
        [
            {**diagnosis[0], "rows_needed": 10, "estimated_calendar_date_until_eligible": "2026-06-01", "low_liquidity_flag": False},
            {**diagnosis[1], "rows_needed": 249, "estimated_calendar_date_until_eligible": "unknown", "low_liquidity_flag": False},
        ],
    )
    _write_csv(output / "manual_review_list.csv", [{**diagnosis[1], "manual_review_reason": "abnormal_return"}])
    _write_csv(
        output / "factor_score_report.csv",
        [
            {"symbol": "159001", "name": "ETF C", "score_status": "no_used_factors", "used_factor_count": 0, "notes": "no enabled factor produced a usable score"},
            {"symbol": "510300", "name": "ETF D", "score_status": "ok", "used_factor_count": 3, "notes": ""},
        ],
    )
    _write_csv(output / "factor_score_gate.csv", [{"gate_item": "min_computable_ratio", "status": "blocked", "blocking": True}])
    _write_csv(output / "etf_metrics_coverage.csv", [{"metric_name": "tracking_error", "main_failure_reason": "missing_benchmark"}])
    _write_csv(output / "index_data_coverage.csv", [{"tracking_index_code": "000300", "usable_as_benchmark": False}])
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


class CandidateUnblockTest(unittest.TestCase):
    def test_classification_rules(self) -> None:
        self.assertEqual(classify_unblock_path(candidate_row={"candidate_status": "blocked_short_history"})[0], "wait_for_history")
        self.assertEqual(classify_unblock_path(candidate_row={"candidate_status": "blocked_manual_review"})[0], "manual_review_required")
        self.assertEqual(
            classify_unblock_path(candidate_row={"candidate_status": "blocked_no_used_factors"}, benchmark_missing=True)[0],
            "benchmark_dependency_missing",
        )
        self.assertEqual(classify_unblock_path(candidate_row={"candidate_status": "blocked_factor_gate"})[0], "factor_gate_blocked")

    def test_build_candidate_unblock_plan_and_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root)
            rows = build_candidate_unblock_plan(output_dir=root / "output")
            by_symbol = {row["symbol"]: row for row in rows}
            self.assertEqual(by_symbol["159007"]["unblock_path"], "wait_for_history")
            self.assertTrue(by_symbol["159007"]["can_be_unblocked_by_waiting"])
            self.assertEqual(by_symbol["159231"]["unblock_path"], "manual_review_required")
            self.assertTrue(by_symbol["159231"]["can_be_unblocked_by_manual_review"])
            self.assertEqual(by_symbol["159001"]["unblock_path"], "benchmark_dependency_missing")
            self.assertIn("not a low score", by_symbol["159001"]["notes"])
            self.assertTrue(by_symbol["510300"]["still_blocked_after_primary_fix"])

            plan, summary = write_candidate_unblock_plan(
                rows,
                report_path=root / "output" / "candidate_unblock_plan.csv",
                summary_path=root / "output" / "candidate_unblock_summary.csv",
            )
            validate_output_file_schema(plan, "candidate_unblock_plan")
            validate_output_file_schema(summary, "candidate_unblock_summary")
            parsed = summarize_candidate_unblock_plan(rows)
            self.assertEqual(parsed["immediate_eligible_count"], 0)
            self.assertEqual(parsed["wait_for_history_count"], 1)
            self.assertEqual(parsed["manual_review_required_count"], 1)
            self.assertEqual(parsed["no_used_factors_count"], 1)
            self.assertTrue(parsed["benchmark_dependency_missing_count"] >= 1)

    def test_qa_report_merge_and_readonly_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture_reports(root)
            watched = [
                root / "data" / "cache" / "159007.csv",
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
                summary = main.command_build_candidate_unblock_plan()
            finally:
                os.chdir(old_cwd)
            self.assertEqual(summary["immediate_eligible_count"], 0)
            self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}, before)
            validate_output_file_schema(root / "output" / "qa_report.json", "qa_report")
            validate_output_file_schema(root / "output" / "data_governance_status.json", "data_governance_status")
            merged = json.loads((root / "output" / "qa_report.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["strategy_layer"]["candidate_unblock"]["immediate_eligible_count"], 0)


if __name__ == "__main__":
    unittest.main()
