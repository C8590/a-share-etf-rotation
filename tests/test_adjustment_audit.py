from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data.adjustment import audit_adjustment_frame, build_adjustment_audit, summarize_adjustment_audit


def _frame(**overrides: object) -> pd.DataFrame:
    data: dict[str, object] = {
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "open": [10.0, 10.1, 10.2],
        "high": [10.2, 10.3, 10.4],
        "low": [9.9, 10.0, 10.1],
        "close": [10.0, 10.1, 10.2],
        "volume": [1000, 1100, 1200],
        "amount": [10000, 11110, 12240],
        "symbol": ["510300", "510300", "510300"],
        "name": ["ETF A", "ETF A", "ETF A"],
        "source": ["akshare.fund_etf_hist_em.qfq", "akshare.fund_etf_hist_em.qfq", "akshare.fund_etf_hist_em.qfq"],
        "adjust": ["qfq", "qfq", "qfq"],
    }
    data.update(overrides)
    return pd.DataFrame(data)


class AdjustmentAuditTest(unittest.TestCase):
    def test_normal_qfq_data_is_ok(self) -> None:
        row = audit_adjustment_frame("510300", "ETF A", _frame(source=["unit-test.qfq"] * 3))
        self.assertEqual(row["adjust"], "qfq")
        self.assertEqual(row["audit_status"], "ok")

    def test_unknown_adjustment_for_sina_source(self) -> None:
        frame = _frame(source=["akshare.fund_etf_hist_sina"] * 3)
        frame = frame.drop(columns=["adjust"])
        row = audit_adjustment_frame("510300", "ETF A", frame)
        self.assertEqual(row["adjust"], "unknown")
        self.assertEqual(row["audit_status"], "warning_unknown_adjustment")

    def test_fallback_used_for_em_qfq_source(self) -> None:
        row = audit_adjustment_frame("510300", "ETF A", _frame().drop(columns=["adjust"]))
        self.assertTrue(row["fallback_used"])
        self.assertEqual(row["audit_status"], "warning_fallback_used")

    def test_abnormal_return_sets_possible_adjustment_context(self) -> None:
        frame = _frame(close=[10.0, 20.5, 20.6], source=["akshare.fund_etf_hist_sina"] * 3)
        frame = frame.drop(columns=["adjust"])
        row = audit_adjustment_frame("510300", "ETF A", frame)
        self.assertEqual(row["audit_status"], "warning_abnormal_return")
        self.assertEqual(row["abnormal_return_count"], 1)
        self.assertTrue(row["possible_adjustment_issue"])

    def test_missing_source_or_missing_adjust_is_error(self) -> None:
        row_missing_source = audit_adjustment_frame("510300", "ETF A", _frame().drop(columns=["source", "adjust"]))
        self.assertEqual(row_missing_source["audit_status"], "error_missing_adjustment")

        row_missing_adjust = audit_adjustment_frame("510300", "ETF A", _frame(source=["custom.vendor"] * 3).drop(columns=["adjust"]))
        self.assertEqual(row_missing_adjust["audit_status"], "error_missing_adjustment")

    def test_mixed_adjustment_detection(self) -> None:
        frame = _frame(adjust=["qfq", "none", "qfq"])
        row = audit_adjustment_frame("510300", "ETF A", frame)
        self.assertEqual(row["audit_status"], "error_mixed_adjustment")

    def test_audit_status_falls_back_to_unknown(self) -> None:
        frame = _frame(source=["custom.vendor"] * 3, adjust=["mystery", "mystery", "mystery"])
        row = audit_adjustment_frame("510300", "ETF A", frame)
        self.assertEqual(row["audit_status"], "unknown")

    def test_build_adjustment_audit_writes_report_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            output_dir = root / "output"
            cache_dir.mkdir()
            _frame().to_csv(cache_dir / "510300.csv", index=False, encoding="utf-8-sig")
            rows = build_adjustment_audit(
                [{"symbol": "510300", "name": "ETF A"}],
                output_dir=output_dir,
                cache_dir=cache_dir,
                cache_meta_dir=root / "meta",
            )
            self.assertTrue((output_dir / "adjustment_audit.csv").exists())
            summary = summarize_adjustment_audit(rows)
            self.assertEqual(summary["total_checked"], 1)
            self.assertEqual(summary["unknown_adjustment_count"], 1)
            self.assertIn("legacy cache without metadata", rows[0]["audit_reason"])


if __name__ == "__main__":
    unittest.main()
