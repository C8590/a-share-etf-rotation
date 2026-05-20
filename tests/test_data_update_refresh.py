from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from data.downloader import update_all_data_incremental, update_many_etfs, update_one_etf
from data.storage import get_csv_path, load_etf_data, save_etf_data
from data.trading_calendar import resolve_signal_context


def _daily_frame(start: str = "2026-05-11", periods: int = 3) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=periods)
    return pd.DataFrame(
        {
            "date": dates,
            "open": [1.0 + i * 0.01 for i in range(len(dates))],
            "high": [1.1 + i * 0.01 for i in range(len(dates))],
            "low": [0.9 + i * 0.01 for i in range(len(dates))],
            "close": [1.05 + i * 0.01 for i in range(len(dates))],
            "volume": [1000 + i for i in range(len(dates))],
            "amount": [10000 + i * 10 for i in range(len(dates))],
        }
    )


class DataUpdateRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_symbol_formats_share_one_cache_path(self) -> None:
        paths = {get_csv_path(value) for value in ["510210", "510210.SH", "sh510210"]}
        self.assertEqual(paths, {Path("data/cache/510210.csv")})

    def test_small_sample_update_writes_three_success_rows(self) -> None:
        pool = [
            {"symbol": "510210", "name": "上证指数ETF富国"},
            {"symbol": "560000", "name": "样本ETF"},
            {"symbol": "510300", "name": "沪深300ETF"},
        ]

        def fake_download(symbol: str, start_date: str, end_date: str | None = None, retries: int = 3, retry_delay: float = 2.0, **kwargs):
            frame = _daily_frame(periods=3)
            frame["source"] = None
            return frame, f"fake.{symbol}"

        with (
            patch("data.downloader.download_etf_history", side_effect=fake_download),
            patch("data.downloader.refresh_daily_data_after_close", return_value=[]),
            patch("data.downloader.load_market_etf_universe", return_value=pd.DataFrame(pool)),
        ):
            statuses = update_many_etfs(pool, "20260101", end_date="2026-05-13", mode="refresh", max_workers=1)

        sample = {item.symbol: item for item in statuses}
        self.assertEqual(set(sample), {"510210", "560000", "510300"})
        self.assertTrue(all(item.status in {"success", "cold_start"} for item in statuses))
        self.assertTrue(all(item.local_latest_date == item.latest_date for item in statuses))
        self.assertTrue(Path("output/data_coverage_report.csv").exists())

    def test_download_failure_with_cache_is_cached_success(self) -> None:
        save_etf_data("510210.SH", _daily_frame(periods=2), name="上证指数ETF富国", source="seed")

        with patch("data.downloader.download_etf_history", side_effect=RuntimeError("network unavailable")):
            status = update_one_etf(
                {"symbol": "sh510210", "name": "上证指数ETF富国"},
                "20260101",
                "2026-05-13",
                mode="refresh",
            )

        self.assertTrue(status.success)
        self.assertEqual(status.status, "cached_success")
        self.assertIn("联网更新失败", status.error)
        self.assertEqual(load_etf_data("510210").index.max().date().isoformat(), status.latest_date)

    def test_empty_source_without_cache_is_failed_with_real_error(self) -> None:
        with patch("data.downloader.download_etf_history", side_effect=RuntimeError("akshare.fund_etf_hist_em.none: source returned empty DataFrame")):
            status = update_one_etf(
                {"symbol": "510300", "name": "沪深300ETF"},
                "20260101",
                "2026-05-13",
                mode="refresh",
            )

        self.assertFalse(status.success)
        self.assertEqual(status.status, "failed")
        self.assertIn("source returned empty DataFrame", status.error)

    def test_latest_after_refresh_uses_local_cutoff_not_execute_day(self) -> None:
        calendar = pd.DatetimeIndex(["2026-05-12", "2026-05-13", "2026-05-14"])
        context = resolve_signal_context(
            selected_signal_date=None,
            mode="latest_after_refresh",
            now=datetime(2026, 5, 14, 16, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            data_cutoff_date="2026-05-13",
            trading_calendar=calendar,
        )

        self.assertEqual(context.actual_signal_date.isoformat(), "2026-05-13")
        self.assertEqual(context.execution_date.isoformat(), "2026-05-14")
        self.assertIn("数据源尚未更新", context.data_mode)

    def test_frontend_helpers_handle_all_failed_progress(self) -> None:
        try:
            import streamlit  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("streamlit is not installed in this environment")
        from app import _business_error_frame, _progress_markdown

        text = _progress_markdown(
            {
                "stage": "下载 / 更新行情",
                "current": 94,
                "total": 1160,
                "success_count": 0,
                "failed_count": 91,
                "error": "akshare 接口字段变化",
            }
        )
        self.assertIn("失败：91", text)
        self.assertIn("akshare 接口字段变化", text)

        business, details = _business_error_frame(pd.DataFrame([{"symbol": "510210.SH", "name": "上证指数ETF富国", "success": False, "error": "接口返回空 DataFrame"}]))
        self.assertEqual(business.iloc[0]["ETF代码"], "510210")
        self.assertFalse(details.empty)

    def test_incremental_current_cache_does_not_call_download(self) -> None:
        save_etf_data("510300", _daily_frame(start="2026-05-11", periods=3), name="沪深300ETF", source="seed")
        with patch("data.downloader.download_etf_history") as mocked:
            status = update_one_etf(
                {"symbol": "510300", "name": "沪深300ETF"},
                "20190101",
                "2026-05-13",
                mode="incremental",
            )
        mocked.assert_not_called()
        self.assertTrue(status.success)
        self.assertEqual(status.status, "up_to_date")

    def test_incremental_lagged_cache_downloads_recent_window_only(self) -> None:
        save_etf_data("510300", _daily_frame(start="2026-05-07", periods=3), name="沪深300ETF", source="seed")
        calls: list[tuple[str, str]] = []

        def fake_download(symbol: str, start_date: str, end_date: str | None = None, **kwargs):
            calls.append((start_date, str(end_date)))
            frame = _daily_frame(start="2026-05-12", periods=2)
            return frame, "fake"

        with patch("data.downloader.download_etf_history", side_effect=fake_download):
            status = update_one_etf(
                {"symbol": "510300", "name": "沪深300ETF"},
                "20190101",
                "2026-05-13",
                mode="incremental",
                max_lookback_days=5,
            )
        self.assertEqual(status.status, "success")
        self.assertTrue(calls)
        self.assertGreater(calls[0][0], "20190101")
        self.assertLessEqual(calls[0][0], "20260513")

    def test_incremental_cache_latest_20260516_does_not_download_from_20260430(self) -> None:
        frame = _daily_frame(start="2026-05-14", periods=2)
        frame.loc[len(frame)] = [pd.Timestamp("2026-05-16"), 1.2, 1.3, 1.1, 1.25, 2000, 20000]
        save_etf_data("510300", frame, name="HS300 ETF", source="seed")
        calls: list[tuple[str, str, str]] = []

        def fake_download(symbol: str, start_date: str, end_date: str | None = None, **kwargs):
            calls.append((symbol, start_date, str(end_date)))
            return _daily_frame(start="2026-05-18", periods=2), "fake"

        calendar = pd.DatetimeIndex(pd.bdate_range("2026-04-01", "2026-05-31"))
        with (
            patch("data.downloader.load_a_share_trading_calendar", return_value=calendar),
            patch("data.downloader.download_etf_history", side_effect=fake_download),
        ):
            status = update_one_etf(
                {"symbol": "510300", "name": "HS300 ETF"},
                "20190101",
                "2026-05-19",
                mode="incremental",
            )

        self.assertEqual(status.status, "success")
        self.assertEqual(calls[0][1], "20260518")
        self.assertNotEqual(calls[0][1], "20260430")

    def test_missing_etf_does_not_drag_other_symbol_latest_date_back(self) -> None:
        save_etf_data("510300", _daily_frame(start="2026-05-14", periods=2), name="HS300 ETF", source="seed")
        pool = [
            {"symbol": "510300", "name": "HS300 ETF"},
            {"symbol": "159999", "name": "Missing ETF"},
        ]

        def fake_download(symbol: str, start_date: str, end_date: str | None = None, **kwargs):
            if symbol == "159999":
                raise RuntimeError("network unavailable")
            return _daily_frame(start="2026-05-18", periods=1), "fake"

        with (
            patch("data.downloader.download_etf_history", side_effect=fake_download),
            patch("data.downloader.load_market_etf_universe", return_value=pd.DataFrame(pool)),
        ):
            statuses = update_many_etfs(pool, "20190101", end_date="2026-05-15", mode="incremental", max_workers=1)

        by_symbol = {item.symbol: item for item in statuses}
        self.assertEqual(by_symbol["510300"].status, "up_to_date")
        self.assertEqual(by_symbol["510300"].latest_date, "2026-05-15")
        self.assertEqual(by_symbol["159999"].status, "failed")

    def test_download_writeback_can_be_read_as_new_latest_date(self) -> None:
        save_etf_data("510300", _daily_frame(start="2026-05-11", periods=2), name="HS300 ETF", source="seed")

        def fake_download(symbol: str, start_date: str, end_date: str | None = None, **kwargs):
            return _daily_frame(start="2026-05-13", periods=3), "fake"

        with patch("data.downloader.download_etf_history", side_effect=fake_download):
            status = update_one_etf(
                {"symbol": "510300", "name": "HS300 ETF"},
                "20190101",
                "2026-05-15",
                mode="incremental",
            )

        self.assertEqual(status.latest_date, "2026-05-15")
        self.assertEqual(load_etf_data("510300").index.max().date().isoformat(), "2026-05-15")

    def test_incremental_target_is_not_downgraded_by_old_metadata_majority(self) -> None:
        pool = [
            {"symbol": "510300", "name": "HS300 ETF"},
            {"symbol": "510500", "name": "CSI500 ETF"},
            {"symbol": "159915", "name": "ChiNext ETF"},
        ]
        for item in pool[:2]:
            save_etf_data(item["symbol"], _daily_frame(start="2026-04-28", periods=3), name=item["name"], source="seed")
        save_etf_data("159915", _daily_frame(start="2026-05-14", periods=2), name="ChiNext ETF", source="seed")
        calls: list[tuple[str, str, str]] = []

        def fake_download(symbol: str, start_date: str, end_date: str | None = None, **kwargs):
            calls.append((symbol, start_date, str(end_date)))
            return _daily_frame(start="2026-05-18", periods=1), "fake"

        with (
            patch("data.downloader.download_etf_history", side_effect=fake_download),
            patch("data.downloader.load_market_etf_universe", return_value=pd.DataFrame(pool)),
        ):
            update_all_data_incremental(pool, expected_signal_date="2026-05-18", max_workers=1)

        self.assertTrue(calls)
        self.assertTrue(all(end_date == "20260518" for _, _, end_date in calls))

    def test_cached_failure_is_not_retried_again_same_day(self) -> None:
        save_etf_data("560000", _daily_frame(start="2026-04-28", periods=3), name="Lagged ETF", source="seed")
        with patch("data.downloader.download_etf_history", side_effect=RuntimeError("network unavailable")):
            first = update_one_etf(
                {"symbol": "560000", "name": "Lagged ETF"},
                "20190101",
                "2026-05-19",
                mode="incremental",
            )
        self.assertEqual(first.status, "cached_success")

        with (
            patch("data.downloader.download_etf_history", side_effect=AssertionError("不应重复联网下载")) as mocked_download,
            patch("data.downloader.load_market_etf_universe", return_value=pd.DataFrame([{"symbol": "560000", "name": "Lagged ETF"}])),
        ):
            statuses = update_many_etfs(
                [{"symbol": "560000", "name": "Lagged ETF"}],
                "20190101",
                end_date="2026-05-19",
                mode="incremental",
                max_workers=1,
            )

        mocked_download.assert_not_called()
        self.assertEqual(statuses[0].status, "cached_success")

    def test_incremental_failure_without_cache_is_failed(self) -> None:
        with patch("data.downloader.download_etf_history", side_effect=RuntimeError("network unavailable")):
            status = update_one_etf(
                {"symbol": "510300", "name": "沪深300ETF"},
                "20190101",
                "2026-05-13",
                mode="incremental",
            )
        self.assertFalse(status.success)
        self.assertEqual(status.status, "failed")

    def test_update_data_default_mode_is_incremental(self) -> None:
        from main import parse_args
        import sys

        with patch.object(sys, "argv", ["main.py", "update-data"]):
            args = parse_args()
        self.assertEqual(args.mode, "incremental")
        self.assertFalse(args.full_refresh)

    def test_full_refresh_flag_is_explicit(self) -> None:
        from main import parse_args
        import sys

        with patch.object(sys, "argv", ["main.py", "update-data", "--full-refresh"]):
            args = parse_args()
        self.assertTrue(args.full_refresh)

    def test_metadata_current_cache_skips_csv_scan(self) -> None:
        save_etf_data("510300", _daily_frame(start="2026-05-11", periods=3), name="沪深300ETF", source="seed")
        with (
            patch("data.downloader.load_etf_data") as mocked_load,
            patch("data.downloader.download_etf_history") as mocked_download,
            patch("data.downloader.load_market_etf_universe", return_value=pd.DataFrame([{"symbol": "510300", "name": "沪深300ETF"}])),
        ):
            statuses = update_many_etfs(
                [{"symbol": "510300", "name": "沪深300ETF"}],
                "20190101",
                end_date="2026-05-13",
                mode="incremental",
                max_workers=1,
            )
        mocked_load.assert_not_called()
        mocked_download.assert_not_called()
        self.assertEqual(statuses[0].status, "up_to_date")

    def test_source_circuit_breaker_opens_after_threshold(self) -> None:
        from data.downloader import SourceCircuitBreaker

        breaker = SourceCircuitBreaker(threshold=2, cooldown_seconds=300)
        self.assertTrue(breaker.allow("source.a"))
        breaker.record_failure("source.a")
        self.assertTrue(breaker.allow("source.a"))
        breaker.record_failure("source.a")
        self.assertFalse(breaker.allow("source.a"))


if __name__ == "__main__":
    unittest.main()
