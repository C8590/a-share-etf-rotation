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
from data.trading_calendar import (
    TradingCalendarError,
    audit_trading_calendar,
    get_trading_days,
    is_trading_day,
    latest_trading_day_on_or_before,
    load_local_trading_calendar,
    next_trading_day,
    previous_trading_day,
    save_trading_calendar_snapshot,
)


def _fixture_calendar() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-05-08", "2026-05-11", "2026-05-12", "2026-05-14"],
            "is_open": [True, True, True, True],
            "exchange": ["A_SHARE"] * 4,
            "source": ["unit-test"] * 4,
            "calendar_version": ["test"] * 4,
            "generated_at": ["2026-05-01T00:00:00+08:00"] * 4,
            "note": [""] * 4,
        }
    )


class TradingCalendarTest(unittest.TestCase):
    def _calendar_path(self, root: Path) -> Path:
        return root / "calendar" / "a_share_trading_calendar.csv"

    def test_load_local_trading_calendar_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._calendar_path(Path(tmp))
            save_trading_calendar_snapshot(_fixture_calendar(), path)
            frame = load_local_trading_calendar(path)
            self.assertEqual(len(frame), 4)
            self.assertEqual(str(frame.iloc[0]["date"].date()), "2026-05-08")

    def test_is_trading_day_and_non_trading_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._calendar_path(Path(tmp))
            save_trading_calendar_snapshot(_fixture_calendar(), path)
            self.assertTrue(is_trading_day("2026-05-11", path=path, allow_runtime_refresh=False))
            self.assertFalse(is_trading_day("2026-05-13", path=path, allow_runtime_refresh=False))

    def test_weekend_is_not_trading_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._calendar_path(Path(tmp))
            save_trading_calendar_snapshot(_fixture_calendar(), path)
            self.assertFalse(is_trading_day("2026-05-10", path=path, allow_runtime_refresh=False))

    def test_next_previous_and_latest_trading_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._calendar_path(Path(tmp))
            save_trading_calendar_snapshot(_fixture_calendar(), path)
            self.assertEqual(str(next_trading_day("2026-05-08", path=path, allow_runtime_refresh=False).date()), "2026-05-11")
            self.assertEqual(str(previous_trading_day("2026-05-12", path=path, allow_runtime_refresh=False).date()), "2026-05-11")
            self.assertEqual(str(latest_trading_day_on_or_before("2026-05-13", path=path, allow_runtime_refresh=False).date()), "2026-05-12")

    def test_missing_calendar_without_runtime_refresh_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._calendar_path(Path(tmp))
            with self.assertRaises(FileNotFoundError):
                get_trading_days(path=path, allow_runtime_refresh=False)

    def test_calendar_stale_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._calendar_path(root)
            save_trading_calendar_snapshot(_fixture_calendar(), path)
            row = audit_trading_calendar(output_dir=root / "output", path=path, today="2026-06-01", allow_runtime_refresh=False)
            self.assertEqual(row["status"], "warning_calendar_stale")
            self.assertGreater(row["coverage_gap_days"], 7)

    def test_no_silent_weekday_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._calendar_path(Path(tmp))
            with patch("data.trading_calendar.refresh_a_share_trading_calendar", side_effect=TradingCalendarError("network down")):
                with self.assertRaises(TradingCalendarError):
                    get_trading_days("2026-05-08", "2026-05-15", path=path, allow_runtime_refresh=True, allow_weekday_fallback=False)

                row = audit_trading_calendar(
                    output_dir=Path(tmp) / "output",
                    path=path,
                    today="2026-05-14",
                    allow_runtime_refresh=True,
                    allow_weekday_fallback=True,
                )
                self.assertEqual(row["status"], "warning_weekday_fallback")
                self.assertTrue(row["used_fallback"])

    def test_qa_report_contains_trading_calendar_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                status = DataStatus(symbol="510300", name="ETF A", success=True, rows=3, status="passed")
                data_gate = SimpleNamespace(
                    allow_formal=True,
                    effective_etf_count=1,
                    latest_date="2026-05-14",
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
                            "start_date": "2026-05-08",
                            "end_date": "2026-05-14",
                            "latest_open_day": "2026-05-14",
                            "coverage_gap_days": 0,
                            "used_fallback": False,
                            "reason": "ok",
                        },
                    ),
                    patch.object(main, "build_data_coverage_report", return_value=[status]),
                    patch.object(main, "run_data_quality_checks", return_value=data_gate),
                    patch.object(main, "audit_cache_metadata", return_value=[]),
                    patch.object(main, "build_adjustment_audit", return_value=[]),
                    patch.object(main, "_strategy_qa_rows", return_value=([], [])),
                    patch.object(main, "_ensure_output_file", side_effect=touch_output),
                    patch.object(main, "build_strategy_review", return_value=review),
                ):
                    main.command_qa_check()

                report = json.loads(Path("output/qa_report.json").read_text(encoding="utf-8"))
                self.assertIn("trading_calendar_report", report["data_layer"])
                self.assertEqual(report["data_layer"]["trading_calendar"]["status"], "ok")
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
