from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data.candidate_gate import (
    build_candidate_gate_report,
    evaluate_candidate_eligibility,
    merge_candidate_gate_into_qa_report,
    summarize_candidate_gate,
    write_candidate_gate_report,
)
from data.schema import validate_output_file_schema


def _diagnosis(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": "159007",
        "name": "ETF A",
        "category": "industry",
        "sub_category": "test",
        "history_status": "short_history",
        "cache_status": "fresh",
        "liquidity_status": "ok",
        "price_quality_status": "ok",
        "strategy_eligibility": "blocked_short_history",
        "remediation_priority": "P1_short_history_observe",
        "requires_manual_review": False,
        "exclude_from_candidate_pool": True,
        "recommended_action": "observe",
        "notes": "short history is not a low score",
    }
    row.update(overrides)
    return row


def _factor(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": "159007",
        "name": "ETF A",
        "score_status": "ok",
        "used_factor_count": 3,
    }
    row.update(overrides)
    return row


class CandidateGateTest(unittest.TestCase):
    def test_exclude_from_candidate_pool_blocks(self) -> None:
        row = evaluate_candidate_eligibility(
            symbol="159007",
            diagnosis_row=_diagnosis(strategy_eligibility="eligible", history_status="sufficient_history"),
            factor_score_row=_factor(),
            factor_gate_status="passed",
        )
        self.assertTrue(row["blocked"])
        self.assertIn("exclude_from_candidate_pool", row["block_reason"])

    def test_short_history_is_blocked_short_history(self) -> None:
        row = evaluate_candidate_eligibility(
            symbol="159007",
            diagnosis_row=_diagnosis(),
            factor_score_row=_factor(),
            factor_gate_status="passed",
        )
        self.assertEqual(row["candidate_status"], "blocked_short_history")
        self.assertTrue(row["blocked"])

    def test_manual_review_is_blocked_manual_review(self) -> None:
        row = evaluate_candidate_eligibility(
            symbol="159231",
            diagnosis_row=_diagnosis(
                symbol="159231",
                requires_manual_review=True,
                strategy_eligibility="blocked_manual_review",
                history_status="short_history",
            ),
            factor_score_row=_factor(symbol="159231"),
            factor_gate_status="passed",
        )
        self.assertEqual(row["candidate_status"], "blocked_manual_review")
        self.assertIn("manual_review_required", row["block_reason"])

    def test_no_used_factors_is_not_low_score(self) -> None:
        row = evaluate_candidate_eligibility(
            symbol="159001",
            factor_score_row=_factor(symbol="159001", score_status="no_used_factors", used_factor_count=0),
            factor_gate_status="passed",
        )
        self.assertEqual(row["candidate_status"], "blocked_no_used_factors")
        self.assertIn("not a low score", row["notes"])

    def test_factor_gate_blocked_prevents_scoring_candidate(self) -> None:
        row = evaluate_candidate_eligibility(
            symbol="510300",
            factor_score_row=_factor(symbol="510300", score_status="ok"),
            factor_gate_status="blocked_for_strategy_use",
        )
        self.assertEqual(row["candidate_status"], "blocked_factor_gate")
        self.assertIn("factor_score_gate_blocked_for_strategy_use", row["block_reason"])

    def test_low_liquidity_enters_observation_reason(self) -> None:
        row = evaluate_candidate_eligibility(
            symbol="159009",
            diagnosis_row=_diagnosis(liquidity_status="low_liquidity"),
            factor_score_row=_factor(),
            factor_gate_status="passed",
        )
        self.assertIn("low_liquidity", row["observation_reason"])

    def test_candidate_gate_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                evaluate_candidate_eligibility(
                    symbol="159007",
                    diagnosis_row=_diagnosis(),
                    factor_score_row=_factor(),
                    factor_gate_status="passed",
                )
            ]
            report, summary = write_candidate_gate_report(
                rows,
                report_path=root / "output" / "candidate_gate.csv",
                summary_path=root / "output" / "candidate_gate_summary.csv",
            )
            validate_output_file_schema(report, "candidate_gate")
            validate_output_file_schema(summary, "candidate_gate_summary")
            parsed = summarize_candidate_gate(report_path=report)
            self.assertEqual(parsed["blocked_short_history_count"], 1)

    def test_build_candidate_gate_report_combines_diagnosis_and_factor_inputs(self) -> None:
        diagnosis = pd.DataFrame([_diagnosis(symbol="159007")])
        factors = pd.DataFrame(
            [
                _factor(symbol="159007", score_status="ok"),
                _factor(symbol="159001", score_status="no_used_factors", used_factor_count=0),
                _factor(symbol="510300", score_status="ok"),
            ]
        )
        gate = pd.DataFrame(
            [
                {"gate_item": "min_computable_ratio", "status": "blocked", "blocking": True},
            ]
        )
        rows = build_candidate_gate_report(
            diagnosis=diagnosis,
            factor_score=factors,
            factor_gate=gate,
            etf_metrics=pd.DataFrame(),
        )
        by_symbol = {row["symbol"]: row for row in rows}
        self.assertEqual(by_symbol["159007"]["candidate_status"], "blocked_short_history")
        self.assertEqual(by_symbol["159001"]["candidate_status"], "blocked_no_used_factors")
        self.assertEqual(by_symbol["510300"]["candidate_status"], "blocked_factor_gate")
        self.assertEqual(summarize_candidate_gate(rows)["blocked_factor_gate_count"], 3)

    def test_qa_report_candidate_gate_summary_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            rows = [
                evaluate_candidate_eligibility(
                    symbol="159007",
                    diagnosis_row=_diagnosis(),
                    factor_score_row=_factor(),
                    factor_gate_status="passed",
                )
            ]
            write_candidate_gate_report(rows, report_path=output / "candidate_gate.csv", summary_path=output / "candidate_gate_summary.csv")
            qa = {
                "schema_version": "1.0",
                "data_schema_version": "1.0",
                "data_layer": {
                    "passed": True,
                    "effective_etf_count": 1,
                    "latest_date": "2026-05-15",
                    "reasons": [],
                    "coverage_report": "output/data_coverage_report.csv",
                    "quality_report": "output/data_quality_report.csv",
                    "trading_calendar_report": "output/trading_calendar_audit.csv",
                    "trading_calendar": {
                        "calendar_file": "x",
                        "status": "ok",
                        "source": "unit",
                        "start_date": "2026-01-01",
                        "end_date": "2026-12-31",
                        "latest_open_day": "2026-05-15",
                        "coverage_gap_days": 0,
                        "used_fallback": False,
                        "reason": "unit",
                    },
                    "failure_summary_report": "output/data_failure_summary.csv",
                    "failure_summary": {"total_failed": 0, "failure_type_counts": {}, "severe_failed": 0, "warning_failed": 0, "top_examples": []},
                    "cache_metadata_audit_report": "output/cache_metadata_audit.csv",
                    "cache_metadata_audit": {
                        "total_cache_files": 0,
                        "metadata_exists_count": 0,
                        "legacy_cache_without_metadata_count": 0,
                        "unknown_adjustment_count": 0,
                        "metadata_cache_mismatch_count": 0,
                        "top_examples": [],
                    },
                    "adjustment_audit_report": "output/adjustment_audit.csv",
                    "adjustment_audit": {
                        "total_checked": 0,
                        "unknown_adjustment_count": 0,
                        "fallback_used_count": 0,
                        "abnormal_return_symbols": [],
                        "possible_adjustment_issue_count": 0,
                        "top_examples": [],
                    },
                },
                "strategy_layer": {"passed": True},
                "output_layer": {"passed": True},
                "allow_small_observation": True,
                "blocking_reasons": [],
                "recommended_for_observation": [],
                "not_recommended": [],
                "defensive_only": [],
                "risk_note": "unit",
            }
            qa_path = output / "qa_report.json"
            qa_path.write_text(json.dumps(qa), encoding="utf-8")
            self.assertTrue(merge_candidate_gate_into_qa_report(qa_path, rows=rows))
            validate_output_file_schema(qa_path, "qa_report")
            merged = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["strategy_layer"]["candidate_gate"]["blocked_short_history_count"], 1)

    def test_build_candidate_gate_does_not_modify_caches_or_strategy_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            cache = root / "data" / "cache"
            index_cache = root / "data" / "index_cache"
            cache.mkdir(parents=True)
            index_cache.mkdir(parents=True)
            files = [
                cache / "159007.csv",
                index_cache / "000300.csv",
                output / "compare_signal.csv",
                output / "equity_curve.csv",
            ]
            for path in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"sentinel {path.name}", encoding="utf-8")
            before = {path: path.read_text(encoding="utf-8") for path in files}
            rows = build_candidate_gate_report(
                output_dir=output,
                diagnosis=pd.DataFrame([_diagnosis()]),
                factor_score=pd.DataFrame([_factor()]),
                factor_gate=pd.DataFrame([{"status": "passed", "blocking": False}]),
                etf_metrics=pd.DataFrame(),
            )
            write_candidate_gate_report(rows, report_path=output / "candidate_gate.csv", summary_path=output / "candidate_gate_summary.csv")
            after = {path: path.read_text(encoding="utf-8") for path in files}
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
