from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import yaml

import main
from data.downloader import DataStatus
from data.index_data import (
    IndexApiCandidate,
    build_index_map,
    infer_index_candidates,
    normalize_index_history,
    summarize_index_data,
    update_index_data,
    validate_index_history,
    write_index_data_coverage,
    write_index_map,
)
from data.schema import validate_index_cache_frame, validate_output_file_schema


def _metadata_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["510300", "159915", "588000", "518880"],
            "name": ["沪深300ETF华泰柏瑞", "创业板ETF易方达", "科创50ETF华夏", "黄金ETF华安"],
            "category": ["ETF", "ETF", "ETF", "ETF"],
            "sub_category": ["指数型-股票", "指数型-股票", "指数型-股票", "商品型"],
            "tracking_index_name": ["沪深300", "unable_to_confirm", "unable_to_confirm", "unable_to_confirm"],
            "tracking_index_code": ["000300", "unable_to_confirm", "unable_to_confirm", "unable_to_confirm"],
            "metadata_source": ["unit.metadata", "unit.metadata", "unit.metadata", "unit.metadata"],
        }
    )


def _history_frame(rows: int = 5) -> pd.DataFrame:
    dates = pd.bdate_range("2026-05-01", periods=rows)
    close = pd.Series(range(rows), dtype=float) + 10.0
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": close,
            "最高": close + 0.2,
            "最低": close - 0.2,
            "收盘": close,
            "成交量": 1000,
            "成交额": 10000,
        }
    )


def _history_frame_english(rows: int = 5) -> pd.DataFrame:
    dates = pd.bdate_range("2026-05-01", periods=rows)
    close = pd.Series(range(rows), dtype=float) + 10.0
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 1000,
            "amount": 10000,
        }
    )


