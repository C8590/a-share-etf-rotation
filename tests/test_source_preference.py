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
from data.schema import validate_output_file_schema
from data.source_preference import (
    SourceSample,
    compare_source_data,
    run_source_preference_evaluation,
    summarize_source_preference_audit,
    write_source_preference_audit,
)


def _frame(rows: int = 260, start: str = "2024-01-02", close_base: float = 10.0, slope: float = 0.01) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=rows)
    close = pd.Series(range(rows), dtype=float) * slope + close_base
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 1000,
            "amount": 10000,
        }
    )


def _samples(
    *,
    sina: pd.DataFrame | None = None,
    qfq: pd.DataFrame | None = None,
    none: pd.DataFrame | None = None,
    qfq_error: str = "",
    none_error: str = "",
    sina_error: str = "",
) -> list[SourceSample]:
    return [
        SourceSample("510300", "ETF A", "sina_unknown", "fund_etf_hist_sina", "unknown", sina_error == "", sina, sina_error),
        SourceSample("510300", "ETF A", "em_qfq", "fund_etf_hist_em", "qfq", qfq_error == "", qfq, qfq_error),
        SourceSample("510300", "ETF A", "em_none", "fund_etf_hist_em", "none", none_error == "", none, none_error),
    ]


def _row(rows: list[dict[str, object]], candidate: str) -> dict[str, object]:
    return next(item for item in rows if item["source_candidate"] == candidate)


class SourcePreferenceTest(unittest.TestCase):
    def test_em_qfq_success_quality_not_worse_is_safe_to_promote(self) -> None:
        rows = compare_source_data("510300", "ETF A", _samples(sina=_frame(), qfq=_frame(), none=_frame()), latest_expected_date="2024-12-31")
        qfq = _row(rows, "em_qfq")
        self.assertEqual(qfq["preferred_candidate"], "em_qfq")
        self.assertTrue(qfq["safe_to_promote"])
        self.assertFalse(qfq["requires_manual_review"])

    def test_em_qfq_failure_does_not_promote(self) -> None:
        rows = compare_source_data("510300", "ETF A", _samples(sina=_frame(), qfq_error="source unavailable", none=_frame()), latest_expected_date="2024-12-31")
        qfq = _row(rows, "em_qfq")
        self.assertFalse(qfq["safe_to_promote"])
        self.assertNotEqual(qfq["preferred_candidate"], "em_qfq")

    def test_em_qfq_materially_fewer_rows_than_sina_does_not_promote(self) -> None:
        rows = compare_source_data("510300", "ETF A", _samples(sina=_frame(300), qfq=_frame(260), none=_frame(300)), latest_expected_date="2025-03-01")
        qfq = _row(rows, "em_qfq")
        self.assertFalse(qfq["safe_to_promote"])
        self.assertIn("row_count", str(qfq["preference_reason"]))

    def test_em_qfq_close_diff_vs_sina_requires_manual_review(self) -> None:
        rows = compare_source_data("510300", "ETF A", _samples(sina=_frame(close_base=10.0), qfq=_frame(close_base=20.0), none=_frame()), latest_expected_date="2024-12-31")
        qfq = _row(rows, "em_qfq")
        self.assertFalse(qfq["safe_to_promote"])
        self.assertTrue(qfq["requires_manual_review"])

    def test_em_none_success_but_qfq_success_prefers_qfq(self) -> None:
        rows = compare_source_data("510300", "ETF A", _samples(sina=_frame(), qfq=_frame(), none=_frame(close_base=11.0)), latest_expected_date="2024-12-31")
        for item in rows:
            self.assertEqual(item["preferred_candidate"], "em_qfq")

    def test_all_sources_fail_preferred_unknown(self) -> None:
        rows = compare_source_data(
            "510300",
            "ETF A",
            _samples(sina_error="sina down", qfq_error="qfq down", none_error="none down"),
            latest_expected_date="2024-12-31",
        )
        for item in rows:
            self.assertEqual(item["preferred_candidate"], "unknown")
            self.assertFalse(item["safe_to_promote"])

    def test_eval_source_preference_does_not_write_formal_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fetchers = {
                "sina_unknown": lambda *_args: _frame(),
                "em_qfq": lambda *_args: _frame(),
                "em_none": lambda *_args: _frame(),
            }
            rows, audit_path, run_dir = run_source_preference_evaluation(
                symbols="510300",
                output_dir=root / "output",
                source_eval_root=root / "data" / "source_eval",
                config_path=root / "missing.yaml",
                fetchers=fetchers,
            )
            self.assertEqual(len(rows), 3)
            self.assertTrue(audit_path.exists())
            self.assertTrue(run_dir.exists())
            self.assertFalse((root / "data" / "cache").exists())
            self.assertFalse((root / "data" / "cache_meta").exists())

    def test_source_preference_audit_schema_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows = compare_source_data("510300", "ETF A", _samples(sina=_frame(), qfq=_frame(), none=_frame()), latest_expected_date="2024-12-31")
            path = write_source_preference_audit(rows, Path(tmp) / "output" / "source_preference_audit.csv")
            validate_output_file_schema(path, "source_preference_audit")
            summary = summarize_source_preference_audit(rows, path)
            self.assertEqual(summary["total_symbols"], 1)
            self.assertEqual(summary["em_qfq_safe_to_promote_count"], 1)

    def test_qa_report_source_preference_summary_not_run_is_parseable(self) -> None:
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
                self.assertIn("source_preference_audit", report["data_layer"])
                self.assertEqual(report["data_layer"]["source_preference_audit"]["status"], "not_run")
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
