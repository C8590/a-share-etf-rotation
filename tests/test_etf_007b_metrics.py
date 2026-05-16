from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import main
from data.etf_007b import (
    build_007b_small_scope_report,
    classify_007b_metric_status,
    summarize_007b_small_scope,
    write_007b_small_scope_report,
)
from data.schema import validate_output_file_schema
from tests.test_index_007b_readiness import _base_qa_report, _base_status


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _metric_row(
    symbol: str,
    *,
    benchmark_status: str,
    tracking_error_status: str,
    benchmark_available: bool = False,
    tracking_error: object = "",
    relative: object = "",
    benchmark_return: object = "",
    failure_reason: str = "",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": f"ETF {symbol}",
        "category": "ETF",
        "sub_category": "index",
        "tracking_index_code": "000300" if benchmark_status != "missing_benchmark" else "unable_to_confirm",
        "tracking_index_name": "index",
        "benchmark_available": benchmark_available,
        "benchmark_status": benchmark_status,
        "metric_status": "ok" if tracking_error_status == "ok" else "unable_to_compute",
        "tracking_error": tracking_error,
        "tracking_error_status": tracking_error_status,
        "relative_return_20d": relative,
        "relative_return_60d": relative,
        "relative_return_120d": relative,
        "benchmark_return_20d": benchmark_return,
        "benchmark_return_60d": benchmark_return,
        "benchmark_return_120d": benchmark_return,
        "etf_return_20d": 0.01,
        "etf_return_60d": 0.02,
        "etf_return_120d": 0.03,
        "discount_premium": "",
        "discount_premium_status": "source_unavailable",
        "fund_size": "",
        "management_fee": "",
        "custody_fee": "",
        "latest_amount": "",
        "computed_at": "2026-05-16T00:00:00+08:00",
        "data_start_date": "2020-01-01" if benchmark_status != "missing_benchmark" else "",
        "data_end_date": "2026-05-15" if benchmark_status != "missing_benchmark" else "",
        "benchmark_start_date": "2020-01-01" if benchmark_available else "",
        "benchmark_end_date": "2026-05-15" if benchmark_available else "",
        "failure_reason": failure_reason,
        "notes": "unit",
    }