class IndexDataTest(unittest.TestCase):
    def test_index_map_standardization_and_metadata_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "index_map.yaml"
            config.write_text(
                yaml.safe_dump(
                    {
                        "mappings": [
                            {
                                "symbol": "510300",
                                "tracking_index_name": "wrong manual",
                                "tracking_index_code": "999999",
                                "mapping_method": "config_manual",
                                "confidence": 0.9,
                                "requires_manual_review": False,
                            },
                            {
                                "symbol": "159915",
                                "tracking_index_name": "创业板指",
                                "tracking_index_code": "399006",
                                "mapping_method": "config_manual",
                                "confidence": 0.95,
                                "requires_manual_review": False,
                            },
                        ]
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            frame = build_index_map(_metadata_frame(), config_path=config)
        row_510300 = frame[frame["symbol"].eq("510300")].iloc[0]
        self.assertEqual(row_510300["mapping_method"], "metadata_exact")
        self.assertEqual(row_510300["tracking_index_code"], "000300")
        self.assertTrue(row_510300["usable_as_benchmark"])
        row_159915 = frame[frame["symbol"].eq("159915")].iloc[0]
        self.assertEqual(row_159915["mapping_method"], "config_manual")
        self.assertTrue(row_159915["usable_as_benchmark"])

    def test_name_inferred_defaults_to_manual_review(self) -> None:
        candidates = infer_index_candidates("创业板ETF易方达")
        self.assertEqual(candidates[0]["mapping_method"], "name_inferred")
        self.assertTrue(candidates[0]["requires_manual_review"])
        frame = build_index_map(_metadata_frame().iloc[[1]], config_path=Path("missing.yaml"))
        row = frame.iloc[0]
        self.assertEqual(row["mapping_method"], "name_inferred")
        self.assertTrue(row["requires_manual_review"])
        self.assertFalse(row["usable_as_benchmark"])

    def test_unable_to_confirm_is_not_benchmark(self) -> None:
        frame = build_index_map(_metadata_frame().iloc[[3]], config_path=Path("missing.yaml"))
        row = frame.iloc[0]
        self.assertEqual(row["mapping_method"], "unable_to_confirm")
        self.assertFalse(row["usable_as_benchmark"])

    def test_index_history_normalization(self) -> None:
        frame = normalize_index_history(_history_frame(), index_code="000300", index_name="沪深300", source="unit")
        self.assertEqual(list(frame.columns), ["date", "open", "high", "low", "close", "volume", "amount", "index_code", "index_name", "source"])
        self.assertEqual(frame["index_code"].iloc[0], "000300")
        validate_index_cache_frame(frame, "index cache fixture")

    def test_index_history_missing_columns_fails(self) -> None:
        bad = _history_frame().drop(columns=["成交额"])
        with self.assertRaises(ValueError):
            normalize_index_history(bad, index_code="000300", index_name="沪深300", source="unit")

    def test_index_history_coverage_calculation(self) -> None:
        frame = normalize_index_history(_history_frame(), index_code="000300", index_name="沪深300", source="unit")
        checked = validate_index_history(frame, latest_expected_date="2026-05-08")
        self.assertTrue(checked["fetch_success"])
        self.assertEqual(checked["row_count"], 5)
        self.assertEqual(checked["end_date_gap_days"], 1)
        self.assertEqual(checked["quality_status"], "ok")

    def test_output_schema_for_index_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index_map = build_index_map(_metadata_frame().iloc[[0]], config_path=Path("missing.yaml"))
            map_path = write_index_map(index_map, Path(tmp) / "index_map.csv")
            coverage_path = write_index_data_coverage(
                [
                    {
                        "tracking_index_code": "000300",
                        "tracking_index_name": "沪深300",
                        "index_source": "unit",
                        "api_name": "unit.fetch",
                        "source_family": "unit",
                        "fetch_success": True,
                        "schema_valid": True,
                        "start_date": "2026-05-01",
                        "end_date": "2026-05-08",
                        "row_count": 5,
                        "latest_expected_date": "2026-05-08",
                        "end_date_gap_days": 0,
                        "missing_required_columns": "",
                        "missing_values_count": 0,
                        "duplicate_dates_count": 0,
                        "abnormal_return_count": 0,
                        "quality_status": "ok",
                        "usable_as_benchmark": True,
                        "requires_manual_review": False,
                        "failure_reason": "",
                        "notes": "",
                    }
                ],
                Path(tmp) / "index_data_coverage.csv",
            )
            validate_output_file_schema(map_path, "index_map")
            validate_output_file_schema(coverage_path, "index_data_coverage")

    def test_qa_report_index_data_not_run_is_parseable(self) -> None:
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
                self.assertIn("index_data", report["data_layer"])
                self.assertEqual(report["data_layer"]["index_data"]["status"], "not_run")
                self.assertIn("csindex_success_count", report["data_layer"]["index_data"])
                validate_output_file_schema(Path("output/qa_report.json"), "qa_report")
            finally:
                os.chdir(old_cwd)

    def test_csindex_success_writes_index_cache(self) -> None:
        class Ak:
            def stock_zh_index_hist_csindex(self, **_kwargs: object) -> pd.DataFrame:
                return _history_frame_english()

            def index_zh_a_hist(self, **_kwargs: object) -> pd.DataFrame:
                raise AssertionError("CSIndex should be attempted first")

            def stock_zh_index_daily_em(self, **_kwargs: object) -> pd.DataFrame:
                raise AssertionError("CSIndex success should not need EM fallback")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            _metadata_frame().iloc[[0]].to_csv(root / "output" / "etf_metadata.csv", index=False, encoding="utf-8-sig")
            (root / "config").mkdir()
            (root / "config" / "index_map.yaml").write_text("mappings: []\n", encoding="utf-8")
            index_map, rows, _, _ = update_index_data(
                metadata_path=root / "output" / "etf_metadata.csv",
                config_path=root / "config" / "index_map.yaml",
                output_dir=root / "output",
                cache_dir=root / "data" / "index_cache",
                ak_module=Ak(),
            )
            self.assertEqual(int(index_map["usable_as_benchmark"].sum()), 1)
            self.assertTrue((root / "data" / "index_cache" / "000300.csv").exists())
            self.assertEqual(rows[0]["api_name"], "akshare.stock_zh_index_hist_csindex")
            self.assertEqual(rows[0]["source_family"], "csindex")
            self.assertTrue(rows[0]["schema_valid"])
            self.assertTrue(rows[0]["usable_as_benchmark"])

    def test_csindex_missing_field_does_not_write_cache(self) -> None:
        class Ak:
            def stock_zh_index_hist_csindex(self, **_kwargs: object) -> pd.DataFrame:
                return _history_frame_english().drop(columns=["amount"])

            def index_zh_a_hist(self, **_kwargs: object) -> pd.DataFrame:
                raise AssertionError("schema-invalid CSIndex response should be audited, not patched from EM")

            def stock_zh_index_daily_em(self, **_kwargs: object) -> pd.DataFrame:
                raise AssertionError("schema-invalid CSIndex response should be audited, not patched from EM")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            _metadata_frame().iloc[[0]].to_csv(root / "output" / "etf_metadata.csv", index=False, encoding="utf-8-sig")
            (root / "config").mkdir()
            (root / "config" / "index_map.yaml").write_text("mappings: []\n", encoding="utf-8")
            _, rows, _, _ = update_index_data(
                metadata_path=root / "output" / "etf_metadata.csv",
                config_path=root / "config" / "index_map.yaml",
                output_dir=root / "output",
                cache_dir=root / "data" / "index_cache",
                ak_module=Ak(),
            )
            self.assertFalse((root / "data" / "index_cache" / "000300.csv").exists())
            self.assertTrue(rows[0]["fetch_success"])
            self.assertFalse(rows[0]["schema_valid"])
            self.assertFalse(rows[0]["usable_as_benchmark"])
            self.assertEqual(rows[0]["missing_required_columns"], "amount")

    def test_eastmoney_failure_can_fallback_to_csindex_when_ordered(self) -> None:
        def em_fail(_code: str, _start: str, _end: str | None, _ak: object) -> pd.DataFrame:
            raise RuntimeError("proxy failed")

        def csindex_ok(_code: str, _start: str, _end: str | None, _ak: object) -> pd.DataFrame:
            return _history_frame_english()

        candidates = [
            IndexApiCandidate("akshare.index_zh_a_hist", "eastmoney", em_fail),
            IndexApiCandidate("akshare.stock_zh_index_hist_csindex", "csindex", csindex_ok),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            _metadata_frame().iloc[[0]].to_csv(root / "output" / "etf_metadata.csv", index=False, encoding="utf-8-sig")
            (root / "config").mkdir()
            (root / "config" / "index_map.yaml").write_text("mappings: []\n", encoding="utf-8")
            _, rows, _, coverage_path = update_index_data(
                metadata_path=root / "output" / "etf_metadata.csv",
                config_path=root / "config" / "index_map.yaml",
                output_dir=root / "output",
                cache_dir=root / "data" / "index_cache",
                ak_module=object(),
                candidates=candidates,
            )
            self.assertTrue(rows[0]["usable_as_benchmark"])
            self.assertIn("eastmoney_failures=1", rows[0]["notes"])
            summary = summarize_index_data(index_map_path=root / "output" / "index_map.csv", coverage_path=coverage_path)
            self.assertEqual(summary["eastmoney_failure_count"], 1)

    def test_all_sources_failed_records_coverage_failure(self) -> None:
        class Ak:
            def stock_zh_index_hist_csindex(self, **_kwargs: object) -> pd.DataFrame:
                raise RuntimeError("csindex unsupported")

            def index_zh_a_hist(self, **_kwargs: object) -> pd.DataFrame:
                raise RuntimeError("em proxy")

            def stock_zh_index_daily_em(self, **_kwargs: object) -> pd.DataFrame:
                raise RuntimeError("em proxy")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            _metadata_frame().iloc[[0]].to_csv(root / "output" / "etf_metadata.csv", index=False, encoding="utf-8-sig")
            (root / "config").mkdir()
            (root / "config" / "index_map.yaml").write_text("mappings: []\n", encoding="utf-8")
            _, rows, _, _ = update_index_data(
                metadata_path=root / "output" / "etf_metadata.csv",
                config_path=root / "config" / "index_map.yaml",
                output_dir=root / "output",
                cache_dir=root / "data" / "index_cache",
                ak_module=Ak(),
            )
            self.assertFalse(rows[0]["fetch_success"])
            self.assertFalse(rows[0]["schema_valid"])
            self.assertTrue(rows[0]["requires_manual_review"])
            self.assertIn("csindex unsupported", rows[0]["failure_reason"])

    def test_schema_invalid_samples_do_not_write_formal_cache(self) -> None:
        def config_for(symbol: str, code: str) -> dict[str, object]:
            return {
                "symbol": symbol,
                "tracking_index_name": f"index-{code}",
                "tracking_index_code": code,
                "mapping_method": "config_manual",
                "confidence": 0.95,
                "requires_manual_review": False,
            }

        class Ak:
            def stock_zh_index_hist_csindex(self, **_kwargs: object) -> pd.DataFrame:
                frame = _history_frame_english()
                frame["amount"] = pd.NA
                return frame

            def index_zh_a_hist(self, **_kwargs: object) -> pd.DataFrame:
                raise AssertionError("invalid CSIndex data must not be repaired from fallback")

            def stock_zh_index_daily_em(self, **_kwargs: object) -> pd.DataFrame:
                raise AssertionError("invalid CSIndex data must not be repaired from fallback")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            pd.DataFrame(
                {
                    "symbol": ["588000", "512480"],
                    "name": ["ETF A", "ETF B"],
                    "category": ["ETF", "ETF"],
                    "sub_category": ["ETF", "ETF"],
                    "tracking_index_name": ["unable_to_confirm", "unable_to_confirm"],
                    "tracking_index_code": ["unable_to_confirm", "unable_to_confirm"],
                    "metadata_source": ["unit", "unit"],
                }
            ).to_csv(root / "output" / "etf_metadata.csv", index=False, encoding="utf-8-sig")
            (root / "config").mkdir()
            (root / "config" / "index_map.yaml").write_text(
                yaml.safe_dump({"mappings": [config_for("588000", "000688"), config_for("512480", "931865")]}, allow_unicode=True),
                encoding="utf-8",
            )
            _, rows, _, _ = update_index_data(
                metadata_path=root / "output" / "etf_metadata.csv",
                config_path=root / "config" / "index_map.yaml",
                output_dir=root / "output",
                cache_dir=root / "data" / "index_cache",
                ak_module=Ak(),
            )
            self.assertEqual({row["tracking_index_code"] for row in rows}, {"000688", "931865"})
            self.assertFalse((root / "data" / "index_cache" / "000688.csv").exists())
            self.assertFalse((root / "data" / "index_cache" / "931865.csv").exists())
            self.assertTrue(all(row["fetch_success"] for row in rows))
            self.assertTrue(all(not row["schema_valid"] for row in rows))
            self.assertTrue(all(not row["usable_as_benchmark"] for row in rows))

    def test_update_index_data_does_not_modify_etf_price_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            _metadata_frame().iloc[[0]].to_csv(root / "output" / "etf_metadata.csv", index=False, encoding="utf-8-sig")
            (root / "config").mkdir()
            (root / "config" / "index_map.yaml").write_text("mappings: []\n", encoding="utf-8")
            cache_dir = root / "data" / "cache"
            cache_dir.mkdir(parents=True)
            etf_cache = cache_dir / "510300.csv"
            original = "date,open,high,low,close,volume,amount,symbol,name,source\n2026-05-08,1,1,1,1,1,1,510300,ETF,local_cache\n"
            etf_cache.write_text(original, encoding="utf-8")
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                def fetcher(_code: str, _start: str, _end: str | None) -> pd.DataFrame:
                    frame = _history_frame()
                    frame.attrs["source"] = "unit.fetcher"
                    return frame

                index_map, coverage_rows, map_path, coverage_path = update_index_data(fetcher=fetcher)
                self.assertTrue(map_path.exists())
                self.assertTrue(coverage_path.exists())
                self.assertEqual(etf_cache.read_text(encoding="utf-8"), original)
                self.assertTrue((root / "data" / "index_cache" / "000300.csv").exists())
                self.assertEqual(int(index_map["usable_as_benchmark"].sum()), 1)
                self.assertEqual(len(coverage_rows), 1)
                self.assertTrue(coverage_rows[0]["schema_valid"])
            finally:
                os.chdir(old_cwd)

    def test_summary_ok_from_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index_map = build_index_map(_metadata_frame().iloc[[0]], config_path=Path("missing.yaml"))
            map_path = write_index_map(index_map, Path(tmp) / "index_map.csv")
            coverage_path = write_index_data_coverage([], Path(tmp) / "index_data_coverage.csv")
            summary = summarize_index_data(index_map_path=map_path, coverage_path=coverage_path)
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["total_index_mappings"], 1)


if __name__ == "__main__":
    unittest.main()
