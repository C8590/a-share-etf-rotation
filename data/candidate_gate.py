from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


CANDIDATE_GATE_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "candidate_status",
    "eligibility_status",
    "gate_passed",
    "blocked",
    "block_reason",
    "observation_reason",
    "data_quality_status",
    "history_status",
    "cache_status",
    "liquidity_status",
    "price_quality_status",
    "strategy_eligibility",
    "remediation_priority",
    "requires_manual_review",
    "exclude_from_candidate_pool",
    "factor_score_status",
    "factor_gate_status",
    "recommended_action",
    "notes",
]

CANDIDATE_GATE_SUMMARY_COLUMNS = [
    "gate_item",
    "count",
    "ratio",
    "severity",
    "finding",
    "suggested_action",
    "examples",
    "notes",
]

CANDIDATE_STATUS_VALUES = {
    "eligible",
    "observation_only",
    "blocked_short_history",
    "blocked_manual_review",
    "blocked_quality_failed",
    "blocked_no_used_factors",
    "blocked_factor_gate",
    "unknown",
}

BLOCKED_STATUSES = {
    "blocked_short_history",
    "blocked_manual_review",
    "blocked_quality_failed",
    "blocked_no_used_factors",
    "blocked_factor_gate",
}


def _read_csv(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    return pd.read_csv(file_path, dtype={"symbol": str}, encoding="utf-8-sig").fillna("")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _factor_gate_status(gate_rows: pd.DataFrame | list[dict[str, Any]] | None) -> str:
    if gate_rows is None:
        return "unknown"
    frame = pd.DataFrame(gate_rows)
    if frame.empty:
        return "unknown"
    blocking = frame.get("blocking", pd.Series(dtype=object)).astype(str).str.lower().isin(["true", "1", "yes"])
    blocked = frame.get("status", pd.Series(dtype=object)).astype(str).eq("blocked")
    if bool((blocking & blocked).any()):
        return "blocked_for_strategy_use"
    if bool(blocked.any()):
        return "warning"
    return "passed"


def evaluate_candidate_eligibility(
    *,
    symbol: str,
    name: str = "",
    diagnosis_row: dict[str, Any] | None = None,
    factor_score_row: dict[str, Any] | None = None,
    source_lag_row: dict[str, Any] | None = None,
    factor_gate_status: str = "unknown",
) -> dict[str, Any]:
    diagnosis = diagnosis_row or {}
    factor = factor_score_row or {}
    source_lag = source_lag_row or {}
    symbol = str(symbol).zfill(6)
    requires_manual_review = _bool_value(diagnosis.get("requires_manual_review", False))
    source_lag_blocked = _bool_value(source_lag.get("exclude_from_candidate_pool", False))
    exclude_from_candidate_pool = _bool_value(diagnosis.get("exclude_from_candidate_pool", False)) or source_lag_blocked
    strategy_eligibility = _text(diagnosis.get("strategy_eligibility"))
    if source_lag_blocked and not strategy_eligibility:
        strategy_eligibility = "blocked_quality_failed"
    history_status = _text(diagnosis.get("history_status") or "unknown")
    cache_status = _text(diagnosis.get("cache_status") or source_lag.get("source_lag_status") or "unknown")
    liquidity_status = _text(diagnosis.get("liquidity_status") or "unknown")
    price_quality_status = _text(diagnosis.get("price_quality_status") or "unknown")
    remediation_priority = _text(diagnosis.get("remediation_priority"))
    factor_score_status = _text(factor.get("score_status") or "unknown")

    observation_reasons: list[str] = []
    block_reasons: list[str] = []
    notes: list[str] = []
    if liquidity_status == "low_liquidity":
        observation_reasons.append("low_liquidity")
    if factor_score_status == "no_used_factors":
        notes.append("no_used_factors is unscoreable evidence, not a low score")
    if source_lag_blocked:
        notes.append("source lag blocker; keep blocked; not fixable by ordinary refresh")

    if source_lag_blocked:
        candidate_status = "blocked_quality_failed"
        block_reasons.append("source_lag_blocker")
    elif requires_manual_review:
        candidate_status = "blocked_manual_review"
        block_reasons.append("manual_review_required")
    elif strategy_eligibility == "blocked_short_history" or history_status in {"short_history", "very_short_history"}:
        candidate_status = "blocked_short_history"
        block_reasons.append("short_history")
    elif strategy_eligibility in {"blocked_quality_failed", "blocked_missing_cache"} or exclude_from_candidate_pool:
        candidate_status = "blocked_quality_failed"
        block_reasons.append(strategy_eligibility or "exclude_from_candidate_pool")
    elif factor_score_status == "no_used_factors":
        candidate_status = "blocked_no_used_factors"
        block_reasons.append("no_used_factors")
    elif factor_gate_status == "blocked_for_strategy_use":
        candidate_status = "blocked_factor_gate"
        block_reasons.append("factor_score_gate_blocked_for_strategy_use")
    elif strategy_eligibility == "observation_only" or observation_reasons:
        candidate_status = "observation_only"
    elif factor_score_status in {"ok", "unknown"}:
        candidate_status = "eligible"
    else:
        candidate_status = "unknown"
        block_reasons.append(f"unclassified_factor_score_status:{factor_score_status}")

    if exclude_from_candidate_pool and "exclude_from_candidate_pool" not in block_reasons:
        block_reasons.append("exclude_from_candidate_pool")
    blocked = candidate_status in BLOCKED_STATUSES or exclude_from_candidate_pool
    gate_passed = candidate_status == "eligible" and not blocked
    data_quality_status = "failed" if diagnosis else "not_in_diagnosis"
    recommended_action = _text(source_lag.get("recommended_action")) or _text(diagnosis.get("recommended_action"))
    if not recommended_action:
        if candidate_status == "blocked_factor_gate":
            recommended_action = "keep factor score in observation mode; do not enter ETF-GAP-008B"
        elif candidate_status == "blocked_no_used_factors":
            recommended_action = "do not score as low; wait for usable factor coverage"
        elif candidate_status == "observation_only":
            recommended_action = "observe only until liquidity and evidence are adequate"
        elif candidate_status == "eligible":
            recommended_action = "eligible for future candidate research only if global factor gate passes"
        else:
            recommended_action = "review inputs before candidate use"

    row = {
        "symbol": symbol,
        "name": _text(name or diagnosis.get("name") or factor.get("name") or source_lag.get("name")),
        "category": _text(diagnosis.get("category")),
        "sub_category": _text(diagnosis.get("sub_category")),
        "candidate_status": candidate_status,
        "eligibility_status": "passed" if gate_passed else ("blocked" if blocked else "observation_only"),
        "gate_passed": bool(gate_passed),
        "blocked": bool(blocked),
        "block_reason": ";".join(dict.fromkeys([item for item in block_reasons if item])),
        "observation_reason": ";".join(dict.fromkeys(observation_reasons)),
        "data_quality_status": data_quality_status,
        "history_status": history_status,
        "cache_status": cache_status,
        "liquidity_status": liquidity_status,
        "price_quality_status": price_quality_status,
        "strategy_eligibility": strategy_eligibility,
        "remediation_priority": remediation_priority,
        "requires_manual_review": bool(requires_manual_review),
        "exclude_from_candidate_pool": bool(exclude_from_candidate_pool),
        "factor_score_status": factor_score_status,
        "factor_gate_status": factor_gate_status,
        "recommended_action": recommended_action,
        "notes": "; ".join([_text(diagnosis.get("notes")), _text(source_lag.get("notes")), *notes]).strip("; "),
    }
    return {column: row.get(column, "") for column in CANDIDATE_GATE_COLUMNS}


def build_candidate_gate_report(
    *,
    output_dir: str | Path = "output",
    diagnosis_path: str | Path | None = None,
    factor_score_path: str | Path | None = None,
    factor_gate_path: str | Path | None = None,
    etf_metrics_path: str | Path | None = None,
    source_lag_path: str | Path | None = None,
    diagnosis: pd.DataFrame | None = None,
    factor_score: pd.DataFrame | None = None,
    factor_gate: pd.DataFrame | None = None,
    etf_metrics: pd.DataFrame | None = None,
    source_lag: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    diagnosis_frame = diagnosis if diagnosis is not None else _read_csv(diagnosis_path or output_path / "data_quality_diagnosis.csv")
    factor_frame = factor_score if factor_score is not None else _read_csv(factor_score_path or output_path / "factor_score_report.csv")
    factor_gate_frame = factor_gate if factor_gate is not None else _read_csv(factor_gate_path or output_path / "factor_score_gate.csv")
    metrics_frame = etf_metrics if etf_metrics is not None else _read_csv(etf_metrics_path or output_path / "etf_metrics.csv")
    if source_lag is not None:
        source_lag_frame = source_lag
    elif source_lag_path is not None or all(item is None for item in [diagnosis, factor_score, factor_gate, etf_metrics]):
        source_lag_frame = _read_csv(source_lag_path or output_path / "source_lag_report.csv")
    else:
        source_lag_frame = pd.DataFrame()

    gate_status = _factor_gate_status(factor_gate_frame)

    def by_symbol(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
        if frame.empty or "symbol" not in frame.columns:
            return {}
        return {
            str(row.get("symbol", "")).zfill(6): row
            for row in frame.to_dict("records")
            if _text(row.get("symbol"))
        }

    diagnosis_by_symbol = by_symbol(diagnosis_frame)
    factor_by_symbol = by_symbol(factor_frame)
    source_lag_by_symbol = by_symbol(source_lag_frame)
    metrics_by_symbol = by_symbol(metrics_frame)
    symbols = sorted(set(diagnosis_by_symbol) | set(factor_by_symbol) | set(source_lag_by_symbol))
    rows: list[dict[str, Any]] = []
    status_order = {
        "blocked_manual_review": 0,
        "blocked_short_history": 1,
        "blocked_quality_failed": 2,
        "blocked_no_used_factors": 3,
        "blocked_factor_gate": 4,
        "observation_only": 5,
        "eligible": 6,
        "unknown": 7,
    }
    for symbol in symbols:
        diagnosis_row = dict(diagnosis_by_symbol.get(symbol, {}))
        factor_row = dict(factor_by_symbol.get(symbol, {}))
        source_lag_row = dict(source_lag_by_symbol.get(symbol, {}))
        metrics_row = dict(metrics_by_symbol.get(symbol, {}))
        if diagnosis_row:
            if not _text(diagnosis_row.get("category")):
                diagnosis_row["category"] = metrics_row.get("category", "")
            if not _text(diagnosis_row.get("sub_category")):
                diagnosis_row["sub_category"] = metrics_row.get("sub_category", "")
        name = _text(diagnosis_row.get("name") or factor_row.get("name") or metrics_row.get("name") or source_lag_row.get("name"))
        rows.append(
            evaluate_candidate_eligibility(
                symbol=symbol,
                name=name,
                diagnosis_row=diagnosis_row,
                factor_score_row=factor_row,
                source_lag_row=source_lag_row,
                factor_gate_status=gate_status,
            )
        )
    rows.sort(key=lambda row: (status_order.get(str(row["candidate_status"]), 99), str(row["symbol"])))
    return rows


def _examples(frame: pd.DataFrame, mask: pd.Series, limit: int = 5) -> str:
    examples = frame.loc[mask, ["symbol", "name"]].head(limit).to_dict("records")
    return ";".join(f"{item['symbol']} {item['name']}" for item in examples)


def _summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    total = max(1, len(frame))
    specs = [
        ("eligible", frame["candidate_status"].eq("eligible"), "info", "Eligible rows are only research inputs if the global factor gate passes.", "use only after QA and factor gate are clean"),
        ("observation_only", frame["candidate_status"].eq("observation_only"), "medium", "Observation-only rows must stay out of candidate construction.", "watch but do not score into candidates"),
        ("blocked", frame["blocked"].astype(str).str.lower().isin(["true", "1", "yes"]), "high", "Blocked rows cannot enter candidate construction.", "candidate gate is upstream of scoring"),
        ("blocked_short_history", frame["candidate_status"].eq("blocked_short_history"), "high", "Short history blocks candidate use and is not a low score.", "wait for minimum row count"),
        ("blocked_manual_review", frame["candidate_status"].eq("blocked_manual_review"), "high", "Manual review rows require confirmation before candidate use.", "review abnormal/unknown price quality"),
        ("blocked_no_used_factors", frame["candidate_status"].eq("blocked_no_used_factors"), "high", "No usable factors is unscoreable, not bearish.", "improve factor coverage"),
        ("blocked_factor_gate", frame["factor_gate_status"].eq("blocked_for_strategy_use"), "high", "Global factor score gate blocks candidate research.", "do not enter ETF-GAP-008B"),
        ("low_liquidity_observation", frame["observation_reason"].astype(str).str.contains("low_liquidity", regex=False), "medium", "Low liquidity is carried as an observation reason.", "filter or observe until tradability improves"),
    ]
    out: list[dict[str, Any]] = []
    for item, mask, severity, finding, action in specs:
        count = int(mask.sum())
        out.append(
            {
                "gate_item": item,
                "count": count,
                "ratio": round(count / total, 6),
                "severity": severity if count else "info",
                "finding": finding,
                "suggested_action": action if count else "no action",
                "examples": _examples(frame, mask),
                "notes": "candidate gate report aggregation",
            }
        )
    for value, count in frame["candidate_status"].value_counts().sort_index().items():
        mask = frame["candidate_status"].eq(value)
        out.append(
            {
                "gate_item": f"candidate_status:{value}",
                "count": int(count),
                "ratio": round(int(count) / total, 6),
                "severity": "high" if str(value).startswith("blocked") else "medium",
                "finding": f"{count} row(s) have candidate_status={value}.",
                "suggested_action": "see per-symbol recommended_action",
                "examples": _examples(frame, mask),
                "notes": "grouped by candidate_status",
            }
        )
    return out


def write_candidate_gate_report(
    rows: list[dict[str, Any]],
    *,
    report_path: str | Path = "output/candidate_gate.csv",
    summary_path: str | Path = "output/candidate_gate_summary.csv",
) -> tuple[Path, Path]:
    report = Path(report_path)
    summary = Path(summary_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=CANDIDATE_GATE_COLUMNS).to_csv(report, index=False, encoding="utf-8-sig")
    pd.DataFrame(_summary_rows(rows), columns=CANDIDATE_GATE_SUMMARY_COLUMNS).to_csv(summary, index=False, encoding="utf-8-sig")
    return report, summary


def summarize_candidate_gate(rows: list[dict[str, Any]] | None = None, *, report_path: str | Path | None = None, example_limit: int = 10) -> dict[str, Any]:
    frame = pd.DataFrame(rows) if rows is not None else _read_csv(report_path or "output/candidate_gate.csv")
    if frame.empty:
        return {
            "total_symbols": 0,
            "eligible_count": 0,
            "observation_only_count": 0,
            "blocked_count": 0,
            "blocked_short_history_count": 0,
            "blocked_manual_review_count": 0,
            "blocked_factor_gate_count": 0,
            "blocked_no_used_factors_count": 0,
            "low_liquidity_observation_count": 0,
            "candidate_status_counts": {},
            "top_blocking_reasons": {},
            "top_examples": [],
        }

    def count_status(status: str) -> int:
        return int(frame["candidate_status"].eq(status).sum())

    blocked_mask = frame["blocked"].astype(str).str.lower().isin(["true", "1", "yes"])
    reason_counts: dict[str, int] = {}
    for text in frame.loc[blocked_mask, "block_reason"].fillna("").astype(str):
        for item in [part.strip() for part in text.split(";") if part.strip()]:
            reason_counts[item] = reason_counts.get(item, 0) + 1
    top_examples = frame.head(example_limit)[
        ["symbol", "name", "candidate_status", "block_reason", "observation_reason", "recommended_action"]
    ].to_dict("records")
    return {
        "total_symbols": int(len(frame)),
        "eligible_count": count_status("eligible"),
        "observation_only_count": count_status("observation_only"),
        "blocked_count": int(blocked_mask.sum()),
        "blocked_short_history_count": count_status("blocked_short_history"),
        "blocked_manual_review_count": count_status("blocked_manual_review"),
        "blocked_factor_gate_count": int(frame["factor_gate_status"].eq("blocked_for_strategy_use").sum()),
        "blocked_no_used_factors_count": count_status("blocked_no_used_factors"),
        "low_liquidity_observation_count": int(frame["observation_reason"].astype(str).str.contains("low_liquidity", regex=False).sum()),
        "candidate_status_counts": {str(k): int(v) for k, v in frame["candidate_status"].value_counts().sort_index().to_dict().items()},
        "top_blocking_reasons": {str(k): int(v) for k, v in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:example_limit]},
        "top_examples": top_examples,
    }


def merge_candidate_gate_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    rows: list[dict[str, Any]] | None = None,
) -> bool:
    path = Path(qa_report_path)
    if not path.exists():
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    strategy_layer = report.setdefault("strategy_layer", {})
    output_dir = path.parent
    summary = summarize_candidate_gate(rows, report_path=output_dir / "candidate_gate.csv")
    strategy_layer.update(
        {
            "candidate_gate_report": str(output_dir / "candidate_gate.csv"),
            "candidate_gate_summary_report": str(output_dir / "candidate_gate_summary.csv"),
            "candidate_gate": summary,
        }
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