def _write_fixture(root: Path) -> None:
    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    _base_qa_report(output)
    _base_status(output)
    readiness = {
        "index_007b_readiness_report": "output/index_007b_readiness.csv",
        "index_007b_unlock_plan_report": "output/index_007b_unlock_plan.csv",
        "index_007b_readiness_summary_report": "output/index_007b_readiness_summary.csv",
        "readiness_status": "ready_small_scope",
        "allowed_to_enter_007b": True,
        "allowed_to_enter_007b_scope": "small_scope",
        "full_scope_available": False,
        "blocking_items": [],
        "warning_items": ["missing_benchmark_count"],
        "usable_benchmark_count": 1,
        "index_cache_valid_count": 1,
        "tracking_error_computable_count": 1,
        "relative_return_computable_count": 1,
        "top_blockers": [],
        "next_recommended_action": "unit",
    }
    report = json.loads((output / "qa_report.json").read_text(encoding="utf-8"))
    report["data_layer"]["index_007b_readiness"] = readiness
    (output / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(
        output / "index_007b_readiness.csv",
        [
            {
                "readiness_item": "usable_benchmark_count",
                "current_status": "passed",
                "passed": True,
                "blocking": False,
                "severity": "info",
                "threshold": ">0",
                "actual_value": 1,
                "blocker_type": "benchmark_dependency",
                "dependency": "unit",
                "remediation_action": "unit",
                "prerequisite_task": "unit",
                "estimated_path": "unit",
                "can_be_resolved_by_network": False,
                "can_be_resolved_by_index_update": False,
                "can_be_resolved_by_manual_mapping": False,
                "can_be_resolved_by_schema_fix": False,
                "notes": "unit",
            },
            {
                "readiness_item": "missing_benchmark_count",
                "current_status": "warning",
                "passed": False,
                "blocking": False,
                "severity": "warning",
                "threshold": "0 for full-scope",
                "actual_value": 1,
                "blocker_type": "mapping_unconfirmed",
                "dependency": "unit",
                "remediation_action": "unit",
                "prerequisite_task": "unit",
                "estimated_path": "unit",
                "can_be_resolved_by_network": False,
                "can_be_resolved_by_index_update": False,
                "can_be_resolved_by_manual_mapping": True,
                "can_be_resolved_by_schema_fix": False,
                "notes": "unit",
            },
        ],
    )
    _write_csv(
        output / "etf_metrics.csv",
        [
            _metric_row("510300", benchmark_status="ok", tracking_error_status="ok", benchmark_available=True, tracking_error=0.01, relative=0.02, benchmark_return=0.03),
            _metric_row("159915", benchmark_status="no_index_cache", tracking_error_status="no_index_cache", failure_reason="missing index cache"),
            _metric_row("159001", benchmark_status="missing_benchmark", tracking_error_status="missing_benchmark", failure_reason="no confirmed benchmark mapping"),
            _metric_row("510500", benchmark_status="ok", tracking_error_status="insufficient_overlap", benchmark_available=True, failure_reason="overlap_days=10 < min_overlap_days=60"),
        ],
    )


class ETF007BMetricsTest(unittest.TestCase):
    def test_classify_statuses(self) -> None:
        self.assertEqual(
            classify_007b_metric_status(
                _metric_row("510300", benchmark_status="ok", tracking_error_status="ok", benchmark_available=True, tracking_error=0.01, relative=0.02, benchmark_return=0.03)
            ),
            "computed_valid",
        )
        self.assertEqual(classify_007b_metric_status(_metric_row("159915", benchmark_status="no_index_cache", tracking_error_status="no_index_cache")), "no_index_cache")
        self.assertEqual(classify_007b_metric_status(_metric_row("159001", benchmark_status="missing_benchmark", tracking_error_status="missing_benchmark")), "missing_benchmark")
        self.assertEqual(classify_007b_metric_status(_metric_row("510500", benchmark_status="ok", tracking_error_status="insufficient_overlap", benchmark_available=True)), "insufficient_overlap")

    def test_build_report_keeps_unavailable_rows_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            rows = build_007b_small_scope_report(output_dir=root / "output", etf_cache_dir=root / "data" / "cache", index_cache_dir=root / "data" / "index_cache")
            by_symbol = {row["symbol"]: row for row in rows}
            self.assertEqual(by_symbol["510300"]["validation_status"], "computed_valid")
            self.assertEqual(by_symbol["510300"]["computation_status"], "computed_valid")
            self.assertNotEqual(by_symbol["510300"]["tracking_error"], "")
            self.assertNotEqual(by_symbol["510300"]["relative_return_60d"], "")
            self.assertEqual(by_symbol["159915"]["validation_status"], "no_index_cache")
            self.assertEqual(by_symbol["159915"]["tracking_error"], "")
            self.assertEqual(by_symbol["159001"]["validation_status"], "missing_benchmark")
            self.assertEqual(by_symbol["159001"]["relative_return_20d"], "")
            self.assertEqual(by_symbol["510500"]["validation_status"], "insufficient_overlap")

    def test_schema_summary_and_qa_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            rows = build_007b_small_scope_report(output_dir=root / "output")
            report_path, summary_path = write_007b_small_scope_report(
                rows,
                report_path=root / "output" / "etf_007b_metrics_report.csv",
                summary_path=root / "output" / "etf_007b_metrics_summary.csv",
                readiness_summary={"allowed_to_enter_007b_scope": "small_scope", "full_scope_available": False},
            )
            validate_output_file_schema(report_path, "etf_007b_metrics_report")
            validate_output_file_schema(summary_path, "etf_007b_metrics_summary")
            summary = summarize_007b_small_scope(
                rows,
                report_path=report_path,
                readiness_summary={"allowed_to_enter_007b_scope": "small_scope", "full_scope_available": False},
            )
            self.assertEqual(summary["computed_valid_count"], 1)
            self.assertEqual(summary["scope"], "small_scope")
            self.assertFalse(summary["full_scope_available"])

    def test_command_is_readonly_and_merges_qa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root)
            watched = [
                root / "data" / "cache" / "510300.csv",
                root / "data" / "index_cache" / "000300.csv",
                root / "output" / "compare_signal.csv",
                root / "output" / "compare_signal.txt",
                root / "output" / "equity_curve.csv",
                root / "output" / "performance.json",
            ]
            for path in watched:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"sentinel {path.name}\n", encoding="utf-8")
                os.utime(path, (1_700_000_000, 1_700_000_000))
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                summary = main.command_validate_etf_007b_metrics()
            finally:
                os.chdir(old_cwd)
            self.assertEqual(summary["computed_valid_count"], 1)
            self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}, before)
            validate_output_file_schema(root / "output" / "etf_007b_metrics_report.csv", "etf_007b_metrics_report")
            validate_output_file_schema(root / "output" / "etf_007b_metrics_summary.csv", "etf_007b_metrics_summary")
            validate_output_file_schema(root / "output" / "qa_report.json", "qa_report")
            merged = json.loads((root / "output" / "qa_report.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["data_layer"]["etf_007b_metrics"]["scope"], "small_scope")
            self.assertFalse(merged["data_layer"]["etf_007b_metrics"]["full_scope_available"])


if __name__ == "__main__":
    unittest.main()
