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
from data.cache_refresh import (
    build_missing_cache_symbols,
    repair_missing_cache,
    summarize_missing_cache_repair,
)
from data.downloader import DataStatus
from data.schema import validate_output_file_schema


def _write_plan(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _cache_frame(rows: int = 260) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=rows)
    close = pd.Series(range(rows), dtype=float) / 100 + 10.0
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


def _write_cache(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = _cache_frame(260)
    frame["symbol"] = path.stem
    frame["name"] = "Existing"
    frame["source"] = "akshare.fund_etf_hist_sina"
    frame.to_csv(path, index=False, encoding="utf-8-sig")


class MissingCacheRepairTest(unittest.TestCase):
    def test_selects_only_p0_missing_cache_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "output" / "cache_refresh_plan.csv"
            _write_plan(
                plan,
                [
                    {"symbol": "159032", "name": "Missing A", "refresh_priority": "P0_missing_cache"},
                    {"symbol": "510300", "name": "Legacy", "refresh_priority": "P1_legacy_unknown_adjustment"},
                ],
            )
            rows = build_missing_cache_symbols(plan_path=plan)
            self.assertEqual([row["symbol"] for row in rows], ["159032"])

    def test_max_count_over_10_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_missing_cache_symbols(max_count=11, plan_path="missing.csv")

    def test_existing_cache_skips_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            _write_cache(cache / "159032.csv")
            plan = root / "output" / "cache_refresh_plan.csv"
            _write_plan(plan, [{"symbol": "159032", "name": "Missing A", "refresh_priority": "P0_missing_cache"}])
            rows, _ = repair_missing_cache(plan_path=plan, output_dir=root / "output", cache_dir=cache, cache_meta_dir=root / "meta", backup_root=root / "backup")
            self.assertEqual(rows[0]["repair_status"], "skipped_existing_cache")
            self.assertFalse(rows[0]["repair_attempted"])

    def test_dry_run_does_not_write_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "output" / "cache_refresh_plan.csv"
            _write_plan(plan, [{"symbol": "159032", "name": "Missing A", "refresh_priority": "P0_missing_cache"}])
            rows, manifest = repair_missing_cache(
                plan_path=plan,
                output_dir=root / "output",
                cache_dir=root / "cache",
                cache_meta_dir=root / "meta",
                backup_root=root / "backup",
                dry_run=True,
                downloader=lambda **_kwargs: (_cache_frame(), "akshare.fund_etf_hist_sina", {}),
            )
            self.assertIsNone(manifest)
            self.assertFalse((root / "cache" / "159032.csv").exists())
            self.assertEqual(rows[0]["skip_reason"], "dry_run=True")

    def test_success_writes_cache_metadata_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "output" / "cache_refresh_plan.csv"
            _write_plan(plan, [{"symbol": "159032", "name": "Missing A", "refresh_priority": "P0_missing_cache"}])

            rows, manifest = repair_missing_cache(
                plan_path=plan,
                output_dir=root / "output",
                cache_dir=root / "cache",
                cache_meta_dir=root / "meta",
                backup_root=root / "backup",
                downloader=lambda **_kwargs: (
                    _cache_frame(),
                    "akshare.fund_etf_hist_em.qfq",
                    {"fallback_used": True, "fallback_chain": ["akshare.fund_etf_hist_sina", "akshare.fund_etf_hist_em.qfq"]},
                ),
            )
            self.assertEqual(rows[0]["repair_status"], "repaired_ok")
            self.assertTrue((root / "cache" / "159032.csv").exists())
            self.assertTrue((root / "meta" / "159032.json").exists())
            metadata = json.loads((root / "meta" / "159032.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["created_by"], "ETF-GAP-003E")
            self.assertEqual(metadata["adjust"], "qfq")
            self.assertIsNotNone(manifest)
            validate_output_file_schema(root / "output" / "missing_cache_repair_report.csv", "missing_cache_repair_report")

    def test_download_failure_does_not_write_fake_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "output" / "cache_refresh_plan.csv"
            _write_plan(plan, [{"symbol": "159032", "name": "Missing A", "refresh_priority": "P0_missing_cache"}])

            def failing_downloader(**_kwargs: object) -> tuple[pd.DataFrame, str, dict[str, object]]:
                raise RuntimeError("source unavailable")

            rows, _ = repair_missing_cache(
                plan_path=plan,
                output_dir=root / "output",
                cache_dir=root / "cache",
                cache_meta_dir=root / "meta",
                backup_root=root / "backup",
                downloader=failing_downloader,
            )
            self.assertEqual(rows[0]["repair_status"], "download_failed")
            self.assertFalse((root / "cache" / "159032.csv").exists())
            self.assertTrue(rows[0]["still_missing_cache"])

    def test_missing_metadata_after_repair_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "output" / "cache_refresh_plan.csv"
            _write_plan(plan, [{"symbol": "159032", "name": "Missing A", "refresh_priority": "P0_missing_cache"}])
            missing_metadata_path = root / "meta" / "159032.json"
            with patch("data.cache_refresh.write_cache_metadata", return_value=missing_metadata_path):
                rows, _ = repair_missing_cache(
                    plan_path=plan,
                    output_dir=root / "output",
                    cache_dir=root / "cache",
                    cache_meta_dir=root / "meta",
                    backup_root=root / "backup",
                    downloader=lambda **_kwargs: (_cache_frame(), "akshare.fund_etf_hist_sina", {}),
                )
            self.assertEqual(rows[0]["repair_status"], "metadata_missing_after_repair")
            self.assertFalse(rows[0]["metadata_written"])

    def test_explicit_non_missing_symbol_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "output" / "cache_refresh_plan.csv"
            _write_plan(plan, [{"symbol": "510300", "name": "Legacy", "refresh_priority": "P1_legacy_unknown_adjustment"}])
            rows, _ = repair_missing_cache(
                symbols="510300",
                plan_path=plan,
                output_dir=root / "output",
                cache_dir=root / "cache",
                cache_meta_dir=root / "meta",
                backup_root=root / "backup",
                dry_run=True,
            )
            self.assertEqual(rows[0]["repair_status"], "skipped_not_missing_cache")

    def test_summary_not_run_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = summarize_missing_cache_repair(report_path=Path(tmp) / "output" / "missing_cache_repair_report.csv")
            self.assertEqual(summary["status"], "not_run")
            self.assertEqual(summary["attempted_count"], 0)

    def test_qa_report_missing_cache_repair_summary_not_run_is_parseable(self) -> None:
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
                self.assertIn("missing_cache_repair", report["data_layer"])
                self.assertEqual(report["data_layer"]["missing_cache_repair"]["status"], "not_run")
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
