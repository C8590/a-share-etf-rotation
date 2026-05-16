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
    compute_multi_factor_score,
    evaluate_factor_score_gate,
    summarize_factor_score,
    summarize_factor_score_gate,
    write_factor_score_audit,
    write_factor_score_gate_report,
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


def _gate_row(gate: pd.DataFrame, item: str) -> pd.Series:
    rows = gate[gate["gate_item"].eq(item)]
    if rows.empty:
        raise AssertionError(f"missing gate item: {item}")
    return rows.iloc[0]


class FactorScoreGateTest(unittest.TestCase):
    def _blocked_fixture(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[FactorDefinition]]:
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
        }
        definitions = [
            _factor("momentum"),
            _factor("data_completeness"),
            _factor("tracking_error", source="etf_metrics", status_field="tracking_error_status", direction="lower_better"),
            _factor("discount_premium", source="etf_metrics", status_field="discount_premium_status", direction="lower_better"),
            _factor("fund_size", source="etf_metadata", enabled=False),
        ]
        report, detail = compute_multi_factor_score(frames, definitions, max_count=None)
        quality = pd.DataFrame(
            {
                "symbol": ["510300", "159915"],
                "rows": [20, 20],
                "status": ["failed", "failed"],
                "primary_failure_type": ["insufficient_rows", "insufficient_rows"],
            }
        )
        audit = build_factor_score_audit(report, detail, data_quality=quality)
        return report, detail, audit, definitions

    def test_computable_ratio_below_threshold_blocks(self) -> None:
        report, detail, audit, definitions = self._blocked_fixture()
        gate = evaluate_factor_score_gate(report, detail, audit, definitions)
        row = _gate_row(gate, "min_computable_ratio")
        self.assertEqual(row["status"], "blocked")
        self.assertFalse(bool(row["passed"]))
        self.assertTrue(bool(row["blocking"]))

    def test_unable_to_score_ratio_too_high_blocks(self) -> None:
        report, detail, audit, definitions = self._blocked_fixture()
        gate = evaluate_factor_score_gate(report, detail, audit, definitions)
        row = _gate_row(gate, "max_unable_to_score_ratio")
        self.assertEqual(row["status"], "blocked")
        self.assertIn("0.5000", row["actual_value"])

    def test_short_history_bias_full_coverage_blocks(self) -> None:
        report, detail, audit, definitions = self._blocked_fixture()
        gate = evaluate_factor_score_gate(report, detail, audit, definitions)
        row = _gate_row(gate, "no_short_history_bias")
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(row["severity"], "high")
        self.assertIn("1/1", row["actual_value"])

    def test_source_unavailable_core_factors_block(self) -> None:
        report, detail, audit, definitions = self._blocked_fixture()
        gate = evaluate_factor_score_gate(report, detail, audit, definitions)
        row = _gate_row(gate, "no_source_unavailable_core_factors")
        self.assertEqual(row["status"], "blocked")
        self.assertIn("tracking_error", row["actual_value"])
        self.assertIn("discount_premium", row["actual_value"])

    def test_gate_report_schema_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, detail, audit, definitions = self._blocked_fixture()
            report_path, detail_path = write_factor_score_reports(
                report,
                detail,
                report_path=root / "factor_score_report.csv",
                detail_path=root / "factor_score_detail.csv",
            )
            audit_path = write_factor_score_audit(audit, audit_path=root / "factor_score_audit.csv")
            gate = evaluate_factor_score_gate(report, detail, audit, definitions)
            gate_path = write_factor_score_gate_report(gate, gate_path=root / "factor_score_gate.csv")
            validate_output_file_schema(gate_path, "factor_score_gate")
            summary = summarize_factor_score_gate(gate_path=gate_path, report_path=report_path, detail_path=detail_path, audit_path=audit_path)

            self.assertEqual(summary["gate_status"], "blocked_for_strategy_use")
            self.assertGreater(summary["failed_gate_count"], 0)
            self.assertTrue(summary["blocking_findings"])

    def test_qa_report_factor_score_gate_summary_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            (root / "config").mkdir()
            report, detail, audit, definitions = self._blocked_fixture()
            report_path, detail_path = write_factor_score_reports(
                report,
                detail,
                report_path=root / "output" / "factor_score_report.csv",
                detail_path=root / "output" / "factor_score_detail.csv",
            )
            audit_path = write_factor_score_audit(audit, audit_path=root / "output" / "factor_score_audit.csv")
            gate = evaluate_factor_score_gate(report, detail, audit, definitions)
            write_factor_score_gate_report(gate, gate_path=root / "output" / "factor_score_gate.csv")
            (root / "config" / "factor_score.yaml").write_text(
                yaml.safe_dump(
                    {
                        "factors": [
                            {
                                "name": factor.name,
                                "field": factor.field,
                                "enabled": factor.enabled,
                                "weight": factor.weight,
                                "direction": factor.direction,
                                "required": factor.required,
                                "missing_policy": factor.missing_policy,
                                "source": factor.source,
                                "status_field": factor.status_field,
                                "min_coverage_required": factor.min_coverage_required,
                            }
                            for factor in definitions
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

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
                self.assertEqual(factor_score["factor_score_gate_report"], "output\\factor_score_gate.csv")
                self.assertEqual(factor_score["gate_status"], "blocked_for_strategy_use")
                self.assertGreater(factor_score["failed_gate_count"], 0)
                self.assertTrue(factor_score["blocking_findings"])
                validate_output_file_schema(Path("output/qa_report.json"), "qa_report")
            finally:
                os.chdir(old_cwd)

    def test_compute_factor_score_gate_does_not_modify_strategy_cache_or_backtest_outputs(self) -> None:
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
                summary = summarize_factor_score(
                    report_path="output/factor_score_report.csv",
                    detail_path="output/factor_score_detail.csv",
                    audit_path="output/factor_score_audit.csv",
                    gate_path="output/factor_score_gate.csv",
                    config_path="config/factor_score.yaml",
                )
                self.assertIn("gate_status", summary)
            finally:
                os.chdir(old_cwd)

            for path, content in snapshots.items():
                self.assertEqual((root / path).read_text(encoding="utf-8"), content)
            self.assertTrue((root / "output" / "factor_score_gate.csv").exists())


if __name__ == "__main__":
    unittest.main()
