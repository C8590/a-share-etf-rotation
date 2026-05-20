from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .io_utils import read_table, write_table


RECOMMENDATION_COLUMNS = [
    "recommendation_id",
    "grade",
    "title",
    "recommendation",
    "evidence_sources",
    "evidence_metric",
    "evidence_value",
    "market_state",
    "sector_l2",
    "trend_maturity",
    "label_policy",
    "sample_count",
    "risks",
    "rationale",
    "action_boundary",
]

GRADES = {"recommend_adopt", "recommend_observe", "do_not_adopt_yet", "forbidden_auto_apply"}


@dataclass(frozen=True)
class RecommendationRunResult:
    recommendations: pd.DataFrame
    report: str
    warnings: list[str]


def make_recommendations_from_artifacts(artifacts_dir: str | Path, out_dir: str | Path) -> RecommendationRunResult:
    artifacts = Path(artifacts_dir)
    loaded, warnings = _load_inputs(artifacts)
    recommendations = build_entry_rule_review_recommendations(loaded, warnings=warnings)
    report = build_entry_rule_review_report(recommendations, warnings)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    write_table(recommendations, out_path, "entry_rule_review_recommendations", "csv")
    (out_path / "entry_rule_review_recommendations.md").write_text(report, encoding="utf-8")
    return RecommendationRunResult(recommendations=recommendations, report=report, warnings=warnings)


def build_entry_rule_review_recommendations(
    inputs: dict[str, Any],
    warnings: list[str] | None = None,
) -> pd.DataFrame:
    warnings = list(warnings or [])
    rows: list[dict[str, Any]] = []

    calibration = inputs.get("calibration_suggestions")
    stability_text = str(inputs.get("ml_stability_report") or "")
    risk_scores = inputs.get("ml_entry_risk_scores")
    labeled = inputs.get("labeled_samples")

    stability = _summarize_stability(stability_text)
    risk_summary = _summarize_risk_scores(risk_scores, labeled)

    rows.append(_overall_bad_risk_row(stability, risk_summary, warnings))
    rows.extend(_calibration_rows(calibration))
    rows.extend(_model_fail_rows(stability))
    rows.extend(_forbidden_rows())

    df = pd.DataFrame(rows, columns=RECOMMENDATION_COLUMNS)
    if df.empty:
        df = pd.DataFrame(columns=RECOMMENDATION_COLUMNS)
    df["recommendation_id"] = [f"REC-{i:03d}" for i in range(1, len(df) + 1)]
    df["grade"] = df["grade"].where(df["grade"].isin(GRADES), "recommend_observe")
    return df[RECOMMENDATION_COLUMNS]


def build_entry_rule_review_report(recommendations: pd.DataFrame, warnings: list[str] | None = None) -> str:
    warnings = list(warnings or [])
    lines: list[str] = []
    lines.append("# entry_rule_review_recommendations")
    lines.append("")
    lines.append("This report is an offline historical_ml review artifact. It must not be used as a live trading signal, must not write back entry parameters, and must not connect to QMT.")
    lines.append("")
    if warnings:
        lines.append("## Input Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    sections = [
        ("Can Review", "recommend_adopt"),
        ("Should Observe", "recommend_observe"),
        ("Do Not Adopt Yet", "do_not_adopt_yet"),
        ("Forbidden Auto Apply", "forbidden_auto_apply"),
    ]
    for title, grade in sections:
        lines.append(f"## {title}")
        lines.append("")
        subset = recommendations.loc[recommendations["grade"].eq(grade)] if not recommendations.empty else pd.DataFrame()
        lines.append(_markdown(subset))
        lines.append("")

    lines.append("## Boundaries")
    lines.append("")
    lines.append("- historical_ml only provides offline diagnostics and manual review recommendations.")
    lines.append("- Do not automatically write these recommendations into entry parameters.")
    lines.append("- Do not use this report as a realtime trading signal.")
    lines.append("- Do not connect this workflow to QMT or order routing.")
    return "\n".join(lines) + "\n"


