from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data.observation_pool import (
    build_short_history_observation_pool,
    classify_observation_priority,
    estimate_days_until_eligible,
    merge_observation_pool_into_qa_report,
    summarize_observation_pool,
    write_observation_pool_report,
)
from data.schema import validate_output_file_schema


def _diagnosis(symbol: str = "159007", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "name": "ETF A",
        "category": "industry",
        "sub_category": "test",
        "failure_type": "insufficient_rows",
        "row_count": 240,
        "min_required_rows": 250,
        "first_date": "2025-05-20",
        "last_date": "2026-05-12",
        "latest_expected_date": "2026-05-15",
        "end_date_gap_days": 3,
        "history_status": "short_history",
        "liquidity_status": "ok",
        "price_quality_status": "ok",
        "strategy_eligibility": "blocked_short_history",
        "requires_manual_review": False,
        "exclude_from_candidate_pool": True,
        "recommended_action": "observe",
        "reason": "too few rows",
        "notes": "short history is not a low score",
    }
    row.update(overrides)
    return row


def _candidate(symbol: str = "159007", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "name": "ETF A",
        "category": "industry",
        "sub_category": "test",
        "candidate_status": "blocked_short_history",
        "observation_reason": "",
    }
    row.update(overrides)
    return row


def _calendar(days: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": day,
                "is_open": True,
                "exchange": "A_SHARE",
                "source": "unit-test",
                "calendar_version": "1.0",
                "generated_at": "2026-05-15T00:00:00+08:00",
                "note": "",
            }
            for day in days
        ]
    )


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


