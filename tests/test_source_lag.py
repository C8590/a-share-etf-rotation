from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data.candidate_gate import build_candidate_gate_report
from data.data_governance import build_data_governance_status
from data.qa_status import build_qa_status_breakdown, summarize_qa_status, write_qa_status_report
from data.schema import validate_output_file_schema
from data.source_lag import (
    build_source_lag_report,
    merge_source_lag_into_qa_report,
    summarize_source_lag,
    write_source_lag_report,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _sha(path: Path) -> str:
    if path.is_dir():
        digest = hashlib.sha256()
        for item in sorted(path.rglob("*")):
            if item.is_file():
                digest.update(str(item.relative_to(path)).encode())
                digest.update(item.read_bytes())
        return digest.hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SourceLagTest(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        output = root / "output"
        _write_csv(
            output / "data_coverage_report.csv",
            [
                {
                    "symbol": "560000",
                    "name": "智能电车ETF浦银",
                    "success": True,
                    "source": "local_cache",
                    "start_date": "2021-09-30",
                    "end_date": "2026-04-30",
                    "rows": 1108,
                    "status": "passed",
                    "failure_reason": "",
                },
                {
                    "symbol": "510300",
                    "name": "沪深300ETF华泰柏瑞",
                    "success": True,
                    "source": "local_cache",
                    "start_date": "2019-01-02",
                    "end_date": "2026-05-13",
                    "rows": 1782,
                    "status": "passed",
                    "failure_reason": "",
                },
            ],
        )
        _write_csv(
            output / "data_failure_summary.csv",
            [
                {
                    "symbol": "560000",
                    "name": "智能电车ETF浦银",
                    "asset_class": "equity",
                    "category": "sector",
                    "source": "local_cache",
                    "start_date": "2021-09-30",
                    "end_date": "2026-04-30",
                    "row_count": 1108,
                    "latest_expected_date": "2026-05-15",
                    "end_date_gap_days": 15,
                    "failure_type": "stale_end_date",
                    "failure_reason": "end_date is 15 day(s) behind latest_expected_date",
                    "severity": "severe",
                    "suggested_action": "refresh local cache and verify source end-date coverage",
                }
            ],
        )
        _write_csv(
            output / "source_diagnostics_report.csv",
            [
                {"symbol": "560000", "check_type": "akshare_sina", "success": True, "row_count": 1108, "error_type": "", "diagnosis": "ok"},
                {"symbol": "560000", "check_type": "akshare_em_qfq", "success": False, "row_count": 0, "error_type": "ProxyError", "diagnosis": "proxy_or_network_blocked"},
                {"symbol": "560000", "check_type": "akshare_em_none", "success": False, "row_count": 0, "error_type": "ProxyError", "diagnosis": "proxy_or_network_blocked"},
            ],
        )
        _write_csv(
            root / "data" / "cache" / "560000.csv",
            [
                {"date": "2026-04-30", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1, "symbol": "560000", "name": "智能电车ETF浦银", "source": "akshare.fund_etf_hist_sina"}
            ],
        )
        _write_csv(root / "data" / "index_cache" / "000300.csv", [{"date": "2026-05-13", "close": 1}])
        _write_csv(output / "compare_signal.csv", [{"symbol": "510300", "action": "hold"}])
        (output / "compare_signal.txt").write_text("hold\n", encoding="utf-8")
        _write_csv(output / "equity_curve.csv", [{"date": "2026-05-13", "equity": 1}])
        (output / "performance.json").write_text(json.dumps({"total_return": 0}), encoding="utf-8")
        (output / "qa_report.json").write_text(
            json.dumps(
                {
                    "data_layer": {"passed": False, "reasons": ["ETF end-date coverage gap is 15 days"]},
                    "strategy_layer": {"passed": True},
                    "output_layer": {"passed": True},
                    "allow_small_observation": False,
                    "blocking_reasons": ["ETF end-date coverage gap is 15 days"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (output / "data_governance_status.json").write_text(
            json.dumps({"end_date_coverage_gap_days": 15}, ensure_ascii=False),
            encoding="utf-8",
        )
        return output

    def test_build_source_lag_report_classifies_560000_provider_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = self._fixture(Path(tmp))
            rows = build_source_lag_report(output_dir=output)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["symbol"], "560000")
            self.assertEqual(row["source_lag_status"], "provider_stale")
            self.assertEqual(row["eastmoney_qfq_status"], "proxy_blocked")
            self.assertEqual(row["eastmoney_none_status"], "proxy_blocked")
            self.assertEqual(row["sina_end_date"], "2026-04-30")
            self.assertTrue(row["exclude_from_candidate_pool"])
            self.assertNotEqual(row["can_be_fixed_by_refresh"], "true")

    def test_source_lag_schema_and_summary_are_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = self._fixture(Path(tmp))
            rows = build_source_lag_report(output_dir=output)
            report, summary = write_source_lag_report(rows, report_path=output / "source_lag_report.csv", summary_path=output / "source_lag_summary.csv")
            validate_output_file_schema(report, "source_lag_report")
            validate_output_file_schema(summary, "source_lag_summary")
            parsed = summarize_source_lag(report_path=report)
            self.assertEqual(parsed["source_lag_blocker_count"], 1)
            self.assertEqual(parsed["coverage_gap_driver_symbols"], ["560000"])

    def test_qa_status_maps_coverage_gap_to_source_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = self._fixture(Path(tmp))
            rows = build_source_lag_report(output_dir=output)
            write_source_lag_report(rows, report_path=output / "source_lag_report.csv", summary_path=output / "source_lag_summary.csv")
            qa_rows = build_qa_status_breakdown(output_dir=output)
            gap = [row for row in qa_rows if row["qa_item"] == "end_date_coverage_gap"][0]
            self.assertEqual(gap["actionability"], "source_diagnosis")
            self.assertIn(gap["root_cause"], {"provider_stale", "single_symbol_source_lag"})
            self.assertEqual(gap["can_be_fixed_by_refresh"], "maybe_after_source_available")
            breakdown, summary = write_qa_status_report(qa_rows, breakdown_path=output / "qa_status_breakdown.csv", summary_path=output / "qa_status_summary.csv")
            validate_output_file_schema(breakdown, "qa_status_breakdown")
            validate_output_file_schema(summary, "qa_status_summary")
            self.assertEqual(summarize_qa_status(qa_rows)["source_diagnosis_count"], 1)

    def test_candidate_gate_keeps_source_lag_symbol_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = self._fixture(Path(tmp))
            rows = build_source_lag_report(output_dir=output)
            write_source_lag_report(rows, report_path=output / "source_lag_report.csv", summary_path=output / "source_lag_summary.csv")
            candidates = build_candidate_gate_report(output_dir=output)
            row = [item for item in candidates if item["symbol"] == "560000"][0]
            self.assertEqual(row["candidate_status"], "blocked_quality_failed")
            self.assertIn("source_lag_blocker", row["block_reason"])
            self.assertIn("not fixable by ordinary refresh", row["notes"])
            self.assertNotEqual(row["candidate_status"], "eligible")

    def test_source_lag_summary_merges_into_qa_and_governance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = self._fixture(Path(tmp))
            source_rows = build_source_lag_report(output_dir=output)
            report, _ = write_source_lag_report(source_rows, report_path=output / "source_lag_report.csv", summary_path=output / "source_lag_summary.csv")
            summary = summarize_source_lag(report_path=report)
            self.assertTrue(merge_source_lag_into_qa_report(output / "qa_report.json", summary=summary))
            qa = json.loads((output / "qa_report.json").read_text(encoding="utf-8"))
            self.assertEqual(qa["data_layer"]["source_lag"]["source_lag_count"], 1)
            governance = build_data_governance_status(output_dir=output, qa_report=qa)
            self.assertEqual(governance["source_lag_blocker_count"], 1)
            self.assertEqual(governance["coverage_gap_driver_symbols"], ["560000"])

    def test_source_lag_generation_does_not_modify_protected_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = self._fixture(root)
            protected = [
                root / "data" / "cache",
                root / "data" / "index_cache",
                output / "compare_signal.csv",
                output / "compare_signal.txt",
                output / "equity_curve.csv",
                output / "performance.json",
            ]
            before = {str(path): _sha(path) for path in protected}
            rows = build_source_lag_report(output_dir=output)
            write_source_lag_report(rows, report_path=output / "source_lag_report.csv", summary_path=output / "source_lag_summary.csv")
            after = {str(path): _sha(path) for path in protected}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
