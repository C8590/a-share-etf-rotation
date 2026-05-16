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
from data.schema import validate_output_file_schema
from strategy.factors import (
    FactorDefinition,
    build_factor_score_audit,
    build_factor_score_audit_from_files,
    compute_multi_factor_score,
    summarize_factor_score,
    write_factor_score_audit,
    write_factor_score_reports,
)


def _factor(
    name: str,
    field: str | None = None,
    *,
    enabled: bool = True,
    source: str = "strategy_signal",
    status_field: str = "",
    direction: str = "higher_better",
) -> FactorDefinition:
    return FactorDefinition(
        name=name,
        enabled=enabled,
        weight=1.0,
        direction=direction,
        required=False,
        missing_policy="skip",
        source=source,
        field=field or name,
        min_coverage_required=0.0,
        notes="unit",
        status_field=status_field,
    )


def _audit_by_item(audit: pd.DataFrame, item: str) -> pd.Series:
    rows = audit[audit["audit_item"].eq(item)]
    if rows.empty:
        raise AssertionError(f"missing audit item: {item}")
    return rows.iloc[0]


class FactorScoreAuditTest(unittest.TestCase):
    def _sample_reports(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        frames = {
            "strategy_signal": pd.DataFrame(
                {
                    "symbol": ["510300", "159915"],
                    "name": ["A", "B"],
                    "momentum": [0.2, ""],
                    "data_completeness": [1.0, ""],
                }
            ),
            "etf_metrics": pd.DataFrame(
                {
                    "symbol": ["510300", "159915"],
                    "name": ["A", "B"],
                    "tracking_error": ["", ""],
                    "tracking_error_status": ["no_index_cache", "missing_benchmark"],
                    "discount_premium": ["", ""],
                    "discount_premium_status": ["source_unavailable", "source_unavailable"],
                }
            ),
            "etf_metadata": pd.DataFrame({"symbol": ["510300", "159915"], "name": ["A", "B"], "fund_size": ["", ""]}),
        }
        definitions = [
            _factor("momentum"),
            _factor("data_completeness"),
            _factor("tracking_error", source="etf_metrics", status_field="tracking_error_status", direction="lower_better"),
            _factor("discount_premium", source="etf_metrics", status_field="discount_premium_status", direction="lower_better"),
            _factor("fund_size", source="etf_metadata", enabled=False),
        ]
        return compute_multi_factor_score(frames, definitions, max_count=None)

    def test_audit_counts_used_skipped_source_unavailable_and_disabled(self) -> None:
        report, detail = self._sample_reports()
        audit = build_factor_score_audit(report, detail)

        self.assertEqual(int(_audit_by_item(audit, "score_computable_count")["count"]), 1)
        self.assertGreaterEqual(int(_audit_by_item(audit, "optional_factor_skipped_count")["count"]), 1)
        self.assertEqual(int(_audit_by_item(audit, "source_unavailable_factor_count")["count"]), 2)
        self.assertEqual(int(_audit_by_item(audit, "disabled_factor_count")["count"]), 1)

        factor_rows = audit[audit["audit_item"].eq("factor_coverage_by_name")]
        self.assertIn("momentum is used for 1 of 2 symbols.", factor_rows["finding"].tolist())
        self.assertTrue(any("tracking_error" in finding for finding in factor_rows["finding"].astype(str)))

    def test_audit_summary_recognizes_computable_ratio_and_source_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, detail = self._sample_reports()
            report_path, detail_path = write_factor_score_reports(
                report,
                detail,
                report_path=root / "factor_score_report.csv",
                detail_path=root / "factor_score_detail.csv",
            )
            audit = build_factor_score_audit(report, detail)
            audit_path = write_factor_score_audit(audit, audit_path=root / "factor_score_audit.csv")
            summary = summarize_factor_score(report_path=report_path, detail_path=detail_path, audit_path=audit_path)

            self.assertEqual(summary["computable_ratio"], 0.5)
            self.assertEqual(summary["audit_status"], "blocked_for_strategy_use")
            self.assertTrue(any("source_unavailable" in item["finding"] for item in summary["high_severity_findings"]))

    def test_factor_score_audit_csv_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, detail = self._sample_reports()
            audit = build_factor_score_audit(report, detail)
            audit_path = write_factor_score_audit(audit, audit_path=root / "factor_score_audit.csv")
            validate_output_file_schema(audit_path, "factor_score_audit")
            frame = pd.read_csv(audit_path, dtype=str, encoding="utf-8-sig").fillna("")
            for column in ["audit_item", "status", "severity", "count", "ratio", "finding", "suggested_action"]:
                self.assertIn(column, frame.columns)

    def test_qa_report_factor_score_audit_summary_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            report, detail = self._sample_reports()
            report_path, detail_path = write_factor_score_reports(
                report,
                detail,
                report_path=root / "output" / "factor_score_report.csv",
                detail_path=root / "output" / "factor_score_detail.csv",
            )
            audit = build_factor_score_audit(report, detail)
            write_factor_score_audit(audit, audit_path=root / "output" / "factor_score_audit.csv")

            old_cwd = Path.cwd()
            os.chdir(root)
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

                qa = json.loads(Path("output/qa_report.json").read_text(encoding="utf-8"))
                factor_score = qa["strategy_layer"]["factor_score"]
                self.assertEqual(factor_score["factor_score_audit_report"], "output\\factor_score_audit.csv")
                self.assertEqual(factor_score["audit_status"], "blocked_for_strategy_use")
                self.assertEqual(factor_score["computable_ratio"], 0.5)
                self.assertTrue(factor_score["top_blocking_reasons"])
                validate_output_file_schema(Path("output/qa_report.json"), "qa_report")
            finally:
                os.chdir(old_cwd)

    def test_build_audit_from_files_and_compute_command_do_not_modify_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            (root / "data" / "cache").mkdir(parents=True)
            (root / "data" / "index_cache").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "output" / "compare_signal.csv").write_text("symbol,name,momentum\n510300,A,0.2\n", encoding="utf-8")
            (root / "output" / "performance.json").write_text('{"ok": true}', encoding="utf-8")
            (root / "data" / "cache" / "510300.csv").write_text("cache", encoding="utf-8")
            (root / "data" / "index_cache" / "000300.csv").write_text("index", encoding="utf-8")
            (root / "config" / "factor_score.yaml").write_text(
                yaml.safe_dump(
                    {
                        "factors": [
                            {
                                "name": "momentum",
                                "field": "momentum",
                                "enabled": True,
                                "weight": 1.0,
                                "direction": "higher_better",
                                "required": False,
                                "missing_policy": "skip",
                                "source": "strategy_signal",
                                "min_coverage_required": 0.0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            snapshots = {
                path: (root / path).read_text(encoding="utf-8")
                for path in [
                    "output/compare_signal.csv",
                    "output/performance.json",
                    "data/cache/510300.csv",
                    "data/index_cache/000300.csv",
                ]
            }
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                main.command_compute_factor_score(config_path="config/factor_score.yaml", max_count=1)
                audit = build_factor_score_audit_from_files(
                    report_path="output/factor_score_report.csv",
                    detail_path="output/factor_score_detail.csv",
                )
                self.assertFalse(audit.empty)
            finally:
                os.chdir(old_cwd)

            for path, content in snapshots.items():
                self.assertEqual((root / path).read_text(encoding="utf-8"), content)
            self.assertTrue((root / "output" / "factor_score_audit.csv").exists())


if __name__ == "__main__":
    unittest.main()