def _load_inputs(artifacts: Path) -> tuple[dict[str, Any], list[str]]:
    inputs: dict[str, Any] = {}
    warnings: list[str] = []
    table_specs = {
        "calibration_suggestions": artifacts / "entry_calibration_suggestions.csv",
        "ml_entry_risk_scores": artifacts / "ml_entry_risk_scores.csv",
        "labeled_samples": artifacts / "entry_candidate_samples_labeled.csv",
    }
    for key, path in table_specs.items():
        if not path.exists():
            warnings.append(f"missing optional input: {path.name}")
            continue
        try:
            inputs[key] = read_table(path)
        except Exception as exc:  # pragma: no cover - defensive file corruption path
            warnings.append(f"failed to read {path.name}: {exc}")
    for key, filename in {
        "ml_baseline_report": "ml_baseline_report.md",
        "ml_stability_report": "ml_stability_report.md",
    }.items():
        path = artifacts / filename
        if not path.exists():
            warnings.append(f"missing optional input: {filename}")
            continue
        inputs[key] = path.read_text(encoding="utf-8", errors="replace")
    return inputs, warnings


def _overall_bad_risk_row(stability: dict[str, Any], risk_summary: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    stable_lift = bool(stability.get("rolling_lift_gt_one") and stability.get("policy_lift_gt_one"))
    sample_count = int(risk_summary.get("sample_count", 0) or stability.get("rolling_min_sample_count", 0) or 0)
    has_missing = any("missing optional input" in w for w in warnings)
    grade = "recommend_adopt" if stable_lift and sample_count >= 30 and not has_missing else "recommend_observe"
    evidence = []
    if stability.get("rolling_lifts"):
        evidence.append("rolling_lifts=" + ",".join(f"{v:.4f}" for v in stability["rolling_lifts"]))
    if stability.get("policy_lifts"):
        evidence.append("label_policy_lifts=" + ",".join(f"{v:.4f}" for v in stability["policy_lifts"]))
    if risk_summary.get("high_risk_bad_rate") is not None:
        evidence.append(f"high_risk_bad_rate={risk_summary['high_risk_bad_rate']:.4f}")
    risks = ["behavior_feature_leakage_risk: low because risk scores are interpreted as diagnostics only"]
    if has_missing:
        risks.append("sample_size_risk: input warnings present")
    return _row(
        grade=grade,
        title="Overall bad_entry risk stratification",
        recommendation="Use the overall bad_entry high-risk bucket as an offline priority list for entry manual rule review.",
        evidence_sources="baseline_ml;stability",
        evidence_metric="rolling_and_label_policy_high_risk_lift",
        evidence_value=" | ".join(evidence) if evidence else "not available",
        market_state="all",
        sector_l2="all",
        trend_maturity="all",
        label_policy="strict/default/loose",
        sample_count=sample_count,
        risks="; ".join(risks),
        rationale="Stable lift above 1 indicates the bucket can rank historical bad_entry risk better than random, but it remains offline evidence.",
    )


def _calibration_rows(calibration: pd.DataFrame | None) -> list[dict[str, Any]]:
    if calibration is None or calibration.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, rec in calibration.head(20).iterrows():
        confidence = str(rec.get("confidence", "")).lower()
        market = str(rec.get("affected_market_state", "all") or "all")
        notes = str(rec.get("notes", "") or "")
        grade = "recommend_observe"
        if confidence == "high" and market != "defense" and "concentration warning" not in notes.lower():
            grade = "recommend_adopt"
        if market == "defense" or "model_fails" in notes.lower():
            grade = "do_not_adopt_yet"
        rows.append(
            _row(
                grade=grade,
                title=f"Calibration: {rec.get('parameter_area', 'unknown')}",
                recommendation=str(rec.get("suggested_action", "review calibration evidence manually")),
                evidence_sources="calibration",
                evidence_metric=str(rec.get("evidence_metric", "")),
                evidence_value=str(rec.get("evidence_value", "")),
                market_state=market,
                sector_l2="all",
                trend_maturity=_trend_scope(rec),
                label_policy="default",
                sample_count=int(pd.to_numeric(rec.get("sample_count", 0), errors="coerce") or 0),
                risks=_calibration_risks(rec),
                rationale=str(rec.get("current_pattern", "")),
            )
        )
    return rows


def _model_fail_rows(stability: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for market in stability.get("market_model_fails", []):
        rows.append(
            _row(
                grade="do_not_adopt_yet",
                title=f"Market state model_fails: {market}",
                recommendation="Do not absorb this market_state-specific ML conclusion until the failure mode is reviewed.",
                evidence_sources="stability",
                evidence_metric="market_state_status",
                evidence_value="model_fails",
                market_state=str(market),
                sector_l2="all",
                trend_maturity="all",
                label_policy="default",
                sample_count=0,
                risks="model_fails_risk: high",
                rationale="Stability diagnostics marked this market_state as model_fails.",
            )
        )
    for sector in stability.get("sector_model_fails", []):
        rows.append(
            _row(
                grade="do_not_adopt_yet",
                title=f"sector_l2 model_fails: {sector}",
                recommendation="Do not absorb this sector_l2-specific ML conclusion until benchmark quality and concentration are reviewed.",
                evidence_sources="stability;benchmark_quality",
                evidence_metric="sector_l2_status",
                evidence_value="model_fails",
                market_state="all",
                sector_l2=str(sector),
                trend_maturity="all",
                label_policy="default",
                sample_count=0,
                risks="model_fails_risk: high; sector_benchmark_risk: medium",
                rationale="Stability diagnostics marked this sector_l2 as model_fails.",
            )
        )
    return rows


def _forbidden_rows() -> list[dict[str, Any]]:
    forbidden = [
        ("Automatic entry parameter writeback", "Never automatically modify entry thresholds, weights, or config from historical_ml recommendations."),
        ("Realtime trading signal generation", "Never use this offline model or report to produce realtime buy/sell advice."),
        ("QMT or order-routing integration", "Never connect historical_ml diagnostics to QMT, order placement, or execution automation."),
    ]
    return [
        _row(
            grade="forbidden_auto_apply",
            title=title,
            recommendation=rec,
            evidence_sources="project_boundary",
            evidence_metric="forbidden_action",
            evidence_value="true",
            market_state="all",
            sector_l2="all",
            trend_maturity="all",
            label_policy="all",
            sample_count=0,
            risks="automation_risk: forbidden; trading_signal_risk: forbidden",
            rationale="This violates historical_ml project boundaries.",
            action_boundary="forbidden: do not implement automatically",
        )
        for title, rec in forbidden
    ]


def _summarize_stability(text: str) -> dict[str, Any]:
    rolling = _table_after_heading(text, "Rolling Time Validation")
    market = _table_after_heading(text, "Market State Validation")
    sector = _table_after_heading(text, "Sector L2 Validation")
    policy = _table_after_heading(text, "Label Policy Sensitivity")
    rolling_lifts = _numeric_list(rolling, "high_risk_lift")
    policy_lifts = _numeric_list(policy, "high_risk_lift")
    market_fails = _failed_values(market, "market_state")
    sector_fails = _failed_values(sector, "sector_l2")
    rolling_counts = _numeric_list(rolling, "sample_count")
    return {
        "rolling_lifts": rolling_lifts,
        "policy_lifts": policy_lifts,
        "rolling_lift_gt_one": bool(rolling_lifts and all(v > 1.0 for v in rolling_lifts)),
        "policy_lift_gt_one": bool(policy_lifts and all(v > 1.0 for v in policy_lifts)),
        "market_model_fails": market_fails,
        "sector_model_fails": sector_fails,
        "rolling_min_sample_count": min(rolling_counts) if rolling_counts else 0,
    }


def _summarize_risk_scores(risk_scores: pd.DataFrame | None, labeled: pd.DataFrame | None) -> dict[str, Any]:
    if risk_scores is None or risk_scores.empty:
        return {}
    df = risk_scores.copy()
    if "auto_label" not in df.columns and labeled is not None:
        df = df.merge(labeled[["trade_date", "code", "auto_label"]], on=["trade_date", "code"], how="left")
    if "auto_label" not in df.columns or "bad_entry_risk_bucket" not in df.columns:
        return {"sample_count": len(df)}
    bad = df["auto_label"].fillna("").astype(str).eq("bad_entry")
    high = df["bad_entry_risk_bucket"].fillna("").astype(str).eq("high")
    return {
        "sample_count": int(len(df)),
        "overall_bad_rate": float(bad.mean()) if len(df) else None,
        "high_risk_bad_rate": float(bad[high].mean()) if high.any() else None,
    }


def _table_after_heading(text: str, heading: str) -> pd.DataFrame:
    if not text:
        return pd.DataFrame()
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lstrip("#").strip().lower() == heading.lower():
            start = i + 1
            break
    if start is None:
        return pd.DataFrame()
    table_lines: list[str] = []
    seen_table = False
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#") and seen_table:
            break
        if stripped.startswith("|") and stripped.endswith("|"):
            seen_table = True
            table_lines.append(stripped)
        elif seen_table and stripped:
            break
    return _parse_markdown_table(table_lines)


def _parse_markdown_table(lines: list[str]) -> pd.DataFrame:
    if len(lines) < 2:
        return pd.DataFrame()
    header = _split_md_row(lines[0])
    rows = []
    for line in lines[2:]:
        cells = _split_md_row(line)
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells)))
    return pd.DataFrame(rows)