class ObservationPoolTest(unittest.TestCase):
    def test_rows_needed_calculation(self) -> None:
        estimate = estimate_days_until_eligible(row_count=240, min_required_rows=250)
        self.assertEqual(estimate["rows_needed"], 10)
        self.assertEqual(estimate["estimated_trading_days_until_eligible"], 10)

    def test_very_short_history_classification(self) -> None:
        rows = build_short_history_observation_pool(
            diagnosis=pd.DataFrame([_diagnosis(row_count=20, history_status="very_short_history")]),
            candidate_gate=pd.DataFrame([_candidate()]),
            metadata=pd.DataFrame(),
            calendar=_calendar(["2026-05-15", "2026-05-18"]),
        )
        self.assertEqual(rows[0]["observation_status"], "very_short_history")
        self.assertEqual(rows[0]["observation_priority"], "P3_archive_watch")

    def test_manual_review_required_priority(self) -> None:
        self.assertEqual(
            classify_observation_priority(
                history_status="very_short_history",
                requires_manual_review=True,
                low_liquidity_flag=True,
            ),
            "P0_manual_review",
        )
        rows = build_short_history_observation_pool(
            diagnosis=pd.DataFrame(
                [
                    _diagnosis(
                        requires_manual_review=True,
                        failure_type="insufficient_rows;abnormal_return",
                        price_quality_status="requires_review",
                    )
                ]
            ),
            candidate_gate=pd.DataFrame([_candidate(candidate_status="blocked_manual_review")]),
            metadata=pd.DataFrame(),
            calendar=_calendar(["2026-05-15", "2026-05-18"]),
        )
        self.assertEqual(rows[0]["observation_priority"], "P0_manual_review")
        self.assertEqual(rows[0]["observation_status"], "manual_review_required")
        self.assertIn("abnormal_return", rows[0]["manual_review_reason"])

    def test_low_liquidity_observation_marker(self) -> None:
        rows = build_short_history_observation_pool(
            diagnosis=pd.DataFrame([_diagnosis(liquidity_status="low_liquidity")]),
            candidate_gate=pd.DataFrame([_candidate(observation_reason="low_liquidity")]),
            metadata=pd.DataFrame(),
            calendar=_calendar(["2026-05-15", "2026-05-18"]),
        )
        self.assertTrue(rows[0]["low_liquidity_flag"])
        self.assertEqual(rows[0]["observation_status"], "waiting_but_low_liquidity")
        self.assertEqual(rows[0]["observation_priority"], "P2_low_liquidity_watch")

    def test_calendar_estimates_date_when_available(self) -> None:
        rows = build_short_history_observation_pool(
            diagnosis=pd.DataFrame([_diagnosis(row_count=248)]),
            candidate_gate=pd.DataFrame([_candidate()]),
            metadata=pd.DataFrame(),
            calendar=_calendar(["2026-05-15", "2026-05-18", "2026-05-19"]),
        )
        self.assertEqual(rows[0]["rows_needed"], 2)
        self.assertEqual(rows[0]["estimated_calendar_date_until_eligible"], "2026-05-19")

    def test_calendar_shortage_does_not_fabricate_date(self) -> None:
        rows = build_short_history_observation_pool(
            diagnosis=pd.DataFrame([_diagnosis(row_count=248)]),
            candidate_gate=pd.DataFrame([_candidate()]),
            metadata=pd.DataFrame(),
            calendar=_calendar(["2026-05-15", "2026-05-18"]),
        )
        self.assertEqual(rows[0]["estimated_calendar_date_until_eligible"], "unknown")
        self.assertEqual(summarize_observation_pool(rows)["unknown_estimate_count"], 1)

    def test_observation_pool_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = build_short_history_observation_pool(
                diagnosis=pd.DataFrame([_diagnosis(row_count=248)]),
                candidate_gate=pd.DataFrame([_candidate()]),
                metadata=pd.DataFrame(),
                calendar=_calendar(["2026-05-15", "2026-05-18", "2026-05-19"]),
            )
            report, summary = write_observation_pool_report(
                rows,
                report_path=root / "output" / "short_history_observation_pool.csv",
                summary_path=root / "output" / "short_history_observation_summary.csv",
            )
            validate_output_file_schema(report, "short_history_observation_pool")
            validate_output_file_schema(summary, "short_history_observation_summary")

    def test_qa_report_observation_pool_summary_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            rows = build_short_history_observation_pool(
                diagnosis=pd.DataFrame([_diagnosis(row_count=248)]),
                candidate_gate=pd.DataFrame([_candidate()]),
                metadata=pd.DataFrame(),
                calendar=_calendar(["2026-05-15", "2026-05-18", "2026-05-19"]),
            )
            write_observation_pool_report(
                rows,
                report_path=output / "short_history_observation_pool.csv",
                summary_path=output / "short_history_observation_summary.csv",
            )
            qa_path = output / "qa_report.json"
            qa_path.write_text(json.dumps(_qa_fixture()), encoding="utf-8")
            self.assertTrue(merge_observation_pool_into_qa_report(qa_path, rows=rows))
            validate_output_file_schema(qa_path, "qa_report")
            merged = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["data_layer"]["observation_pool"]["total_observation_count"], 1)
            self.assertEqual(merged["data_layer"]["estimated_eligible_within_20d_count"], 1)

    def test_build_observation_pool_does_not_modify_caches_or_strategy_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            cache = root / "data" / "cache"
            index_cache = root / "data" / "index_cache"
            cache.mkdir(parents=True)
            index_cache.mkdir(parents=True)
            files = [
                cache / "159007.csv",
                index_cache / "000300.csv",
                output / "compare_signal.csv",
                output / "equity_curve.csv",
            ]
            for path in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"sentinel {path.name}", encoding="utf-8")
            before = {path: path.read_text(encoding="utf-8") for path in files}
            rows = build_short_history_observation_pool(
                output_dir=output,
                diagnosis=pd.DataFrame([_diagnosis()]),
                candidate_gate=pd.DataFrame([_candidate()]),
                metadata=pd.DataFrame(),
                calendar=_calendar(["2026-05-15", "2026-05-18"]),
            )
            write_observation_pool_report(
                rows,
                report_path=output / "short_history_observation_pool.csv",
                summary_path=output / "short_history_observation_summary.csv",
            )
            after = {path: path.read_text(encoding="utf-8") for path in files}
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
