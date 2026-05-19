from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
import unittest

import pandas as pd

from ui.governance_parser import (
    CANDIDATE_STATUS_OPTIONS,
    KEY_LABELS,
    OBSERVATION_STATUS_OPTIONS,
    QA_ACTIONABILITY_OPTIONS,
    QA_BLOCK_SCOPE_OPTIONS,
    ACTION_LABELS,
    FIELD_LABELS,
    STATUS_LABELS,
    format_action,
    format_display_value,
    format_status,
    get_007b_summary,
    get_008b_summary,
    get_candidate_gate_summary,
    get_governance_status,
    get_manual_review_summary,
    get_qa_status,
    localize_dataframe_values,
    load_csv_report,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _tree_hash(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    parts: list[str] = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            parts.append(f"{item.relative_to(path)}:{hashlib.sha256(item.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


class GovernanceDashboardParserTest(unittest.TestCase):
    def test_status_values_are_natural_chinese(self) -> None:
        self.assertEqual(format_status("governed_blocked"), "已治理，未放行")
        self.assertEqual(format_status("blocked"), "阻断")
        self.assertEqual(format_status("blocked_for_strategy_use"), "暂不可用于策略")
        self.assertEqual(format_status("ready_small_scope"), "小范围就绪")
        self.assertEqual(format_status("small_scope"), "小范围研究")
        self.assertEqual(format_status("wait_for_history"), "等待历史数据补足")
        self.assertEqual(format_status("refresh_needed"), "需要受控刷新")
        self.assertEqual(format_status("source_diagnosis"), "需要数据源诊断")
        self.assertEqual(format_status("provider_stale"), "数据源停更或滞后")
        self.assertEqual(format_status("source_lag_blocker"), "数据源滞后阻断")
        self.assertEqual(format_status("manual_review"), "人工复核")
        self.assertEqual(format_status("no_used_factors"), "无可用因子")
        self.assertEqual(format_status("missing_benchmark"), "缺少基准指数")
        self.assertEqual(format_status("no_index_cache"), "缺少指数缓存")
        self.assertEqual(format_status("computed_valid"), "指标可计算")
        self.assertEqual(format_status("buy_candidate"), "买入候选")
        self.assertEqual(format_status("watch_candidate"), "观察候选")
        self.assertEqual(format_status("avoid"), "暂不考虑")
        self.assertEqual(format_status("data_blocked"), "数据不足，不能判断")
        self.assertEqual(format_status("false"), "否")

    def test_forbidden_semantic_terms_are_not_exposed(self) -> None:
        text = "\n".join(
            [
                *KEY_LABELS.values(),
                *FIELD_LABELS.values(),
                *STATUS_LABELS.values(),
                *ACTION_LABELS.values(),
                *[label for label, _ in QA_ACTIONABILITY_OPTIONS],
                *[label for label, _ in CANDIDATE_STATUS_OPTIONS],
                *[label for label, _ in OBSERVATION_STATUS_OPTIONS],
            ]
        )
        for forbidden in ["爆发", "手册审核", "等待历史记录", "治理受阻"]:
            self.assertNotIn(forbidden, text)

    def test_recommended_action_maps_to_chinese(self) -> None:
        self.assertEqual(
            format_action("keep excluded and observe until sufficient history"),
            "继续排除，等待历史数据补足",
        )
        self.assertEqual(format_action("do not enter 008B"), "不进入 008B")
        self.assertIn("诊断指数数据源", format_action("diagnose-index-source -> update-index-data -> compute-etf-metrics"))

    def test_filter_options_do_not_expose_raw_values(self) -> None:
        labels = [label for options in [QA_ACTIONABILITY_OPTIONS, QA_BLOCK_SCOPE_OPTIONS, CANDIDATE_STATUS_OPTIONS, OBSERVATION_STATUS_OPTIONS] for label, _ in options]
        raw_fragments = ["wait_for_history", "governance_blocked", "blocked_short_history", "manual_review_required"]
        self.assertFalse(any(raw in label for raw in raw_fragments for label in labels))
        self.assertIn("等待历史数据补足", labels)
        self.assertIn("治理门禁阻断", labels)

    def test_dataframe_display_maps_status_and_actions(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "validation_status": "computed_valid",
                    "factor_gate_status": "blocked_for_strategy_use",
                    "block_reason": "no_used_factors",
                    "recommended_action": "keep excluded and observe until sufficient history",
                }
            ]
        )
        display = localize_dataframe_values(frame)
        self.assertEqual(display.iloc[0]["validation_status"], "指标可计算")
        self.assertEqual(display.iloc[0]["factor_gate_status"], "暂不可用于策略")
        self.assertEqual(display.iloc[0]["block_reason"], "无可用因子")
        self.assertEqual(display.iloc[0]["recommended_action"], "继续排除，等待历史数据补足")

    def test_missing_data_governance_status_returns_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = get_governance_status(Path(tmp))
            self.assertEqual(status["overall_project_status"], "governed_blocked")
            self.assertFalse(status["allowed_to_enter_007b"])
            self.assertTrue(status["warnings"])

    def test_missing_qa_status_breakdown_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            qa = get_qa_status(Path(tmp))
            self.assertEqual(qa["blocking_count"], 0)
            self.assertTrue(any("missing report" in warning for warning in qa["warnings"]))

    def test_007b_report_extracts_six_computed_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "output" / "data_governance_status.json",
                {
                    "allowed_to_enter_007b": True,
                    "allowed_to_enter_007b_scope": "small_scope",
                    "allowed_to_enter_008b": False,
                    "candidate_eligible_count": 0,
                    "candidate_blocked_count": 2,
                    "data_quality_failed_count": 1,
                    "end_date_coverage_gap_days": 15,
                    "manual_review_count": 0,
                    "factor_gate_status": "blocked_for_strategy_use",
                    "next_recommended_action": "keep blocked",
                    "etf_007b_full_scope_available": False,
                },
            )
            _write_csv(
                root / "output" / "etf_007b_metrics_report.csv",
                [
                    {
                        "symbol": "159928",
                        "name": "消费ETF",
                        "tracking_index_code": "000932",
                        "tracking_index_name": "中证主要消费",
                        "tracking_error": "0.02",
                        "relative_return_20d": "0.02",
                        "relative_return_60d": "0.03",
                        "relative_return_120d": "0.04",
                        "validation_status": "computed_valid",
                    },
                    {
                        "symbol": "510300",
                        "name": "沪深300ETF",
                        "tracking_index_code": "000300",
                        "tracking_index_name": "沪深300",
                        "tracking_error": "0.01",
                        "relative_return_20d": "0.02",
                        "relative_return_60d": "0.03",
                        "relative_return_120d": "0.04",
                        "validation_status": "computed_valid",
                    },
                    {
                        "symbol": "510500",
                        "name": "中证500ETF",
                        "tracking_index_code": "000905",
                        "tracking_index_name": "中证500",
                        "tracking_error": "0.03",
                        "relative_return_20d": "0.02",
                        "relative_return_60d": "0.03",
                        "relative_return_120d": "0.04",
                        "validation_status": "computed_valid",
                    },
                    {
                        "symbol": "510880",
                        "name": "红利ETF",
                        "tracking_index_code": "000015",
                        "tracking_index_name": "上证红利",
                        "tracking_error": "0.04",
                        "relative_return_20d": "0.02",
                        "relative_return_60d": "0.03",
                        "relative_return_120d": "0.04",
                        "validation_status": "computed_valid",
                    },
                    {
                        "symbol": "512100",
                        "name": "中证1000ETF",
                        "tracking_index_code": "000852",
                        "tracking_index_name": "中证1000",
                        "tracking_error": "0.05",
                        "relative_return_20d": "0.02",
                        "relative_return_60d": "0.03",
                        "relative_return_120d": "0.04",
                        "validation_status": "computed_valid",
                    },
                    {
                        "symbol": "512880",
                        "name": "证券ETF",
                        "tracking_index_code": "399975",
                        "tracking_index_name": "中证全指证券公司",
                        "tracking_error": "0.06",
                        "relative_return_20d": "0.02",
                        "relative_return_60d": "0.03",
                        "relative_return_120d": "0.04",
                        "validation_status": "computed_valid",
                    },
                    {
                        "symbol": "159915",
                        "name": "创业板ETF",
                        "tracking_index_code": "399006",
                        "tracking_index_name": "创业板指",
                        "tracking_error": "",
                        "relative_return_20d": "",
                        "relative_return_60d": "",
                        "relative_return_120d": "",
                        "validation_status": "no_index_cache",
                    },
                ],
            )
            _write_csv(root / "output" / "etf_007b_metrics_summary.csv", [{"summary_item": "computed_valid_count", "count": 6}])
            _write_csv(root / "output" / "index_007b_readiness.csv", [{"readiness_item": "usable_benchmark_count", "current_status": "passed", "blocking": False, "actual_value": 1}])
            summary = get_007b_summary(root)
            self.assertEqual(summary["computed_valid_count"], 6)
            self.assertIn("510300", set(summary["computed_valid"]["symbol"]))
            self.assertEqual(summary["no_index_cache_count"], 1)
            self.assertEqual(summary["allowed_to_enter_007b_scope"], "small_scope")
            self.assertFalse(summary["full_scope_available"])

    def test_008b_readiness_extracts_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "output" / "data_governance_status.json",
                {
                    "allowed_to_enter_007b": True,
                    "allowed_to_enter_007b_scope": "small_scope",
                    "allowed_to_enter_008b": False,
                    "candidate_eligible_count": 0,
                    "candidate_blocked_count": 1,
                    "data_quality_failed_count": 1,
                    "end_date_coverage_gap_days": 15,
                    "manual_review_count": 0,
                    "factor_gate_status": "blocked_for_strategy_use",
                    "next_recommended_action": "blocked",
                    "factor_008b_readiness_status": "blocked",
                },
            )
            _write_csv(root / "output" / "factor_008b_readiness.csv", [{"readiness_item": "candidate_eligible_count", "current_status": "blocked", "blocking": True, "actual_value": "0/269", "remediation_action": "clear blockers"}])
            _write_csv(root / "output" / "factor_score_gate.csv", [{"gate_item": "min_computable_ratio", "status": "blocked", "blocking": True, "actual_value": "0.48"}])
            _write_csv(root / "output" / "factor_score_audit.csv", [{"audit_item": "score_computable_count", "status": "ok", "count": 24, "finding": "24 of 50"}])
            summary = get_008b_summary(root)
            self.assertFalse(summary["allowed_to_enter_008b"])
            self.assertEqual(summary["readiness_status"], "blocked")
            self.assertEqual(summary["blockers"].iloc[0]["readiness_item"], "candidate_eligible_count")

    def test_candidate_gate_extracts_blocked_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows: list[dict[str, object]] = []
            rows.extend(
                {
                    "symbol": f"159{i:03d}",
                    "name": f"ETF{i}",
                    "candidate_status": "blocked_short_history",
                    "eligibility_status": "blocked",
                    "block_reason": "short_history",
                    "factor_gate_status": "blocked_for_strategy_use",
                }
                for i in range(239)
            )
            rows.extend(
                {
                    "symbol": f"560{i:03d}",
                    "name": f"ReviewETF{i}",
                    "candidate_status": "blocked_manual_review",
                    "eligibility_status": "blocked",
                    "block_reason": "manual_review_required",
                    "factor_gate_status": "blocked_for_strategy_use",
                }
                for i in range(5)
            )
            rows.extend(
                {
                    "symbol": f"588{i:03d}",
                    "name": f"NoFactorETF{i}",
                    "candidate_status": "blocked_no_used_factors",
                    "eligibility_status": "blocked",
                    "block_reason": "no_used_factors",
                    "factor_gate_status": "blocked_for_strategy_use",
                }
                for i in range(25)
            )
            _write_csv(root / "output" / "candidate_gate.csv", rows)
            _write_csv(root / "output" / "candidate_unblock_plan.csv", [{"symbol": "159000", "name": "ETF0", "unblock_status": "waiting", "next_action": "wait", "can_be_unblocked_by_benchmark_update": False}])
            summary = get_candidate_gate_summary(root)
            self.assertEqual(summary["eligible_count"], 0)
            self.assertEqual(summary["blocked_count"], 269)
            self.assertEqual(summary["blocked_short_history_count"], 239)
            self.assertEqual(summary["blocked_manual_review_count"], 5)
            self.assertEqual(summary["blocked_no_used_factors_count"], 25)

    def test_manual_review_list_extracts_five_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_csv(
                root / "output" / "short_history_observation_pool.csv",
                [
                    {"symbol": "159231", "name": "A", "history_status": "short_history", "estimated_trading_days_until_eligible": 10, "requires_manual_review": True, "low_liquidity_flag": True, "observation_status": "manual_review_required"},
                    {"symbol": "159246", "name": "B", "history_status": "very_short_history", "estimated_trading_days_until_eligible": 40, "requires_manual_review": False, "low_liquidity_flag": False, "observation_status": "waiting"},
                ],
            )
            _write_csv(
                root / "output" / "manual_review_list.csv",
                [
                    {"symbol": f"1592{i}", "name": f"ETF{i}", "manual_review_reason": "abnormal_return", "recommended_checks": "check source", "review_status": "blocked_until_review"}
                    for i in range(5)
                ],
            )
            summary = get_manual_review_summary(root)
            self.assertEqual(summary["manual_review_count"], 5)
            self.assertEqual(summary["low_liquidity_watch_count"], 1)
            self.assertEqual(summary["estimated_eligible_within_20d"], 1)
            self.assertEqual(summary["estimated_eligible_within_60d"], 2)

    def test_missing_columns_return_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output" / "qa_status_breakdown.csv"
            _write_csv(path, [{"qa_item": "x"}])
            report = load_csv_report(path, ["qa_item", "root_cause"])
            self.assertTrue(any("missing columns" in warning for warning in report["warnings"]))

    def test_parser_does_not_modify_protected_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protected_paths = [
                root / "data" / "cache" / "510300.csv",
                root / "data" / "index_cache" / "000300.csv",
                root / "output" / "compare_signal.csv",
                root / "output" / "compare_signal.txt",
                root / "output" / "equity_curve.csv",
                root / "output" / "performance.json",
            ]
            for path in protected_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("stable", encoding="utf-8")
            before = {path: _tree_hash(path) for path in protected_paths}
            get_governance_status(root)
            get_qa_status(root)
            get_candidate_gate_summary(root)
            get_007b_summary(root)
            get_008b_summary(root)
            get_manual_review_summary(root)
            after = {path: _tree_hash(path) for path in protected_paths}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
