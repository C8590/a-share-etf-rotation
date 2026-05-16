from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data.manual_review import (
    build_manual_review_list,
    classify_manual_review_reason,
    merge_manual_review_into_qa_report,
    summarize_manual_review,
    validate_manual_review_inputs,
    write_manual_review_report,
)
from data.schema import validate_output_file_schema


def _diagnosis(symbol: str = "159231", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "name": "ETF A",
        "category": "industry",
        "sub_category": "test",
        "failure_type": "insufficient_rows;abnormal_return",
        "primary_failure_type": "new_etf_short_history",
        "secondary_failure_type": "insufficient_rows;abnormal_return",
        "row_count": 249,
        "min_required_rows": 250,
        "first_date": "2025-04-29",
        "last_date": "2026-05-12",
        "latest_expected_date": "2026-05-15",
        "end_date_gap_days": 3,
        "history_status": "short_history",
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
        "reason": "too few rows: 249 < 250; daily close return exceeds 20% on 1 day(s)",
        "notes": "abnormal return may reflect adjustment/source issues",
    }
    row.update(overrides)
    return row


def _observation(symbol: str = "159231", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "name": "ETF A",
        "category": "industry",
        "sub_category": "test",
        "row_count": 249,
        "min_required_rows": 250,
        "rows_needed": 1,
        "history_status": "short_history",
        "liquidity_status": "ok",
        "observation_status": "manual_review_required",
        "observation_priority": "P0_manual_review",
        "requires_manual_review": True,
        "manual_review_reason": "abnormal_return;requires_review",
        "low_liquidity_flag": False,
        "abnormal_return_flag": True,
        "candidate_status": "blocked_manual_review",
    }
    row.update(overrides)
    return row


def _candidate(symbol: str = "159231", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "name": "ETF A",
        "category": "industry",
        "sub_category": "test",
        "candidate_status": "blocked_manual_review",
        "block_reason": "manual_review_required;exclude_from_candidate_pool",
        "observation_reason": "",
    }
    row.update(overrides)
    return row


def _qa_fixture() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "data_schema_version": "1.0",
        "data_layer": {
            "passed": False,
            "effective_etf_count": 1,
            "latest_date": "2026-05-15",
            "reasons": [],
            "coverage_report": "output/data_coverage_report.csv",
            "quality_report": "output/data_quality_report.csv",
            "trading_calendar_report": "output/trading_calendar_audit.csv",
            "trading_calendar": {
                "calendar_file": "x",
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
        },
        "strategy_layer": {"passed": True},
        "output_layer": {"passed": True},
        "allow_small_observation": False,
        "blocking_reasons": [],
        "recommended_for_observation": [],
        "not_recommended": [],
        "defensive_only": [],
        "risk_note": "unit",
    }


