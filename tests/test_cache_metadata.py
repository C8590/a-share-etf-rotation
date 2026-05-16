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
from data.adjustment import audit_cache_metadata, build_adjustment_audit
from data.downloader import DataStatus
from data.storage import build_cache_metadata, load_cache_metadata, save_etf_data, write_cache_metadata


def _frame(source: str = "akshare.fund_etf_hist_em.qfq") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [10.0, 10.1, 10.2],
            "high": [10.2, 10.3, 10.4],
            "low": [9.9, 10.0, 10.1],
            "close": [10.0, 10.1, 10.2],
            "volume": [1000, 1100, 1200],
            "amount": [10000, 11110, 12240],
            "symbol": ["510300", "510300", "510300"],
            "name": ["ETF A", "ETF A", "ETF A"],
            "source": [source, source, source],
        }
    )


class CacheMetadataTest(unittest.TestCase):
    def _write_cache(self, root: Path, symbol: str = "510300", source: str = "akshare.fund_etf_hist_em.qfq") -> Path:
        cache_dir = root / "cache"
        return save_etf_data(symbol, _frame(source), data_dir=cache_dir, name="ETF A", source=source)

    def test_qfq_metadata_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_file = self._write_cache(root, source="akshare.fund_etf_hist_em.qfq")
            metadata = build_cache_metadata("510300", _frame(), name="ETF A", source="akshare.fund_etf_hist_em.qfq", cache_file=cache_file)
            write_cache_metadata("510300", metadata, root / "meta")
            loaded = load_cache_metadata("510300", root / "meta")
            self.assertEqual(loaded["adjust"], "qfq")
            self.assertEqual(loaded["download_method"], "akshare_em_chunked_qfq")

    def test_none_metadata_write(self) -> None:
        metadata = build_cache_metadata("510300", _frame("akshare.fund_etf_hist_em.none"), name="ETF A", source="akshare.fund_etf_hist_em.none")
        self.assertEqual(metadata["adjust"], "none")
        self.assertEqual(metadata["download_method"], "akshare_em_chunked_none")

    def test_sina_metadata_write_is_unknown(self) -> None:
        metadata = build_cache_metadata("510300", _frame("akshare.fund_etf_hist_sina"), name="ETF A", source="akshare.fund_etf_hist_sina")
        self.assertEqual(metadata["adjust"], "unknown")
        self.assertEqual(metadata["download_method"], "akshare_sina")

    def test_fallback_used_write(self) -> None:
        metadata = build_cache_metadata(
            "510300",
            _frame("akshare.fund_etf_hist_em.qfq"),
            name="ETF A",
            source="akshare.fund_etf_hist_em.qfq",
            fallback_used=True,
            fallback_chain=["akshare.fund_etf_hist_sina", "akshare.fund_etf_hist_em.qfq"],
        )
        self.assertTrue(metadata["fallback_used"])
        self.assertEqual(metadata["fallback_chain"], ["akshare.fund_etf_hist_sina", "akshare.fund_etf_hist_em.qfq"])

    def test_legacy_cache_without_metadata_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_cache(root)
            pool = [{"symbol": "510300", "name": "ETF A"}]
            rows = audit_cache_metadata(pool, output_dir=root / "output", cache_dir=root / "cache", cache_meta_dir=root / "meta")
            self.assertEqual(rows[0]["status"], "warning_legacy_cache_without_metadata")

            adjustment_rows = build_adjustment_audit(pool, output_dir=root / "output", cache_dir=root / "cache", cache_meta_dir=root / "meta")
            self.assertEqual(adjustment_rows[0]["adjust"], "unknown")
            self.assertEqual(adjustment_rows[0]["audit_status"], "warning_unknown_adjustment")
            self.assertIn("legacy cache without metadata", adjustment_rows[0]["audit_reason"])

    def test_metadata_cache_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_file = self._write_cache(root)
            metadata = build_cache_metadata("510300", _frame(), name="ETF A", source="akshare.fund_etf_hist_em.qfq", cache_file=cache_file, row_count=99)
            write_cache_metadata("510300", metadata, root / "meta")
            rows = audit_cache_metadata([{"symbol": "510300", "name": "ETF A"}], output_dir=root / "output", cache_dir=root / "cache", cache_meta_dir=root / "meta")
            self.assertEqual(rows[0]["status"], "warning_metadata_cache_mismatch")
            self.assertIn("row_count", rows[0]["reason"])

    def test_missing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = audit_cache_metadata([{"symbol": "510300", "name": "ETF A"}], output_dir=root / "output", cache_dir=root / "cache", cache_meta_dir=root / "meta")
            self.assertEqual(rows[0]["status"], "error_missing_cache")

    def test_qa_report_contains_cache_metadata_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                status = DataStatus(symbol="510300", name="ETF A", success=True, rows=3, status="passed")
                data_gate = SimpleNamespace(
                    allow_formal=True,
                    effective_etf_count=1,
                    latest_date="2024-01-04",
                    reasons=[],
                    failure_summary=[],
                )
                review = pd.DataFrame([{"strategy_name": "unit", "strategy_status": "recommended_for_observation"}])

                def touch_output(path: str, _builder: object) -> None:
                    output = Path(path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text("ok", encoding="utf-8")

                with (
                    patch.object(main, "load_etf_pool", return_value=[{"symbol": "510300", "name": "ETF A"}]),
                    patch.object(
                        main,
                        "audit_trading_calendar",
                        return_value={
                            "calendar_file": "data/calendar/a_share_trading_calendar.csv",
                            "status": "ok",
                            "source": "unit-test",
                            "start_date": "2024-01-02",
                            "end_date": "2024-01-04",
                            "latest_open_day": "2024-01-04",
                            "coverage_gap_days": 0,
                            "used_fallback": False,
                            "reason": "ok",
                        },
                    ),
                    patch.object(main, "build_data_coverage_report", return_value=[status]),
                    patch.object(main, "run_data_quality_checks", return_value=data_gate),
                    patch.object(
                        main,
                        "audit_cache_metadata",
                        return_value=[
                            {
                                "symbol": "510300",
                                "name": "ETF A",
                                "cache_file": "data/cache/510300.csv",
                                "metadata_file": "data/cache_meta/510300.json",
                                "metadata_exists": False,
                                "adjust": "unknown",
                                "status": "warning_legacy_cache_without_metadata",
                                "reason": "legacy cache without metadata",
                            }
                        ],
                    ),
                    patch.object(main, "build_adjustment_audit", return_value=[]),
                    patch.object(main, "_strategy_qa_rows", return_value=([], [])),
                    patch.object(main, "_ensure_output_file", side_effect=touch_output),
                    patch.object(main, "build_strategy_review", return_value=review),
                ):
                    main.command_qa_check()

                report = json.loads(Path("output/qa_report.json").read_text(encoding="utf-8"))
                self.assertIn("cache_metadata_audit_report", report["data_layer"])
                self.assertIn("cache_metadata_audit", report["data_layer"])
                self.assertEqual(report["data_layer"]["cache_metadata_audit"]["legacy_cache_without_metadata_count"], 1)
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