def _split_md_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _numeric_list(df: pd.DataFrame, col: str) -> list[float]:
    if df.empty or col not in df.columns:
        return []
    return [float(v) for v in pd.to_numeric(df[col], errors="coerce").dropna().tolist()]


def _failed_values(df: pd.DataFrame, value_col: str) -> list[str]:
    if df.empty or "status" not in df.columns or value_col not in df.columns:
        return []
    failed = df.loc[df["status"].astype(str).eq("model_fails"), value_col].fillna("").astype(str)
    return [v for v in failed.tolist() if v]


def _calibration_risks(rec: pd.Series) -> str:
    risks = []
    sample_count = int(pd.to_numeric(rec.get("sample_count", 0), errors="coerce") or 0)
    if sample_count < 30:
        risks.append("sample_size_risk: medium")
    notes = str(rec.get("notes", "") or "").lower()
    if "concentration warning" in notes:
        risks.append("sample_size_risk: concentration warning")
    if str(rec.get("affected_market_state", "")) == "defense":
        risks.append("model_fails_risk: defense requires separate review")
    return "; ".join(risks or ["sample_size_risk: low"])


def _trend_scope(rec: pd.Series) -> str:
    text = " ".join(str(rec.get(col, "")) for col in ["parameter_area", "current_pattern", "suggested_action", "notes"]).lower()
    if "overheat" in text:
        return "overheat"
    return "all"


def _row(
    *,
    grade: str,
    title: str,
    recommendation: str,
    evidence_sources: str,
    evidence_metric: str,
    evidence_value: str,
    market_state: str,
    sector_l2: str,
    trend_maturity: str,
    label_policy: str,
    sample_count: int,
    risks: str,
    rationale: str,
    action_boundary: str = "manual review only; no trading automation",
) -> dict[str, Any]:
    return {
        "recommendation_id": "",
        "grade": grade,
        "title": title,
        "recommendation": recommendation,
        "evidence_sources": evidence_sources,
        "evidence_metric": evidence_metric,
        "evidence_value": evidence_value,
        "market_state": market_state,
        "sector_l2": sector_l2,
        "trend_maturity": trend_maturity,
        "label_policy": label_policy,
        "sample_count": int(sample_count),
        "risks": risks,
        "rationale": rationale,
        "action_boundary": action_boundary,
    }


def _markdown(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "(no rows)"
    cols = [
        "recommendation_id",
        "title",
        "recommendation",
        "evidence_sources",
        "market_state",
        "sector_l2",
        "label_policy",
        "risks",
        "action_boundary",
    ]
    return df[cols].to_markdown(index=False)
