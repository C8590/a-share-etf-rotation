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
from data.etf_metrics import (
    compute_benchmark_return,
    compute_etf_metrics,
    compute_relative_return,
    compute_tracking_error,
    load_benchmark_for_etf,
    summarize_etf_metrics,
    write_etf_metrics_report,
)
from data.schema import validate_output_file_schema


def _metadata(symbols: list[str] | None = None) -> pd.DataFrame:
    rows = []
    for symbol in symbols or ["510300", "159915"]:
        rows.append(
            {
                "symbol": symbol,
                "name": f"ETF {symbol}",
                "category": "ETF",
                "sub_category": "index",
                "fund_size": "",
                "management_fee": "",
                "custody_fee": "",
                "latest_amount": "1000",
            }
        )
    return pd.DataFrame(rows)


def _index_map(symbol: str = "510300", code: str = "000300", usable: bool = True) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "etf_name": f"ETF {symbol}",
                "category": "ETF",
                "sub_category": "index",
                "tracking_index_name": f"INDEX {code}",
                "tracking_index_code": code,
                "index_source": "unit",
                "mapping_method": "config_manual" if usable else "unable_to_confirm",
                "confidence": 0.95 if usable else 0.0,
                "requires_manual_review": False if usable else True,
                "usable_as_benchmark": usable,
                "notes": "",
            }
        ]
    )


