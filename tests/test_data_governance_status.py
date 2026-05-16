from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data.data_governance import (
    build_data_governance_status,
    merge_data_governance_into_qa_report,
    write_data_governance_runbook,
    write_data_governance_status,
)
from data.schema import validate_output_file_schema


def _qa_fixture(*, usable_benchmark_count: int = 0) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "data_schema_version": "1.0",
        "data_layer": {
            "passed": False,
            "effective_etf_count": 1,
            "latest_date": "2026-05-15",
            "reasons": ["data quality failed for 1 ETF(s)", "ETF end-date coverage gap is 15 days"],
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
            "index_data": {
                "status": "ok",
                "index_map_report": "output/index_map.csv",
                "index_data_coverage_report": "output/index_data_coverage.csv",
                "total_index_mappings": 1,
                "index_cache_written_count": 0,
                "usable_benchmark_count": usable_benchmark_count,
                "fetch_success_count": 0,
                "fetch_failed_count": 0,
                "csindex_success_count": 0,
                "eastmoney_failure_count": 0,
                "schema_invalid_count": 0,
                "manual_review_required_count": 0,
                "low_coverage_indexes": [],
                "top_examples": [],
            },
        },
        "strategy_layer": {
            "passed": True,
        },
        "output_layer": {"passed": True},
        "allow_small_observation": False,
        "blocking_reasons": [],
        "recommended_for_observation": [],
        "not_recommended": [],
        "defensive_only": [],
        "risk_note": "unit",
    }


class DataGovernanceStatusTest(unittest.TestCase):
    def _status(self, **overrides: object) -> dict[str, object]:
        diagnosis = pd.DataFrame(
            [
                {
                    "symbol": "159231",
                    "requires_manual_review": True,
                    "history_status": "short_history",
                }
            ]
        )
        candidate_gate = pd.DataFrame(
            [
                {
                    "symbol": "159231",
                    "candidate_status": "blocked_manual_review",
                    "blocked": True,
                },
                {
                    "symbol": "159007",
                    "candidate_status": "blocked_short_history",
                    "blocked": True,
                },
                {
                    "symbol": "159001",
                    "candidate_status": "blocked_no_used_factors",
                    "blocked": True,
                },
            ]
        )
        observation_pool = pd.DataFrame(
            [
                {
                    "symbol": "159231",
                    "history_status": "short_history",
                    "estimated_trading_days_until_eligible": 1,
                },
                {
                    "symbol": "560320",
                    "history_status": "very_short_history",
                    "estimated_trading_days_until_eligible": 249,
                },
            ]
        )
        manual_review = pd.DataFrame(
            [
                {
                    "symbol": "159231",
                    "review_priority": "P0_manual_review",
                    "review_status": "blocked_until_review",
                }
            ]
        )
        factor_gate = pd.DataFrame([{"gate_item": "min_computable_ratio", "status": "blocked", "blocking": True}])
        kwargs = {
            "diagnosis": diagnosis,
            "candidate_gate": candidate_gate,
            "observation_pool": observation_pool,
            "manual_review": manual_review,
            "factor_gate": factor_gate,
            "qa_report": _qa_fixture(),
        }
        kwargs.update(overrides)
        return build_data_governance_status(**kwargs)

    def test_generates_status_from_governance_reports(self) -> None:
        status = self._status()
        self.assertEqual(status["data_quality_failed_count"], 1)
        self.assertEqual(status["candidate_total"], 3)
        self.assertEqual(status["candidate_blocked_count"], 3)
        self.assertEqual(status["manual_review_count"], 1)
        self.assertEqual(status["factor_gate_status"], "blocked_for_strategy_use")

    def test_eligible_zero_blocks_008b(self) -> None:
        status = self._status()
        self.assertEqual(status["candidate_eligible_count"], 0)
        self.assertFalse(status["allowed_to_enter_008b"])
        self.assertIn("no_candidate_eligible", status["blocking_reasons"])

    def test_usable_benchmark_zero_blocks_007b(self) -> None:
        status = self._status(qa_report=_qa_fixture(usable_benchmark_count=0))
        self.assertFalse(status["allowed_to_enter_007b"])
        self.assertIn("no_usable_benchmark", status["blocking_reasons"])

    def test_manual_review_and_short_history_blocking_reasons(self) -> None:
        status = self._status()
        self.assertIn("manual_review_required", status["blocking_reasons"])
        self.assertIn("short_history", status["blocking_reasons"])

    def test_status_json_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = self._status()
            path = write_data_governance_status(status, path=root / "output" / "data_governance_status.json")
            validate_output_file_schema(path, "data_governance_status")

    def test_qa_report_data_governance_summary_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            status = self._status()
            write_data_governance_status(status, path=output / "data_governance_status.json")
            write_data_governance_runbook(status, path=root / "docs" / "research" / "data_governance_runbook.md")
            qa_path = output / "qa_report.json"
            qa_path.write_text(json.dumps(_qa_fixture()), encoding="utf-8")
            self.assertTrue(merge_data_governance_into_qa_report(qa_path, status=status))
            validate_output_file_schema(qa_path, "qa_report")
            merged = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertFalse(merged["data_layer"]["data_governance"]["allowed_to_enter_008b"])
            self.assertIn("blocking_reasons", merged["data_layer"]["data_governance"])

    def test_summarize_data_governance_does_not_modify_caches_or_strategy_outputs(self) -> None:
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
            status = self._status()
            write_data_governance_status(status, path=output / "data_governance_status.json")
            write_data_governance_runbook(status, path=root / "docs" / "research" / "data_governance_runbook.md")
            after = {path: path.read_text(encoding="utf-8") for path in files}
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