class ManualReviewTest(unittest.TestCase):
    def test_only_requires_manual_review_rows_are_included(self) -> None:
        rows = build_manual_review_list(
            diagnosis=pd.DataFrame(
                [
                    _diagnosis(symbol="159231", requires_manual_review=True),
                    _diagnosis(symbol="159007", requires_manual_review=False, failure_type="insufficient_rows"),
                ]
            ),
            observation_pool=pd.DataFrame([_observation(symbol="159231")]),
            candidate_gate=pd.DataFrame([_candidate(symbol="159231")]),
            quality_report=pd.DataFrame(),
            failure_summary=pd.DataFrame(),
            metadata=pd.DataFrame(),
        )
        self.assertEqual([row["symbol"] for row in rows], ["159231"])

    def test_manual_review_status_stays_blocked(self) -> None:
        rows = build_manual_review_list(
            diagnosis=pd.DataFrame([_diagnosis()]),
            observation_pool=pd.DataFrame([_observation()]),
            candidate_gate=pd.DataFrame([_candidate()]),
            quality_report=pd.DataFrame(),
            failure_summary=pd.DataFrame(),
            metadata=pd.DataFrame(),
        )
        self.assertEqual(rows[0]["review_priority"], "P0_manual_review")
        self.assertIn(rows[0]["review_status"], {"blocked_until_review", "pending_manual_review"})
        self.assertEqual(rows[0]["candidate_status"], "blocked_manual_review")

    def test_abnormal_return_enters_recommended_checks(self) -> None:
        rows = build_manual_review_list(
            diagnosis=pd.DataFrame([_diagnosis(failure_type="insufficient_rows;abnormal_return")]),
            observation_pool=pd.DataFrame([_observation(abnormal_return_flag=True)]),
            candidate_gate=pd.DataFrame([_candidate()]),
            quality_report=pd.DataFrame([{"symbol": "159231", "warnings": "daily close return exceeds 20%"}]),
            failure_summary=pd.DataFrame(),
            metadata=pd.DataFrame(),
        )
        self.assertTrue(rows[0]["abnormal_return_flag"])
        self.assertIn("return outlier", rows[0]["recommended_checks"])
        self.assertIn("abnormal_return", classify_manual_review_reason(diagnosis_row=_diagnosis()))

    def test_low_liquidity_enters_recommended_checks(self) -> None:
        rows = build_manual_review_list(
            diagnosis=pd.DataFrame([_diagnosis(liquidity_status="low_liquidity", failure_type="insufficient_rows;zero_or_low_liquidity")]),
            observation_pool=pd.DataFrame([_observation(low_liquidity_flag=True, abnormal_return_flag=False)]),
            candidate_gate=pd.DataFrame([_candidate(observation_reason="low_liquidity")]),
            quality_report=pd.DataFrame(),
            failure_summary=pd.DataFrame(),
            metadata=pd.DataFrame(),
        )
        self.assertTrue(rows[0]["low_liquidity_flag"])
        self.assertIn("tradability", rows[0]["recommended_checks"])

    def test_very_short_history_enters_evidence_fields(self) -> None:
        rows = build_manual_review_list(
            diagnosis=pd.DataFrame([_diagnosis(symbol="560320", history_status="very_short_history", failure_type="insufficient_rows;unknown", secondary_failure_type="insufficient_rows;unknown", row_count=1)]),
            observation_pool=pd.DataFrame([_observation(symbol="560320", history_status="very_short_history", rows_needed=249, abnormal_return_flag=False, manual_review_reason="unknown_quality_finding")]),
            candidate_gate=pd.DataFrame([_candidate(symbol="560320")]),
            quality_report=pd.DataFrame(),
            failure_summary=pd.DataFrame(),
            metadata=pd.DataFrame(),
        )
        self.assertIn("very_short_history", rows[0]["evidence_fields"])
        self.assertIn("very_short_history", rows[0]["manual_review_reason"])
        self.assertIn("minimum history", rows[0]["recommended_checks"])

    def test_manual_review_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = build_manual_review_list(
                diagnosis=pd.DataFrame([_diagnosis()]),
                observation_pool=pd.DataFrame([_observation()]),
                candidate_gate=pd.DataFrame([_candidate()]),
                quality_report=pd.DataFrame(),
                failure_summary=pd.DataFrame(),
                metadata=pd.DataFrame(),
            )
            report, summary = write_manual_review_report(
                rows,
                report_path=root / "output" / "manual_review_list.csv",
                summary_path=root / "output" / "manual_review_summary.csv",
            )
            validate_output_file_schema(report, "manual_review_list")
            validate_output_file_schema(summary, "manual_review_summary")
            self.assertEqual(summarize_manual_review(rows)["manual_review_count"], 1)

    def test_qa_report_manual_review_summary_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            rows = build_manual_review_list(
                diagnosis=pd.DataFrame([_diagnosis()]),
                observation_pool=pd.DataFrame([_observation()]),
                candidate_gate=pd.DataFrame([_candidate()]),
                quality_report=pd.DataFrame(),
                failure_summary=pd.DataFrame(),
                metadata=pd.DataFrame(),
            )
            write_manual_review_report(
                rows,
                report_path=output / "manual_review_list.csv",
                summary_path=output / "manual_review_summary.csv",
            )
            qa_path = output / "qa_report.json"
            qa_path.write_text(json.dumps(_qa_fixture()), encoding="utf-8")
            self.assertTrue(merge_manual_review_into_qa_report(qa_path, rows=rows))
            validate_output_file_schema(qa_path, "qa_report")
            merged = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["data_layer"]["manual_review"]["manual_review_count"], 1)
            self.assertEqual(merged["data_layer"]["p0_manual_review_count"], 1)

    def test_validation_reports_missing_required_columns(self) -> None:
        validation = validate_manual_review_inputs(diagnosis=pd.DataFrame({"symbol": ["159231"]}))
        self.assertFalse(validation["valid"])
        self.assertIn("diagnosis", validation["missing_required"])

    def test_build_manual_review_does_not_modify_caches_or_strategy_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            cache = root / "data" / "cache"
            index_cache = root / "data" / "index_cache"
            cache.mkdir(parents=True)
            index_cache.mkdir(parents=True)
            files = [
                cache / "159231.csv",
                index_cache / "000300.csv",
                output / "compare_signal.csv",
                output / "equity_curve.csv",
            ]
            for path in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"sentinel {path.name}", encoding="utf-8")
            before = {path: path.read_text(encoding="utf-8") for path in files}
            rows = build_manual_review_list(
                output_dir=output,
                diagnosis=pd.DataFrame([_diagnosis()]),
                observation_pool=pd.DataFrame([_observation()]),
                candidate_gate=pd.DataFrame([_candidate()]),
                quality_report=pd.DataFrame(),
                failure_summary=pd.DataFrame(),
                metadata=pd.DataFrame(),
            )
            write_manual_review_report(
                rows,
                report_path=output / "manual_review_list.csv",
                summary_path=output / "manual_review_summary.csv",
            )
            after = {path: path.read_text(encoding="utf-8") for path in files}
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
