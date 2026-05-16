from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import main


def _price_cache_text(symbol: str = "510300") -> str:
    return "\n".join(
        [
            "date,open,high,low,close,volume,amount,symbol,name,source",
            f"2026-05-13,1.0,1.1,0.9,1.0,1000,1000,{symbol},ETF A,local_cache",
            f"2026-05-14,1.0,1.1,0.9,1.0,1000,1000,{symbol},ETF A,local_cache",
            "",
        ]
    )


class QaCheckReadonlyTest(unittest.TestCase):
    def _write_formal_outputs(self, root: Path) -> list[Path]:
        output = root / "output"
        output.mkdir(parents=True, exist_ok=True)
        files = [
            output / "compare_signal.csv",
            output / "compare_signal.txt",
            output / "equity_curve.csv",
            output / "performance.json",
            output / "benchmark_report.csv",
            output / "oos_results.csv",
            output / "walk_forward_results.csv",
        ]
        for path in files:
            path.write_text(f"sentinel {path.name}\n", encoding="utf-8")
            os.utime(path, (1_700_000_000, 1_700_000_000))
        return files[:4]

    def _readonly_qa_patches(self, *, pool: list[dict[str, str]]):
        data_gate = SimpleNamespace(
            allow_formal=False,
            reasons=["data quality failed for 1 ETF(s)", "ETF end-date coverage gap is 15 days"],
            effective_etf_count=0,
            latest_date="2026-05-14",
            failure_summary=[],
        )
        quality_summary = {
            "short_history_count": 0,
            "stale_cache_count": 0,
            "severe_quality_issue_count": 0,
            "candidate_excluded_count": 0,
            "manual_review_required_count": 0,
            "refresh_needed_count": 0,
            "top_blocking_reasons": [],
            "top_examples": [],
        }
        observation_summary = {
            "total_observation_count": 0,
            "very_short_history_count": 0,
            "low_liquidity_watch_count": 0,
            "manual_review_required_count": 0,
            "estimated_eligible_within_20d_count": 0,
            "estimated_eligible_within_60d_count": 0,
            "unknown_estimate_count": 0,
        }
        manual_summary = {
            "manual_review_count": 0,
            "p0_manual_review_count": 0,
            "abnormal_return_review_count": 0,
            "low_liquidity_review_count": 0,
            "very_short_history_review_count": 0,
        }
        governance_status = {
            "allowed_to_enter_008b": False,
            "allowed_to_enter_007b": False,
            "next_recommended_action": "unit",
            "blocking_reasons": ["data quality failed for 1 ETF(s)", "ETF end-date coverage gap is 15 days"],
        }
        return [
            patch.object(main, "load_etf_pool", return_value=pool),
            patch.object(main, "audit_trading_calendar", return_value={"status": "ok"}),
            patch.object(main, "run_data_quality_checks", return_value=data_gate),
            patch.object(main, "audit_cache_metadata", return_value=[]),
            patch.object(main, "build_adjustment_audit", return_value=[]),
            patch.object(main, "build_refresh_plan", return_value=[]),
            patch.object(main, "build_quality_remediation_plan", return_value=[]),
            patch.object(main, "summarize_quality_diagnosis", return_value=quality_summary),
            patch.object(main, "build_candidate_gate_report", return_value=[]),
            patch.object(main, "summarize_candidate_gate", return_value={"candidate_total": 0}),
            patch.object(main, "build_short_history_observation_pool", return_value=[]),
            patch.object(main, "summarize_observation_pool", return_value=observation_summary),
            patch.object(main, "build_manual_review_list", return_value=[]),
            patch.object(main, "summarize_manual_review", return_value=manual_summary),
            patch.object(main, "_strategy_qa_rows", return_value=([], [])),
            patch.object(
                main,
                "build_strategy_review",
                return_value=pd.DataFrame([{"strategy_name": "unit", "strategy_status": "recommended_for_observation"}]),
            ),
            patch.object(main, "summarize_trading_calendar_audit", return_value={"status": "ok"}),
            patch.object(main, "summarize_failure_summary", return_value={"total_failed": 0}),
            patch.object(main, "summarize_cache_metadata_audit", return_value={"total_cache_files": 0}),
            patch.object(main, "summarize_adjustment_audit", return_value={"total_checked": 0}),
            patch.object(main, "summarize_refresh_plan", return_value={"total_candidates": 0}),
            patch.object(main, "summarize_pilot_refresh", return_value={"status": "not_run"}),
            patch.object(main, "summarize_missing_cache_repair", return_value={"status": "not_run"}),
            patch.object(main, "summarize_source_preference_audit", return_value={"status": "not_run"}),
            patch.object(main, "summarize_source_diagnostics", return_value={"status": "not_run"}),
            patch.object(main, "summarize_etf_metadata", return_value={"status": "not_run"}),
            patch.object(main, "summarize_index_data", return_value={"usable_benchmark_count": 0}),
            patch.object(main, "summarize_index_source_diagnostics", return_value={"status": "not_run"}),
            patch.object(main, "summarize_etf_metrics", return_value={"status": "not_run"}),
            patch.object(main, "summarize_factor_score", return_value={"status": "not_run"}),
            patch.object(main, "build_data_governance_status", return_value=governance_status),
            patch.object(main, "command_backtest", side_effect=AssertionError("qa-check must not rebuild performance")),
            patch.object(main, "command_benchmark", side_effect=AssertionError("qa-check must not rebuild benchmark")),
            patch.object(main, "command_oos_test", side_effect=AssertionError("qa-check must not rebuild oos")),
            patch.object(main, "command_walk_forward", side_effect=AssertionError("qa-check must not rebuild walk-forward")),
            patch.object(main, "command_compare_signal", side_effect=AssertionError("qa-check must not rebuild compare_signal")),
            patch("data.downloader.load_market_etf_universe", side_effect=RuntimeError("unit test skips universe snapshot")),
            patch("data.downloader.save_etf_data", side_effect=AssertionError("qa-check must not write ETF cache")),
            patch("data.downloader.download_etf_history", side_effect=AssertionError("qa-check must not download ETF data")),
            patch("data.storage.write_cache_metadata", side_effect=AssertionError("qa-check must not write cache metadata")),
        ]

    def _run_qa_check(self, root: Path, *, pool: list[dict[str, str]]) -> None:
        old_cwd = Path.cwd()
        os.chdir(root)
        patches = self._readonly_qa_patches(pool=pool)
        started = []
        try:
            for item in patches:
                started.append(item.start())
            with self.assertRaises(SystemExit) as raised:
                main.command_qa_check()
            self.assertEqual(raised.exception.code, 1)
        finally:
            for item in reversed(patches):
                item.stop()
            os.chdir(old_cwd)

    def test_qa_check_does_not_write_cache_index_cache_or_formal_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_file = root / "data" / "cache" / "510300.csv"
            index_file = root / "data" / "index_cache" / "000300.csv"
            meta_file = root / "data" / "cache_meta" / "510300.json"
            cache_file.parent.mkdir(parents=True)
            index_file.parent.mkdir(parents=True)
            meta_file.parent.mkdir(parents=True)
            cache_file.write_text(_price_cache_text(), encoding="utf-8")
            index_file.write_text("date,close\n2026-05-14,1\n", encoding="utf-8")
            meta_file.write_text('{"symbol":"510300"}\n', encoding="utf-8")
            formal_outputs = self._write_formal_outputs(root)
            watched = [cache_file, index_file, meta_file, *formal_outputs]
            for path in watched:
                os.utime(path, (1_700_000_000, 1_700_000_000))
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}

            self._run_qa_check(root, pool=[{"symbol": "510300", "name": "ETF A"}])

            after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}
            self.assertEqual(after, before)
            self.assertTrue((root / "output" / "qa_report.json").exists())
            self.assertTrue((root / "output" / "data_coverage_report.csv").exists())

    def test_qa_check_reports_missing_cache_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "cache").mkdir(parents=True)
            (root / "data" / "index_cache").mkdir(parents=True)
            self._write_formal_outputs(root)

            self._run_qa_check(root, pool=[{"symbol": "159999", "name": "Missing ETF"}])

            self.assertFalse((root / "data" / "cache" / "159999.csv").exists())
            coverage = pd.read_csv(root / "output" / "data_coverage_report.csv", dtype={"symbol": str}).fillna("")
            row = coverage.iloc[0].to_dict()
            self.assertEqual(str(row["symbol"]).zfill(6), "159999")
            self.assertFalse(bool(row["success"]))
            self.assertIn("not found", str(row["failure_reason"]))


if __name__ == "__main__":
    unittest.main()
