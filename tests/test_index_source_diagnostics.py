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
from data.index_source_diagnostics import (
    IndexApiCandidate,
    build_index_diagnostic_targets,
    diagnose_index_api_candidate,
    diagnose_index_source_candidates,
    summarize_index_source_diagnostics,
    write_index_source_diagnostics_report,
)
from data.schema import validate_output_file_schema


def _index_frame(rows: int = 5) -> pd.DataFrame:
    dates = pd.bdate_range("2026-05-04", periods=rows)
    close = pd.Series(range(rows), dtype=float) + 100.0
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1000,
            "amount": 10000,
        }
    )


class ProxyError(Exception):
    pass


class IndexSourceDiagnosticsTest(unittest.TestCase):
    def test_eastmoney_proxy_error_is_classified(self) -> None:
        def fail(_code: str, _start: str, _end: str | None, _ak: object) -> pd.DataFrame:
            raise ProxyError("Unable to connect to proxy; Remote end closed connection without response")

        row = diagnose_index_api_candidate(
            "000300",
            "沪深300",
            api_name="unit.em",
            source_family="eastmoney",
            fetcher=fail,
            latest_expected_date="2026-05-08",
        )
        self.assertFalse(row["call_success"])
        self.assertEqual(row["failure_type"], "proxy_error")
        self.assertFalse(row["usable_as_index_source"])

    def test_candidate_success_is_usable_index_source(self) -> None:
        def ok(_code: str, _start: str, _end: str | None, _ak: object) -> pd.DataFrame:
            return _index_frame()

        row = diagnose_index_api_candidate(
            "000300",
            "沪深300",
            api_name="unit.sina",
            source_family="sina",
            fetcher=ok,
            latest_expected_date="2026-05-08",
        )
        self.assertTrue(row["call_success"])
        self.assertTrue(row["schema_valid"])
        self.assertTrue(row["usable_as_index_source"])
        self.assertFalse(row["requires_manual_review"])

    def test_all_candidates_failed_does_not_write_index_cache(self) -> None:
        def fail(_code: str, _start: str, _end: str | None, _ak: object) -> pd.DataFrame:
            raise RuntimeError("HTTP 503")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                rows = diagnose_index_source_candidates(
                    index_codes="000300",
                    candidates=[IndexApiCandidate("unit.fail", "unknown", fail)],
                )
                path = write_index_source_diagnostics_report(rows, root / "output" / "index_source_diagnostics.csv")
                self.assertTrue(path.exists())
                self.assertFalse((root / "data" / "index_cache").exists())
                self.assertEqual(summarize_index_source_diagnostics(rows)["usable_source_count"], 0)
            finally:
                os.chdir(old_cwd)

    def test_missing_fields_make_schema_invalid(self) -> None:
        def missing_amount(_code: str, _start: str, _end: str | None, _ak: object) -> pd.DataFrame:
            return _index_frame().drop(columns=["amount"])

        row = diagnose_index_api_candidate(
            "000300",
            "沪深300",
            api_name="unit.schema",
            source_family="unknown",
            fetcher=missing_amount,
            latest_expected_date="2026-05-08",
        )
        self.assertTrue(row["call_success"])
        self.assertFalse(row["schema_valid"])
        self.assertEqual(row["failure_type"], "schema_error")
        self.assertIn("amount", row["missing_required_columns"])

    def test_end_date_gap_days_calculation(self) -> None:
        def ok(_code: str, _start: str, _end: str | None, _ak: object) -> pd.DataFrame:
            return _index_frame(rows=3)

        row = diagnose_index_api_candidate(
            "000300",
            "沪深300",
            api_name="unit.gap",
            source_family="sina",
            fetcher=ok,
            latest_expected_date="2026-05-12",
        )
        self.assertEqual(row["end_date"], "2026-05-06")
        self.assertEqual(row["end_date_gap_days"], 6)

    def test_diagnose_index_source_does_not_modify_etf_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "data" / "cache"
            cache_dir.mkdir(parents=True)
            cache_file = cache_dir / "510300.csv"
            original = "date,open,high,low,close,volume,amount,symbol,name,source\n2026-05-08,1,1,1,1,1,1,510300,ETF,local_cache\n"
            cache_file.write_text(original, encoding="utf-8")
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                rows = [
                    diagnose_index_api_candidate(
                        "000300",
                        "沪深300",
                        api_name="unit.ok",
                        source_family="sina",
                        fetcher=lambda *_args: _index_frame(),
                        latest_expected_date="2026-05-08",
                    )
                ]
                with (
                    patch.object(main, "diagnose_index_source_candidates", return_value=rows),
                    patch.object(main, "write_index_source_diagnostics_report", side_effect=lambda value: write_index_source_diagnostics_report(value, root / "output" / "index_source_diagnostics.csv")),
                ):
                    main.command_diagnose_index_source(index_codes="000300", max_count=1)
                self.assertEqual(cache_file.read_text(encoding="utf-8"), original)
                self.assertFalse((root / "data" / "index_cache").exists())
            finally:
                os.chdir(old_cwd)

    def test_schema_and_summary_are_parseable(self) -> None:
        row = diagnose_index_api_candidate(
            "000300",
            "沪深300",
            api_name="unit.ok",
            source_family="sina",
            fetcher=lambda *_args: _index_frame(),
            latest_expected_date="2026-05-08",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_index_source_diagnostics_report([row], Path(tmp) / "index_source_diagnostics.csv")
            validate_output_file_schema(path, "index_source_diagnostics")
            summary = summarize_index_source_diagnostics(report_path=path)
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["usable_source_count"], 1)

    def test_qa_report_index_source_diagnostics_not_run_is_parseable(self) -> None:
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
                self.assertIn("index_source_diagnostics", report["data_layer"])
                self.assertEqual(report["data_layer"]["index_source_diagnostics"]["status"], "not_run")
                validate_output_file_schema(Path("output/qa_report.json"), "qa_report")
            finally:
                os.chdir(old_cwd)

    def test_default_targets_only_high_confidence_codes(self) -> None:
        targets = build_index_diagnostic_targets(index_map_path=Path("missing.csv"), max_count=10)
        self.assertEqual(len(targets), 9)
        self.assertIn({"index_code": "000300", "index_name": "沪深300"}, targets)


if __name__ == "__main__":
    unittest.main()
