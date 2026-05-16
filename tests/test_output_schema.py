from __future__ import annotations

import json
import unittest
from pathlib import Path

import pandas as pd

from data.schema import (
    ADJUST_ALLOWED_VALUES,
    CACHE_METADATA_REQUIRED_FIELDS,
    INDEX_DATA_COVERAGE_REQUIRED_COLUMNS,
    OUTPUT_FILE_SCHEMAS,
    PRICE_CACHE_REQUIRED_COLUMNS,
    TRADING_CALENDAR_REQUIRED_COLUMNS,
    UNIVERSE_CSV_REQUIRED_COLUMNS,
    UNIVERSE_YAML_REQUIRED_KEYS,
    SchemaValidationError,
    validate_bool_like,
    validate_cache_metadata,
    validate_output_file_schema,
    validate_parseable_dates,
    validate_price_cache_frame,
    validate_required_columns,
)
from data.storage import build_cache_metadata


ROOT = Path(__file__).resolve().parents[1]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")


class OutputSchemaTest(unittest.TestCase):
    def test_price_cache_csv_schema(self) -> None:
        cache_files = [
            path
            for path in sorted((ROOT / "data" / "cache").glob("*.csv"))
            if path.stem.isdigit() and len(path.stem) == 6
        ]
        if not cache_files:
            self.skipTest("no local price cache files")
        for path in cache_files:
            with self.subTest(path=path.name):
                frame = pd.read_csv(path, dtype={"symbol": str}, encoding="utf-8-sig")
                validate_price_cache_frame(frame, f"data/cache/{path.name}")
                self.assertTrue(set(PRICE_CACHE_REQUIRED_COLUMNS).issubset(frame.columns))

    def test_cache_metadata_schema_with_fixture_and_current_files(self) -> None:
        fixture = pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "open": [10.0, 10.1],
                "high": [10.2, 10.3],
                "low": [9.9, 10.0],
                "close": [10.1, 10.2],
                "volume": [1000, 1100],
                "amount": [10100, 11220],
                "symbol": ["510300", "510300"],
                "name": ["ETF A", "ETF A"],
                "source": ["akshare.fund_etf_hist_em.qfq", "akshare.fund_etf_hist_em.qfq"],
            }
        )
        metadata = build_cache_metadata("510300", fixture, name="ETF A", source="akshare.fund_etf_hist_em.qfq")
        validate_cache_metadata(metadata, "fixture cache metadata")
        self.assertEqual(set(CACHE_METADATA_REQUIRED_FIELDS) - set(metadata), set())
        self.assertIn(metadata["adjust"], ADJUST_ALLOWED_VALUES)
        self.assertIsInstance(metadata["fallback_used"], bool)
        self.assertIsInstance(metadata["row_count"], int)

        metadata_files = sorted((ROOT / "data" / "cache_meta").glob("*.json"))
        for path in metadata_files:
            with self.subTest(path=path.name):
                current = json.loads(path.read_text(encoding="utf-8"))
                validate_cache_metadata(current, f"data/cache_meta/{path.name}")

    def test_trading_calendar_schema(self) -> None:
        path = ROOT / "data" / "calendar" / "a_share_trading_calendar.csv"
        if not path.exists():
            self.skipTest("trading calendar snapshot does not exist")
        frame = _read_csv(path)
        validate_required_columns(frame, TRADING_CALENDAR_REQUIRED_COLUMNS, "a_share_trading_calendar")
        validate_parseable_dates(frame["date"], "a_share_trading_calendar.date")
        validate_bool_like(frame["is_open"], "a_share_trading_calendar.is_open")
        open_days = frame[frame["is_open"].astype(str).str.lower().isin(["true", "1", "yes", "open"])]
        latest_open_day = pd.to_datetime(open_days["date"], errors="coerce").max()
        self.assertFalse(pd.isna(latest_open_day))

    def test_universe_schema(self) -> None:
        csv_path = ROOT / "data" / "universe" / "etf_universe.csv"
        if csv_path.exists():
            frame = _read_csv(csv_path)
            validate_required_columns(frame, UNIVERSE_CSV_REQUIRED_COLUMNS, "data/universe/etf_universe.csv")
            validate_parseable_dates(frame["fetched_at"], "etf_universe.fetched_at", allow_blank=True)

        yaml_path = ROOT / "config" / "etf_universe.yaml"
        if not yaml_path.exists():
            self.skipTest("config/etf_universe.yaml does not exist")
        import yaml

        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        missing = [key for key in UNIVERSE_YAML_REQUIRED_KEYS if key not in raw]
        self.assertEqual(missing, [])
        self.assertIsInstance(raw.get("etfs"), list)
        self.assertTrue(raw["etfs"])
        for item in raw["etfs"]:
            self.assertIn("symbol", item)
            self.assertIn("name", item)

    def test_data_failure_summary_schema(self) -> None:
        path = ROOT / "output" / "data_failure_summary.csv"
        if not path.exists():
            self.skipTest("data_failure_summary.csv does not exist")
        validate_output_file_schema(path, "data_failure_summary")
        frame = _read_csv(path)
        self.assertIn("failure_type", frame.columns)
        self.assertIn("severity", frame.columns)
        self.assertIn("suggested_action", frame.columns)
        self.assertFalse(frame["failure_type"].empty)

    def test_adjustment_audit_schema(self) -> None:
        path = ROOT / "output" / "adjustment_audit.csv"
        if not path.exists():
            self.skipTest("adjustment_audit.csv does not exist")
        validate_output_file_schema(path, "adjustment_audit")
        frame = _read_csv(path)
        for column in ["audit_status", "adjust", "possible_adjustment_issue"]:
            self.assertIn(column, frame.columns)

    def test_cache_metadata_audit_schema(self) -> None:
        path = ROOT / "output" / "cache_metadata_audit.csv"
        if not path.exists():
            self.skipTest("cache_metadata_audit.csv does not exist")
        validate_output_file_schema(path, "cache_metadata_audit")
        frame = _read_csv(path)
        for column in ["metadata_exists", "status"]:
            self.assertIn(column, frame.columns)
        self.assertTrue(
            frame["status"].isin(["warning_legacy_cache_without_metadata"]).any()
            or frame["metadata_exists"].astype(str).str.lower().isin(["true", "1", "yes"]).any()
        )

    def test_trading_calendar_audit_schema(self) -> None:
        path = ROOT / "output" / "trading_calendar_audit.csv"
        if not path.exists():
            self.skipTest("trading_calendar_audit.csv does not exist")
        validate_output_file_schema(path, "trading_calendar_audit")
        frame = _read_csv(path)
        for column in ["status", "latest_open_day", "used_fallback"]:
            self.assertIn(column, frame.columns)

    def test_cache_refresh_plan_schema_if_present(self) -> None:
        path = ROOT / "output" / "cache_refresh_plan.csv"
        if not path.exists():
            self.skipTest("cache_refresh_plan.csv does not exist")
        validate_output_file_schema(path, "cache_refresh_plan")
        frame = _read_csv(path)
        for column in ["refresh_reason", "refresh_priority", "requires_backup", "safe_to_auto_refresh"]:
            self.assertIn(column, frame.columns)

    def test_pilot_refresh_report_schema_if_present(self) -> None:
        path = ROOT / "output" / "pilot_refresh_report.csv"
        if not path.exists():
            self.skipTest("pilot_refresh_report.csv does not exist")
        validate_output_file_schema(path, "pilot_refresh_report")
        frame = _read_csv(path)
        for column in ["refresh_status", "backup_created", "metadata_written", "row_count_delta"]:
            self.assertIn(column, frame.columns)

    def test_missing_cache_repair_report_schema_if_present(self) -> None:
        path = ROOT / "output" / "missing_cache_repair_report.csv"
        if not path.exists():
            self.skipTest("missing_cache_repair_report.csv does not exist")
        validate_output_file_schema(path, "missing_cache_repair_report")
        frame = _read_csv(path)
        for column in ["repair_status", "metadata_written", "still_missing_cache", "quality_after_repair"]:
            self.assertIn(column, frame.columns)

    def test_qa_status_schemas_if_present(self) -> None:
        breakdown = ROOT / "output" / "qa_status_breakdown.csv"
        summary = ROOT / "output" / "qa_status_summary.csv"
        if not breakdown.exists() or not summary.exists():
            self.skipTest("qa_status reports do not exist")
        validate_output_file_schema(breakdown, "qa_status_breakdown")
        validate_output_file_schema(summary, "qa_status_summary")
        frame = _read_csv(breakdown)
        for column in ["qa_item", "actionability", "root_cause", "blocks_007b", "blocks_008b"]:
            self.assertIn(column, frame.columns)

    def test_candidate_unblock_schemas_if_present(self) -> None:
        plan = ROOT / "output" / "candidate_unblock_plan.csv"
        summary = ROOT / "output" / "candidate_unblock_summary.csv"
        if not plan.exists() or not summary.exists():
            self.skipTest("candidate unblock reports do not exist")
        validate_output_file_schema(plan, "candidate_unblock_plan")
        validate_output_file_schema(summary, "candidate_unblock_summary")
        frame = _read_csv(plan)
        for column in ["symbol", "unblock_path", "unblock_status", "still_blocked_after_primary_fix", "next_action"]:
            self.assertIn(column, frame.columns)

    def test_factor_008b_readiness_schemas_if_present(self) -> None:
        report = ROOT / "output" / "factor_008b_readiness.csv"
        summary = ROOT / "output" / "factor_008b_readiness_summary.csv"
        if not report.exists() or not summary.exists():
            self.skipTest("factor 008B readiness reports do not exist")
        validate_output_file_schema(report, "factor_008b_readiness")
        validate_output_file_schema(summary, "factor_008b_readiness_summary")

    def test_index_007b_readiness_schemas_if_present(self) -> None:
        report = ROOT / "output" / "index_007b_readiness.csv"
        unlock = ROOT / "output" / "index_007b_unlock_plan.csv"
        summary = ROOT / "output" / "index_007b_readiness_summary.csv"
        if not report.exists() or not unlock.exists() or not summary.exists():
            self.skipTest("index 007B readiness reports do not exist")
        validate_output_file_schema(report, "index_007b_readiness")
        validate_output_file_schema(unlock, "index_007b_unlock_plan")
        validate_output_file_schema(summary, "index_007b_readiness_summary")
        frame = _read_csv(report)
        for column in ["readiness_item", "blocking", "blocker_type", "remediation_action", "prerequisite_task"]:
            self.assertIn(column, frame.columns)

    def test_source_preference_audit_schema_if_present(self) -> None:
        path = ROOT / "output" / "source_preference_audit.csv"
        if not path.exists():
            self.skipTest("source_preference_audit.csv does not exist")
        validate_output_file_schema(path, "source_preference_audit")
        frame = _read_csv(path)
        for column in ["source_candidate", "preferred_candidate", "safe_to_promote", "requires_manual_review"]:
            self.assertIn(column, frame.columns)

    def test_source_diagnostics_report_schema_if_present(self) -> None:
        path = ROOT / "output" / "source_diagnostics_report.csv"
        if not path.exists():
            self.skipTest("source_diagnostics_report.csv does not exist")
        validate_output_file_schema(path, "source_diagnostics_report")
        frame = _read_csv(path)
        for column in ["check_type", "error_type", "diagnosis", "suggested_action"]:
            self.assertIn(column, frame.columns)

    def test_index_source_diagnostics_schema_if_present(self) -> None:
        path = ROOT / "output" / "index_source_diagnostics.csv"
        if not path.exists():
            self.skipTest("index_source_diagnostics.csv does not exist")
        validate_output_file_schema(path, "index_source_diagnostics")
        frame = _read_csv(path)
        for column in ["api_name", "source_family", "failure_type", "usable_as_index_source"]:
            self.assertIn(column, frame.columns)

    def test_index_data_coverage_schema_contract_has_source_audit_fields(self) -> None:
        required = set(INDEX_DATA_COVERAGE_REQUIRED_COLUMNS)
        for column in ["api_name", "source_family", "schema_valid", "missing_required_columns", "requires_manual_review", "notes"]:
            self.assertIn(column, required)
        self.assertTrue(set(OUTPUT_FILE_SCHEMAS["index_data_coverage"]["required"]).issuperset(required))

    def test_etf_metadata_schema_if_present(self) -> None:
        path = ROOT / "output" / "etf_metadata.csv"
        if not path.exists():
            self.skipTest("etf_metadata.csv does not exist")
        validate_output_file_schema(path, "etf_metadata")
        frame = _read_csv(path)
        for column in ["fund_company", "inferred_category", "metadata_source", "field_completeness"]:
            self.assertIn(column, frame.columns)

    def test_etf_metadata_coverage_schema_if_present(self) -> None:
        path = ROOT / "output" / "etf_metadata_coverage.csv"
        if not path.exists():
            self.skipTest("etf_metadata_coverage.csv does not exist")
        validate_output_file_schema(path, "etf_metadata_coverage")
        frame = _read_csv(path)
        for column in ["field_name", "coverage_ratio", "importance"]:
            self.assertIn(column, frame.columns)

    def test_qa_report_schema(self) -> None:
        path = ROOT / "output" / "qa_report.json"
        if not path.exists():
            self.skipTest("qa_report.json does not exist")
        validate_output_file_schema(path, "qa_report")
        report = json.loads(path.read_text(encoding="utf-8"))
        data_layer = report["data_layer"]
        for field in ["failure_summary", "adjustment_audit", "cache_metadata_audit", "trading_calendar"]:
            self.assertIn(field, data_layer)

    def test_factor_score_gate_schema_if_present(self) -> None:
        path = ROOT / "output" / "factor_score_gate.csv"
        if not path.exists():
            self.skipTest("factor_score_gate.csv does not exist")
        validate_output_file_schema(path, "factor_score_gate")

    def test_compare_signal_schema_if_present(self) -> None:
        path = ROOT / "output" / "compare_signal.csv"
        if not path.exists():
            self.skipTest("compare_signal.csv does not exist")
        validate_output_file_schema(path, "compare_signal")
        frame = _read_csv(path)
        validate_parseable_dates(frame["latest_date"], "compare_signal.latest_date", allow_blank=True)

    def test_strategy_compare_signal_schema_if_present(self) -> None:
        path = ROOT / "output" / "strategy_compare_signal.csv"
        if not path.exists():
            self.skipTest("strategy_compare_signal.csv does not exist")
        validate_output_file_schema(path, "strategy_compare_signal")

    def test_performance_json_schema_if_present(self) -> None:
        path = ROOT / "output" / "performance.json"
        if not path.exists():
            self.skipTest("performance.json does not exist")
        report = json.loads(path.read_text(encoding="utf-8"))
        required = [
            "total_return",
            "annual_return",
            "max_drawdown",
            "trade_count",
            "start_date",
            "end_date",
            "final_equity",
            "effective_etf_count",
            "min_effective_etf_count",
        ]
        missing = [field for field in required if field not in report]
        self.assertEqual(missing, [])

    def test_schema_validation_rejects_missing_columns(self) -> None:
        with self.assertRaises(SchemaValidationError):
            validate_required_columns(pd.DataFrame({"symbol": ["510300"]}), OUTPUT_FILE_SCHEMAS["compare_signal"]["required"], "compare_signal")


if __name__ == "__main__":
    unittest.main()