def _history(symbol: str = "510300", rows: int = 80, start: float = 100.0, step: float = 0.2, *, index: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=rows)
    close = pd.Series([start + i * step for i in range(rows)], dtype=float)
    frame = pd.DataFrame(
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
    if index:
        frame["index_code"] = symbol
        frame["index_name"] = f"INDEX {symbol}"
        frame["source"] = "unit.index"
    else:
        frame["symbol"] = symbol
        frame["name"] = f"ETF {symbol}"
        frame["source"] = "unit.etf"
    return frame


def _write_inputs(root: Path, *, symbols: list[str] | None = None, map_frame: pd.DataFrame | None = None) -> None:
    (root / "output").mkdir(parents=True, exist_ok=True)
    (root / "data" / "cache").mkdir(parents=True, exist_ok=True)
    (root / "data" / "index_cache").mkdir(parents=True, exist_ok=True)
    _metadata(symbols).to_csv(root / "output" / "etf_metadata.csv", index=False, encoding="utf-8-sig")
    (map_frame if map_frame is not None else _index_map()).to_csv(root / "output" / "index_map.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(columns=["tracking_index_code"]).to_csv(root / "output" / "index_data_coverage.csv", index=False, encoding="utf-8-sig")


class EtfMetricsTest(unittest.TestCase):
    def test_no_index_cache_marks_tracking_error_unable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_inputs(root, symbols=["510300"])
            _history("510300").to_csv(root / "data" / "cache" / "510300.csv", index=False, encoding="utf-8-sig")
            metrics, _coverage = compute_etf_metrics(
                metadata_path=root / "output" / "etf_metadata.csv",
                index_map_path=root / "output" / "index_map.csv",
                index_coverage_path=root / "output" / "index_data_coverage.csv",
                etf_cache_dir=root / "data" / "cache",
                index_cache_dir=root / "data" / "index_cache",
            )
            row = metrics.iloc[0]
            self.assertEqual(row["benchmark_status"], "no_index_cache")
            self.assertEqual(row["tracking_error_status"], "no_index_cache")
            self.assertEqual(row["tracking_error"], "")
            self.assertNotEqual(row["etf_return_20d"], "")

    def test_missing_benchmark_mapping_does_not_compute_tracking_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_inputs(root, symbols=["159915"], map_frame=_index_map("159915", "399006", usable=False))
            _history("159915").to_csv(root / "data" / "cache" / "159915.csv", index=False, encoding="utf-8-sig")
            metrics, _coverage = compute_etf_metrics(
                metadata_path=root / "output" / "etf_metadata.csv",
                index_map_path=root / "output" / "index_map.csv",
                etf_cache_dir=root / "data" / "cache",
                index_cache_dir=root / "data" / "index_cache",
            )
            row = metrics.iloc[0]
            self.assertEqual(row["benchmark_status"], "missing_benchmark")
            self.assertEqual(row["tracking_error_status"], "missing_benchmark")
            self.assertEqual(row["relative_return_20d"], "")

    def test_insufficient_overlap_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_inputs(root, symbols=["510300"])
            _history("510300", rows=20).to_csv(root / "data" / "cache" / "510300.csv", index=False, encoding="utf-8-sig")
            _history("000300", rows=20, index=True).to_csv(root / "data" / "index_cache" / "000300.csv", index=False, encoding="utf-8-sig")
            metrics, _coverage = compute_etf_metrics(
                metadata_path=root / "output" / "etf_metadata.csv",
                index_map_path=root / "output" / "index_map.csv",
                etf_cache_dir=root / "data" / "cache",
                index_cache_dir=root / "data" / "index_cache",
                min_overlap_days=60,
            )
            self.assertEqual(metrics.iloc[0]["tracking_error_status"], "insufficient_overlap")

    def test_tracking_error_relative_and_benchmark_returns_compute_with_samples(self) -> None:
        etf = _history("510300", rows=80, step=0.3)
        bench = _history("000300", rows=80, step=0.2, index=True)
        te = compute_tracking_error(etf, bench, min_overlap_days=20)
        rel = compute_relative_return(etf, bench, 20)
        bench_ret = compute_benchmark_return(bench, 20)
        self.assertEqual(te["status"], "ok")
        self.assertGreater(float(te["value"]), 0.0)
        self.assertEqual(rel["status"], "ok")
        self.assertNotEqual(rel["value"], "")
        self.assertEqual(bench_ret["status"], "ok")
        self.assertNotEqual(bench_ret["value"], "")

    def test_discount_premium_without_nav_iopv_is_source_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_inputs(root, symbols=["510300"])
            _history("510300").to_csv(root / "data" / "cache" / "510300.csv", index=False, encoding="utf-8-sig")
            _history("000300", index=True).to_csv(root / "data" / "index_cache" / "000300.csv", index=False, encoding="utf-8-sig")
            metrics, _coverage = compute_etf_metrics(
                metadata_path=root / "output" / "etf_metadata.csv",
                index_map_path=root / "output" / "index_map.csv",
                etf_cache_dir=root / "data" / "cache",
                index_cache_dir=root / "data" / "index_cache",
                min_overlap_days=20,
            )
            self.assertIn(metrics.iloc[0]["discount_premium_status"], {"source_unavailable", "not_applicable"})

    def test_metric_report_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_inputs(root, symbols=["510300"])
            _history("510300").to_csv(root / "data" / "cache" / "510300.csv", index=False, encoding="utf-8-sig")
            metrics, coverage = compute_etf_metrics(
                metadata_path=root / "output" / "etf_metadata.csv",
                index_map_path=root / "output" / "index_map.csv",
                etf_cache_dir=root / "data" / "cache",
                index_cache_dir=root / "data" / "index_cache",
            )
            metrics_path, coverage_path = write_etf_metrics_report(
                metrics,
                coverage,
                metrics_path=root / "output" / "etf_metrics.csv",
                coverage_path=root / "output" / "etf_metrics_coverage.csv",
            )
            validate_output_file_schema(metrics_path, "etf_metrics")
            validate_output_file_schema(coverage_path, "etf_metrics_coverage")
            summary = summarize_etf_metrics(metrics_path=metrics_path, coverage_path=coverage_path)
            self.assertEqual(summary["total_etfs"], 1)

    def test_qa_report_etf_metrics_not_run_is_parseable(self) -> None:
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
                self.assertIn("etf_metrics", report["data_layer"])
                self.assertEqual(report["data_layer"]["etf_metrics"]["status"], "not_run")
                validate_output_file_schema(Path("output/qa_report.json"), "qa_report")
            finally:
                os.chdir(old_cwd)

    def test_compute_command_does_not_modify_etf_or_index_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_inputs(root, symbols=["510300"])
            etf_path = root / "data" / "cache" / "510300.csv"
            index_path = root / "data" / "index_cache" / "000300.csv"
            _history("510300").to_csv(etf_path, index=False, encoding="utf-8-sig")
            _history("000300", index=True).to_csv(index_path, index=False, encoding="utf-8-sig")
            etf_before = etf_path.read_text(encoding="utf-8-sig")
            index_before = index_path.read_text(encoding="utf-8-sig")
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                main.command_compute_etf_metrics(max_count=1, min_overlap_days=20)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(etf_path.read_text(encoding="utf-8-sig"), etf_before)
            self.assertEqual(index_path.read_text(encoding="utf-8-sig"), index_before)

    def test_load_benchmark_rejects_name_inferred_mapping(self) -> None:
        row = {
            "tracking_index_code": "000300",
            "usable_as_benchmark": True,
            "requires_manual_review": False,
            "mapping_method": "name_inferred",
        }
        _frame, status, _reason = load_benchmark_for_etf(row, index_cache_dir=Path("missing"))
        self.assertEqual(status, "missing_benchmark")


if __name__ == "__main__":
    unittest.main()
