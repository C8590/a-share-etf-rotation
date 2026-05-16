from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data.quality_diagnosis import (
    build_quality_remediation_plan,
    classify_cache_staleness,
    classify_history_status,
    diagnose_quality_failure,
    merge_quality_diagnosis_into_qa_report,
    summarize_quality_diagnosis,
    write_quality_diagnosis_report,
)
from data.schema import validate_output_file_schema


def _quality(symbol: str = "159007", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "name": "ETF A",
        "status": "failed",
        "rows": 15,
        "start_date": "2026-04-17",
        "end_date": "2026-05-12",
        "failure_types": "insufficient_rows",
        "primary_failure_type": "insufficient_rows",
        "errors": "too few rows: 15 < 250",
        "warnings": "",
    }
    row.update(overrides)
    return row


def _failure(symbol: str = "159007", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol,
        "failure_type": "insufficient_rows",
        "failure_reason": "too few rows: 15 < 250",
        "end_date_gap_days": 3,
        "latest_expected_date": "2026-05-15",
    }
    row.update(overrides)
    return row


class QualityDiagnosisTest(unittest.TestCase):
    def test_insufficient_rows_maps_to_short_history_classes(self) -> None:
        self.assertEqual(classify_history_status(15, min_required_rows=250), "very_short_history")
        self.assertEqual(classify_history_status(120, min_required_rows=250), "short_history")
        row = diagnose_quality_failure(
            quality_row=_quality(rows=15),
            failure_rows=[_failure()],
            refresh_row={"symbol": "159007", "cache_exists": True},
            latest_expected_date="2026-05-15",
            cache_dir=Path("not-used"),
        )
        self.assertEqual(row["history_status"], "very_short_history")
        self.assertEqual(row["strategy_eligibility"], "blocked_short_history")
        self.assertEqual(row["remediation_priority"], "P1_short_history_observe")

    def test_old_insufficient_rows_is_cache_incomplete_refresh_candidate(self) -> None:
        row = diagnose_quality_failure(
            quality_row=_quality(rows=80, start_date="2020-01-02", end_date="2026-05-12"),
            failure_rows=[_failure()],
            refresh_row={"symbol": "159007", "cache_exists": True},
            latest_expected_date="2026-05-15",
            cache_dir=Path("not-used"),
        )
        self.assertEqual(row["primary_failure_type"], "old_etf_cache_incomplete")
        self.assertTrue(row["requires_refresh"])

    def test_stale_cache_is_identified(self) -> None:
        self.assertEqual(
            classify_cache_staleness(cache_exists=True, last_date="2026-04-30", latest_expected_date="2026-05-15"),
            "severely_stale",
        )
        row = diagnose_quality_failure(
            quality_row=_quality(failure_types="stale_end_date", primary_failure_type="stale_end_date", rows=300),
            failure_rows=[_failure(failure_type="stale_end_date", end_date_gap_days=15)],
            refresh_row={"symbol": "159007", "cache_exists": True, "latest_cache_date": "2026-04-30"},
            latest_expected_date="2026-05-15",
            cache_dir=Path("not-used"),
        )
        self.assertEqual(row["cache_status"], "severely_stale")
        self.assertEqual(row["remediation_priority"], "P0_refresh_needed")

    def test_missing_cache_is_identified(self) -> None:
        row = diagnose_quality_failure(
            quality_row=_quality(failure_types="download_failed", primary_failure_type="download_failed", rows=0, start_date="", end_date=""),
            failure_rows=[_failure(failure_type="download_failed", failure_reason="local data not found")],
            refresh_row={"symbol": "159007", "cache_exists": False},
            latest_expected_date="2026-05-15",
            cache_dir=Path("not-used"),
        )
        self.assertEqual(row["cache_status"], "missing")
        self.assertEqual(row["strategy_eligibility"], "blocked_missing_cache")

    def test_abnormal_return_requires_manual_review(self) -> None:
        row = diagnose_quality_failure(
            quality_row=_quality(failure_types="abnormal_return", primary_failure_type="abnormal_return", rows=300, errors="", warnings="daily close return exceeds 20% on 1 day(s)"),
            failure_rows=[_failure(failure_type="abnormal_return", failure_reason="daily close return exceeds 20% on 1 day(s)")],
            refresh_row={"symbol": "159007", "cache_exists": True},
            latest_expected_date="2026-05-15",
            cache_dir=Path("not-used"),
        )
        self.assertTrue(row["requires_manual_review"])
        self.assertEqual(row["strategy_eligibility"], "blocked_manual_review")
        self.assertEqual(row["remediation_priority"], "P0_manual_review")

    def test_low_liquidity_gets_filter_recommendation(self) -> None:
        row = diagnose_quality_failure(
            quality_row=_quality(failure_types="zero_or_low_liquidity", primary_failure_type="zero_or_low_liquidity", rows=300, errors=""),
            failure_rows=[_failure(failure_type="zero_or_low_liquidity", failure_reason="avg_amount_20 1 < 20000000")],
            coverage_row={"symbol": "159007", "avg_amount_20": 1},
            refresh_row={"symbol": "159007", "cache_exists": True},
            latest_expected_date="2026-05-15",
            cache_dir=Path("not-used"),
        )
        self.assertEqual(row["liquidity_status"], "low_liquidity")
        self.assertEqual(row["strategy_eligibility"], "observation_only")
        self.assertEqual(row["remediation_priority"], "P2_low_liquidity_filter")

    def test_blocked_short_history_is_not_low_score(self) -> None:
        row = diagnose_quality_failure(
            quality_row=_quality(rows=30),
            failure_rows=[_failure()],
            refresh_row={"symbol": "159007", "cache_exists": True},
            latest_expected_date="2026-05-15",
            cache_dir=Path("not-used"),
        )
        self.assertEqual(row["strategy_eligibility"], "blocked_short_history")
        self.assertIn("not a low score", row["notes"])

    def test_diagnosis_report_schema_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                diagnose_quality_failure(
                    quality_row=_quality(),
                    failure_rows=[_failure()],
                    refresh_row={"symbol": "159007", "cache_exists": True},
                    latest_expected_date="2026-05-15",
                    cache_dir=root / "cache",
                )
            ]
            report, summary = write_quality_diagnosis_report(
                rows,
                report_path=root / "output" / "data_quality_diagnosis.csv",
                summary_path=root / "output" / "data_quality_diagnosis_summary.csv",
            )
            validate_output_file_schema(report, "data_quality_diagnosis")
            validate_output_file_schema(summary, "data_quality_diagnosis_summary")
            parsed = summarize_quality_diagnosis(report_path=report)
            self.assertEqual(parsed["total_failed"], 1)
            self.assertEqual(parsed["short_history_count"], 1)

    def test_qa_report_diagnosis_summary_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            rows = [
                diagnose_quality_failure(
                    quality_row=_quality(),
                    failure_rows=[_failure()],
                    refresh_row={"symbol": "159007", "cache_exists": True},
                    latest_expected_date="2026-05-15",
                    cache_dir=root / "cache",
                )
            ]
            write_quality_diagnosis_report(rows, report_path=output / "data_quality_diagnosis.csv", summary_path=output / "data_quality_diagnosis_summary.csv")
            qa = {
                "schema_version": "1.0",
                "data_schema_version": "1.0",
                "data_layer": {"passed": False},
                "strategy_layer": {"passed": True},
                "output_layer": {"passed": True},
                "allow_small_observation": False,
                "blocking_reasons": [],
                "recommended_for_observation": [],
                "not_recommended": [],
                "defensive_only": [],
                "risk_note": "unit",
            }
            qa_path = output / "qa_report.json"
            qa_path.write_text(json.dumps(qa), encoding="utf-8")
            self.assertTrue(merge_quality_diagnosis_into_qa_report(qa_path, rows=rows))
            merged = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["data_layer"]["data_quality_diagnosis"]["total_failed"], 1)
            self.assertIn("top_blocking_reasons", merged["data_layer"])

    def test_build_plan_does_not_modify_caches_or_strategy_outputs(self) -> None:
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
            quality = pd.DataFrame([_quality()])
            failures = pd.DataFrame([_failure()])
            rows = build_quality_remediation_plan(
                output_dir=output,
                cache_dir=cache,
                quality_report=quality,
                failure_summary=failures,
                cache_refresh_plan=pd.DataFrame([{"symbol": "159007", "cache_exists": True}]),
            )
            write_quality_diagnosis_report(rows, report_path=output / "data_quality_diagnosis.csv", summary_path=output / "data_quality_diagnosis_summary.csv")
            after = {path: path.read_text(encoding="utf-8") for path in files}
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
