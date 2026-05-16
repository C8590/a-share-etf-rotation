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
    backup_cache_file,
    backup_metadata_file,
    build_pilot_refresh_symbols,
    compare_refreshed_cache,
    run_pilot_refresh,
    summarize_pilot_refresh,
)
from data.downloader import DataStatus


def _old_cache(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03"],
            "open": [10.0, 10.0],
            "high": [10.1, 10.1],
            "low": [9.9, 9.9],
            "close": [10.0, 10.0],
            "volume": [1000, 1000],
            "amount": [10000, 10000],
            "symbol": ["510300", "510300"],
            "name": ["ETF A", "ETF A"],
            "source": ["akshare.fund_etf_hist_sina", "akshare.fund_etf_hist_sina"],
        }
    ).to_csv(path, index=False, encoding="utf-8-sig")


def _new_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [10.0, 10.1, 10.2],
            "high": [10.2, 10.3, 10.4],
            "low": [9.9, 10.0, 10.1],
            "close": [10.0, 10.2, 10.3],
            "volume": [1000, 1100, 1200],
            "amount": [10000, 11220, 12360],
        }
    )


def _write_plan(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


class PilotRefreshTest(unittest.TestCase):
    def test_requires_pool_or_symbols(self) -> None:
        with self.assertRaises(ValueError):
            build_pilot_refresh_symbols(plan_path="missing.csv")

    def test_max_count_over_11_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_pilot_refresh_symbols(symbols="510300", max_count=12, plan_path="missing.csv")

    def test_manual_review_required_skips_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "output" / "cache_refresh_plan.csv"
            _write_plan(
                plan,
                [
                    {
                        "symbol": "510300",
                        "name": "ETF A",
                        "refresh_reason": "possible_adjustment_issue",
                        "requires_manual_review": True,
                        "current_adjust": "unknown",
                    }
                ],
            )
            rows, manifest = run_pilot_refresh(
                symbols="510300",
                plan_path=plan,
                output_dir=root / "output",
                cache_dir=root / "cache",
                cache_meta_dir=root / "meta",
                backup_root=root / "backup",
            )
            self.assertIsNotNone(manifest)
            self.assertEqual(rows[0]["refresh_status"], "skipped_manual_review")
            self.assertTrue(rows[0]["refresh_skipped"])

    def test_backup_cache_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            meta = root / "meta"
            backup = root / "backup"
            _old_cache(cache / "510300.csv")
            meta.mkdir()
            (meta / "510300.json").write_text('{"symbol":"510300","adjust":"unknown"}\n', encoding="utf-8")
            cache_result = backup_cache_file("510300", backup, cache)
            meta_result = backup_metadata_file("510300", backup, meta)
            self.assertTrue(cache_result["copied"])
            self.assertTrue(meta_result["copied"])
            self.assertTrue((backup / "cache" / "510300.csv").exists())
            self.assertTrue((backup / "cache_meta" / "510300.json").exists())

    def test_refresh_writes_manifest_report_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            meta = root / "meta"
            plan = root / "output" / "cache_refresh_plan.csv"
            _old_cache(cache / "510300.csv")
            meta.mkdir()
            (meta / "510300.json").write_text('{"symbol":"510300","adjust":"unknown"}\n', encoding="utf-8")
            _write_plan(
                plan,
                [
                    {
                        "symbol": "510300",
                        "name": "ETF A",
                        "refresh_reason": "legacy_cache_without_metadata;unknown_adjustment",
                        "requires_manual_review": False,
                        "current_adjust": "unknown",
                    }
                ],
            )

            def fake_downloader(**_kwargs: object) -> tuple[pd.DataFrame, str, dict[str, object]]:
                return _new_frame(), "akshare.fund_etf_hist_em.qfq", {"fallback_used": False, "fallback_chain": ["akshare.fund_etf_hist_em.qfq"]}

            rows, manifest = run_pilot_refresh(
                symbols="510300",
                plan_path=plan,
                output_dir=root / "output",
                cache_dir=cache,
                cache_meta_dir=meta,
                backup_root=root / "backup",
                downloader=fake_downloader,
            )
            self.assertEqual(rows[0]["refresh_status"], "refreshed_ok")
            self.assertTrue(rows[0]["metadata_written"])
            self.assertTrue(rows[0]["backup_created"])
            self.assertIsNotNone(manifest)
            manifest_data = json.loads(Path(manifest).read_text(encoding="utf-8"))
            self.assertEqual(manifest_data["symbols"], ["510300"])
            metadata = json.loads((meta / "510300.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["adjust"], "qfq")
            self.assertEqual(metadata["created_by"], "ETF-GAP-003D")
            summary = summarize_pilot_refresh(rows, root / "output" / "pilot_refresh_report.csv")
            self.assertEqual(summary["metadata_written_count"], 1)

    def test_compare_refreshed_cache(self) -> None:
        before = pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "close": [10.0, 10.0]})
        after = pd.DataFrame({"date": ["2024-01-02", "2024-01-03", "2024-01-04"], "close": [10.0, 10.2, 10.3]})
        comparison = compare_refreshed_cache(before, after)
        self.assertEqual(comparison["row_count_delta"], 1)
        self.assertGreater(comparison["max_close_abs_diff"], 0)
        self.assertTrue(comparison["end_date_improved"])

    def test_new_metadata_exists_sets_metadata_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            meta = root / "meta"
            plan = root / "output" / "cache_refresh_plan.csv"
            _old_cache(cache / "510300.csv")
            _write_plan(plan, [{"symbol": "510300", "name": "ETF A", "refresh_reason": "legacy_cache_without_metadata", "requires_manual_review": False}])

            def fake_downloader(**_kwargs: object) -> tuple[pd.DataFrame, str, dict[str, object]]:
                return _new_frame(), "akshare.fund_etf_hist_em.qfq", {}

            rows, _ = run_pilot_refresh(
                symbols="510300",
                plan_path=plan,
                output_dir=root / "output",
                cache_dir=cache,
                cache_meta_dir=meta,
                backup_root=root / "backup",
                downloader=fake_downloader,
            )
            self.assertTrue((meta / "510300.json").exists())
            self.assertTrue(rows[0]["metadata_written"])

    def test_dry_run_does_not_modify_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            plan = root / "output" / "cache_refresh_plan.csv"
            cache_file = cache / "510300.csv"
            _old_cache(cache_file)
            original = cache_file.read_text(encoding="utf-8-sig")
            _write_plan(plan, [{"symbol": "510300", "name": "ETF A", "refresh_reason": "legacy_cache_without_metadata", "requires_manual_review": False}])
            rows, manifest = run_pilot_refresh(
                symbols="510300",
                plan_path=plan,
                output_dir=root / "output",
                cache_dir=cache,
                cache_meta_dir=root / "meta",
                backup_root=root / "backup",
                dry_run=True,
                downloader=lambda **_kwargs: (_new_frame(), "akshare.fund_etf_hist_em.qfq", {}),
            )
            self.assertIsNone(manifest)
            self.assertEqual(cache_file.read_text(encoding="utf-8-sig"), original)
            self.assertEqual(rows[0]["skip_reason"], "dry_run=True")

    def test_over_limit_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "output" / "cache_refresh_plan.csv"
            rows = [{"symbol": f"51030{i}", "name": f"ETF {i}", "refresh_reason": "legacy_cache_without_metadata", "requires_manual_review": False} for i in range(3)]
            _write_plan(plan, rows)
            result, _ = run_pilot_refresh(
                symbols="510300,510301,510302",
                max_count=2,
                plan_path=plan,
                output_dir=root / "output",
                cache_dir=root / "cache",
                cache_meta_dir=root / "meta",
                backup_root=root / "backup",
                dry_run=True,
            )
            self.assertEqual(result[2]["refresh_status"], "skipped_over_limit")

    def test_qa_report_pilot_refresh_summary_not_run_is_parseable(self) -> None:
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
                self.assertIn("pilot_refresh", report["data_layer"])
                self.assertEqual(report["data_layer"]["pilot_refresh"]["status"], "not_run")
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
