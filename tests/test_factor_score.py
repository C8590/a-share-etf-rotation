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
    compute_multi_factor_score,
    load_factor_config,
    normalize_factor,
    summarize_factor_score,
    write_factor_score_reports,
)


def _factor(
    name: str,
    field: str | None = None,
    *,
    enabled: bool = True,
    required: bool = False,
    missing_policy: str = "skip",
    source: str = "strategy_signal",
    direction: str = "higher_better",
    status_field: str = "",
) -> FactorDefinition:
    return FactorDefinition(
        name=name,
        enabled=enabled,
        weight=1.0,
        direction=direction,
        required=required,
        missing_policy=missing_policy,
        source=source,
        field=field or name,
        min_coverage_required=0.0,
        notes="unit",
        status_field=status_field,
    )


class FactorScoreTest(unittest.TestCase):
    def test_higher_better_normalization(self) -> None:
        result = normalize_factor(pd.Series([10, 20, 30], index=["a", "b", "c"]), "higher_better")
        self.assertEqual(float(result.loc["a"]), 0.0)
        self.assertEqual(float(result.loc["b"]), 0.5)
        self.assertEqual(float(result.loc["c"]), 1.0)

    def test_lower_better_normalization(self) -> None:
        result = normalize_factor(pd.Series([10, 20, 30], index=["a", "b", "c"]), "lower_better")
        self.assertEqual(float(result.loc["a"]), 1.0)
        self.assertEqual(float(result.loc["b"]), 0.5)
        self.assertEqual(float(result.loc["c"]), 0.0)

    def test_optional_missing_is_skipped_not_zero(self) -> None:
        frames = {"strategy_signal": pd.DataFrame({"symbol": ["510300", "159915"], "name": ["A", "B"], "momentum": [0.2, ""]})}
        report, detail = compute_multi_factor_score(frames, [_factor("momentum")], max_count=None)
        missing = detail[detail["symbol"].eq("159915")].iloc[0]
        self.assertEqual(missing["factor_status"], "skipped_missing_optional")
        self.assertEqual(missing["weighted_score"], "")
        self.assertEqual(report[report["symbol"].eq("159915")]["score_status"].iloc[0], "no_used_factors")

    def test_required_missing_is_unable_to_score(self) -> None:
        frames = {"strategy_signal": pd.DataFrame({"symbol": ["510300"], "name": ["A"], "momentum": [""]})}
        report, detail = compute_multi_factor_score(frames, [_factor("momentum", required=True)], max_count=None)
        self.assertEqual(detail.iloc[0]["factor_status"], "missing_required")
        self.assertEqual(report.iloc[0]["score_status"], "missing_required_factor")

    def test_disabled_factor_does_not_participate(self) -> None:
        frames = {"strategy_signal": pd.DataFrame({"symbol": ["510300"], "name": ["A"], "momentum": [0.2]})}
        report, detail = compute_multi_factor_score(frames, [_factor("momentum", enabled=False)], max_count=None)
        self.assertEqual(detail.iloc[0]["factor_status"], "disabled")
        self.assertEqual(report.iloc[0]["used_factor_count"], 0)
        self.assertEqual(report.iloc[0]["score_status"], "no_used_factors")

    def test_source_unavailable_does_not_participate(self) -> None:
        frames = {"strategy_signal": pd.DataFrame({"symbol": ["510300"], "name": ["A"]})}
        report, detail = compute_multi_factor_score(frames, [_factor("not_there")], max_count=None)
        self.assertEqual(detail.iloc[0]["factor_status"], "source_unavailable")
        self.assertEqual(report.iloc[0]["score_status"], "no_used_factors")

    def test_tracking_error_missing_is_not_used(self) -> None:
        frames = {
            "etf_metrics": pd.DataFrame(
                {
                    "symbol": ["510300"],
                    "name": ["A"],
                    "tracking_error": [""],
                    "tracking_error_status": ["no_index_cache"],
                }
            )
        }
        report, detail = compute_multi_factor_score(
            frames,
            [_factor("tracking_error", source="etf_metrics", direction="lower_better", status_field="tracking_error_status")],
            max_count=None,
        )
        self.assertEqual(detail.iloc[0]["factor_status"], "source_unavailable")
        self.assertEqual(report.iloc[0]["used_factor_count"], 0)

    def test_discount_premium_missing_is_not_used(self) -> None:
        frames = {
            "etf_metrics": pd.DataFrame(
                {
                    "symbol": ["510300"],
                    "name": ["A"],
                    "discount_premium": [""],
                    "discount_premium_status": ["source_unavailable"],
                }
            )
        }
        _report, detail = compute_multi_factor_score(
            frames,
            [_factor("discount_premium", source="etf_metrics", direction="lower_better", status_field="discount_premium_status")],
            max_count=None,
        )
        self.assertEqual(detail.iloc[0]["factor_status"], "source_unavailable")
        self.assertEqual(detail.iloc[0]["weighted_score"], "")

    def test_factor_score_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = {"strategy_signal": pd.DataFrame({"symbol": ["510300"], "name": ["A"], "momentum": [0.2]})}
            report, detail = compute_multi_factor_score(frames, [_factor("momentum")], max_count=None)
            report_path, detail_path = write_factor_score_reports(
                report,
                detail,
                report_path=root / "factor_score_report.csv",
                detail_path=root / "factor_score_detail.csv",
            )
            validate_output_file_schema(report_path, "factor_score_report")
            validate_output_file_schema(detail_path, "factor_score_detail")
            summary = summarize_factor_score(report_path=report_path, detail_path=detail_path)
            self.assertEqual(summary["score_computable_count"], 1)

    def test_qa_report_factor_score_not_run_is_parseable(self) -> None:
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
                self.assertIn("factor_score", report["strategy_layer"])
                self.assertEqual(report["strategy_layer"]["factor_score"]["status"], "not_run")
                validate_output_file_schema(Path("output/qa_report.json"), "qa_report")
            finally:
                os.chdir(old_cwd)

    def test_compute_factor_score_does_not_modify_cache_or_strategy_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            (root / "data" / "cache").mkdir(parents=True)
            (root / "data" / "index_cache").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "output" / "compare_signal.csv").write_text("symbol,name,momentum_20\n510300,A,0.2\n", encoding="utf-8")
            (root / "output" / "performance.json").write_text('{"ok": true}', encoding="utf-8")
            (root / "data" / "cache" / "510300.csv").write_text("cache", encoding="utf-8")
            (root / "data" / "index_cache" / "000300.csv").write_text("index", encoding="utf-8")
            (root / "config" / "factor_score.yaml").write_text(
                yaml.safe_dump(
                    {
                        "factors": [
                            {
                                "name": "momentum_20d",
                                "field": "momentum_20",
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
            finally:
                os.chdir(old_cwd)
            for path, content in snapshots.items():
                self.assertEqual((root / path).read_text(encoding="utf-8"), content)

    def test_default_config_loads(self) -> None:
        definitions = load_factor_config(Path(__file__).resolve().parents[1] / "config" / "factor_score.yaml")
        self.assertTrue(any(item.name == "tracking_error" for item in definitions))


if __name__ == "__main__":
    unittest.main()
