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
from data.source_diagnostics import (
    diagnose_akshare_em_call,
    diagnose_akshare_sina_call,
    diagnose_proxy_environment,
    run_source_diagnostics,
    summarize_source_diagnostics,
    write_source_diagnostics_report,
)


def _frame(rows: int = 260) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=rows)
    close = pd.Series(range(rows), dtype=float) * 0.01 + 10.0
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


class ProxyError(Exception):
    pass


class Timeout(Exception):
    pass


class SourceDiagnosticsTest(unittest.TestCase):
    def test_em_qfq_success(self) -> None:
        ak = SimpleNamespace(fund_etf_hist_em=lambda **_kwargs: _frame())
        rows = diagnose_akshare_em_call("510300", adjust="qfq", run_id="unit", ak_module=ak)
        row = rows[0]
        self.assertEqual(row["check_type"], "akshare_em_qfq")
        self.assertTrue(row["success"])
        self.assertEqual(row["row_count"], 260)
        self.assertEqual(row["diagnosis"], "ok")

    def test_em_qfq_proxy_error(self) -> None:
        def fail(**_kwargs: object) -> pd.DataFrame:
            raise ProxyError("Unable to connect to proxy; Remote end closed connection without response")

        rows = diagnose_akshare_em_call("510300", adjust="qfq", run_id="unit", ak_module=SimpleNamespace(fund_etf_hist_em=fail))
        row = rows[0]
        self.assertFalse(row["success"])
        self.assertEqual(row["error_type"], "ProxyError")
        self.assertEqual(row["diagnosis"], "proxy_or_network_blocked")

    def test_em_none_timeout(self) -> None:
        def fail(**_kwargs: object) -> pd.DataFrame:
            raise Timeout("request timed out")

        rows = diagnose_akshare_em_call("510300", adjust="", run_id="unit", ak_module=SimpleNamespace(fund_etf_hist_em=fail))
        row = rows[0]
        self.assertEqual(row["check_type"], "akshare_em_none")
        self.assertFalse(row["success"])
        self.assertEqual(row["diagnosis"], "timeout_or_endpoint_slow")

    def test_sina_success_but_em_failure_summary(self) -> None:
        def em_fail(**_kwargs: object) -> pd.DataFrame:
            raise ProxyError("proxy failed")

        ak = SimpleNamespace(
            fund_etf_hist_sina=lambda **_kwargs: _frame(),
            fund_etf_hist_em=em_fail,
        )
        rows = []
        rows.extend(diagnose_akshare_sina_call("510300", run_id="unit", ak_module=ak))
        rows.extend(diagnose_akshare_em_call("510300", adjust="qfq", run_id="unit", ak_module=ak))
        rows.extend(diagnose_akshare_em_call("510300", adjust="", run_id="unit", ak_module=ak))
        summary = summarize_source_diagnostics(rows)
        self.assertEqual(summary["sina_success_count"], 1)
        self.assertEqual(summary["em_qfq_success_count"], 0)
        self.assertEqual(summary["em_none_success_count"], 0)
        self.assertEqual(summary["proxy_error_count"], 2)

    def test_proxy_env_detected(self) -> None:
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:7890", "HTTPS_PROXY": "http://127.0.0.1:7890"}, clear=False):
            row = diagnose_proxy_environment("unit")[0]
        self.assertTrue(row["proxy_env_detected"])
        self.assertEqual(row["check_type"], "proxy_env")
        self.assertEqual(row["diagnosis"], "proxy_env_detected")

    def test_diagnose_source_does_not_write_formal_cache(self) -> None:
        class Response:
            status_code = 200

            def json(self) -> dict[str, object]:
                return {"data": {"klines": ["2024-01-02,1,1,1,1"]}}

        ak = SimpleNamespace(
            fund_etf_hist_sina=lambda **_kwargs: _frame(),
            fund_etf_hist_em=lambda **_kwargs: _frame(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                rows, path = run_source_diagnostics(
                    symbols="510300",
                    output_dir=root / "output",
                    config_path=root / "missing.yaml",
                    ak_module=ak,
                    endpoint_getter=lambda *_args, **_kwargs: Response(),
                )
                self.assertEqual(len(rows), 5)
                self.assertTrue(path.exists())
                self.assertFalse((root / "data" / "cache").exists())
                self.assertFalse((root / "data" / "cache_meta").exists())
                validate_output_file_schema(path, "source_diagnostics_report")
            finally:
                os.chdir(old_cwd)

    def test_qa_report_source_diagnostics_not_run_is_parseable(self) -> None:
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
                self.assertIn("source_diagnostics_report", report["data_layer"])
                self.assertEqual(report["data_layer"]["source_diagnostics"]["status"], "not_run")
            finally:
                os.chdir(old_cwd)

    def test_source_diagnostics_summary_schema_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows = diagnose_proxy_environment("unit")
            path = write_source_diagnostics_report(rows, Path(tmp) / "source_diagnostics_report.csv")
            validate_output_file_schema(path, "source_diagnostics_report")
            summary = summarize_source_diagnostics(rows, path)
            self.assertEqual(summary["total_checks"], 1)
            self.assertIn("suggested_action", summary)


if __name__ == "__main__":
    unittest.main()
