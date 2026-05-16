from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


QUALITY_DIAGNOSIS_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "failure_type",
    "primary_failure_type",
    "secondary_failure_type",
    "row_count",
    "min_required_rows",
    "first_date",
    "last_date",
    "latest_expected_date",
    "end_date_gap_days",
    "history_status",
    "cache_status",
    "liquidity_status",
    "price_quality_status",
    "metadata_status",
    "strategy_eligibility",
    "remediation_priority",
    "recommended_action",
    "requires_refresh",
    "requires_manual_review",
    "exclude_from_candidate_pool",
    "reason",
    "notes",
]

QUALITY_DIAGNOSIS_SUMMARY_COLUMNS = [
    "diagnosis_item",
    "count",
    "ratio",
    "severity",
    "suggested_action",
    "examples",
    "notes",
]

HISTORY_STATUSES = {"sufficient_history", "short_history", "very_short_history", "unknown"}
CACHE_STATUSES = {"fresh", "stale", "severely_stale", "missing", "unknown"}
STRATEGY_ELIGIBILITY_STATUSES = {
    "eligible",
    "observation_only",
    "blocked_short_history",
    "blocked_quality_failed",
    "blocked_missing_cache",
    "blocked_manual_review",
}
REMEDIATION_PRIORITIES = {
    "P0_refresh_needed",
    "P0_manual_review",
    "P1_short_history_observe",
    "P1_quality_investigate",
    "P2_low_liquidity_filter",
    "P3_metadata_enrichment",
    "no_action",
}

MANUAL_REVIEW_TYPES = {
    "abnormal_return",
    "invalid_ohlc",
    "missing_required_columns",
    "missing_values",
    "duplicate_dates",
    "unknown",
}
PRICE_QUALITY_TYPES = {
    "abnormal_return",
    "invalid_ohlc",
    "missing_required_columns",
    "missing_values",
    "duplicate_dates",
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
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int_value(value: Any, default: int = 0) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(parsed) else int(float(parsed))


def _float_value(value: Any) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else float(parsed)


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _split_types(value: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in str(value or "").replace("|", ";").split(";"):
        item = raw.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _latest_expected_date(*frames: pd.DataFrame) -> str:
    candidates: list[pd.Timestamp] = []
    for frame in frames:
        if frame.empty:
            continue
        for column in ["latest_expected_date", "target_update_date", "latest_date", "end_date", "local_latest_date"]:
            if column not in frame.columns:
                continue
            values = pd.to_datetime(frame[column], errors="coerce").dropna()
            if not values.empty:
                candidates.append(values.max().normalize())
    if candidates:
        return str(max(candidates).date())
    return str(pd.Timestamp.today().normalize().date())


def _gap_days(last_date: Any, latest_expected_date: Any) -> int:
    last = pd.to_datetime(last_date, errors="coerce")
    expected = pd.to_datetime(latest_expected_date, errors="coerce")
    if pd.isna(last) or pd.isna(expected):
        return 0
    return max(0, int((expected.normalize() - last.normalize()).days))


def classify_history_status(row_count: Any, min_required_rows: int = 250) -> str:
    rows = _int_value(row_count, default=-1)
    if rows < 0:
        return "unknown"
    if rows >= min_required_rows:
        return "sufficient_history"
    very_short_threshold = max(20, int(min_required_rows * 0.25))
    return "very_short_history" if rows < very_short_threshold else "short_history"


def classify_cache_staleness(
    *,
    cache_exists: Any,
    last_date: Any,
    latest_expected_date: Any,
    stale_days: int = 5,
    severe_stale_days: int = 10,
) -> str:
    if not _bool_value(cache_exists):
        return "missing"
    if not _date_text(last_date) or not _date_text(latest_expected_date):
        return "unknown"
    gap = _gap_days(last_date, latest_expected_date)
    if gap > severe_stale_days:
        return "severely_stale"
    if gap > stale_days:
        return "stale"
    return "fresh"


def classify_strategy_eligibility(
    *,
    history_status: str,
    cache_status: str,
    failure_types: list[str],
    requires_manual_review: bool,
    liquidity_status: str = "",
) -> str:
    if cache_status == "missing":
        return "blocked_missing_cache"
    if requires_manual_review:
        return "blocked_manual_review"
    if history_status in {"short_history", "very_short_history"} or "insufficient_rows" in failure_types:
        return "blocked_short_history"
    if any(item in PRICE_QUALITY_TYPES for item in failure_types):
        return "blocked_quality_failed"
    if liquidity_status == "low_liquidity":
        return "observation_only"
    return "eligible"


def _cache_exists_for(symbol: str, refresh_row: dict[str, Any], cache_dir: str | Path) -> bool:
    if refresh_row:
        value = refresh_row.get("cache_exists", "")
        if str(value).strip() != "":
            return _bool_value(value)
        cache_file = _text(refresh_row.get("cache_file", ""))
        if cache_file:
            return Path(cache_file).exists()
    return (Path(cache_dir) / f"{symbol}.csv").exists()


def _primary_diagnosis(
    *,
    failure_types: list[str],
    history_status: str,
    cache_status: str,
    row_count: int,
    min_required_rows: int,
    first_date: str,
    latest_expected_date: str,
) -> str:
    if cache_status == "missing":
        return "missing_cache"
    if cache_status in {"stale", "severely_stale"}:
        return "stale_cache"
    if "insufficient_rows" in failure_types or row_count < min_required_rows:
        first = pd.to_datetime(first_date, errors="coerce")
        expected = pd.to_datetime(latest_expected_date, errors="coerce")
        if pd.isna(first) or pd.isna(expected):
            return "short_history_unknown_age"
        age_days = int((expected.normalize() - first.normalize()).days)
        if age_days > int(min_required_rows * 1.6):
            return "old_etf_cache_incomplete"
        return "new_etf_short_history"
    if "invalid_ohlc" in failure_types:
        return "ohlc_anomaly"
    if "missing_required_columns" in failure_types:
        return "price_field_anomaly"
    if "missing_values" in failure_types:
        return "missing_values"
    if "duplicate_dates" in failure_types:
        return "duplicate_dates"
    if "abnormal_return" in failure_types:
        return "abnormal_return"
    if "zero_or_low_liquidity" in failure_types:
        return "low_liquidity"
    return "quality_failed"


def _recommended_action(priority: str, eligibility: str, primary: str) -> str:
    if priority == "P0_refresh_needed":
        return "targeted refresh candidate; verify coverage and compare before accepting refreshed cache"
    if priority == "P0_manual_review":
        return "manual price-quality review before any strategy use or refresh acceptance"
    if priority == "P1_short_history_observe":
        return "keep observation_only until row_count reaches the minimum history requirement"
    if priority == "P1_quality_investigate":
        return "investigate the quality failure and add a narrower classifier if it recurs"
    if priority == "P2_low_liquidity_filter":
        return "filter before candidate construction until liquidity improves"
    if priority == "P3_metadata_enrichment":
        return "enrich metadata; do not change price cache for this reason alone"
    if eligibility == "eligible":
        return "no remediation required from this diagnosis"
    return f"block candidate use until {primary} is resolved"


def _remediation_priority(
    *,
    primary: str,
    history_status: str,
    cache_status: str,
    requires_manual_review: bool,
    liquidity_status: str,
    metadata_status: str,
) -> str:
    if cache_status in {"missing", "stale", "severely_stale"} or primary == "old_etf_cache_incomplete":
        return "P0_refresh_needed"
    if requires_manual_review:
        return "P0_manual_review"
    if history_status in {"short_history", "very_short_history"}:
        return "P1_short_history_observe"
    if primary in {"quality_failed", "source_latest_lag"}:
        return "P1_quality_investigate"
    if liquidity_status == "low_liquidity":
        return "P2_low_liquidity_filter"
    if metadata_status in {"failed", "warning", "missing", "unknown"}:
        return "P3_metadata_enrichment"
    return "no_action"


def diagnose_quality_failure(
    *,
    quality_row: dict[str, Any],
    failure_rows: list[dict[str, Any]] | None = None,
    coverage_row: dict[str, Any] | None = None,
    refresh_row: dict[str, Any] | None = None,
    metadata_row: dict[str, Any] | None = None,
    latest_expected_date: str = "",
    min_required_rows: int = 250,
    min_avg_amount: float = 20_000_000.0,
    cache_dir: str | Path = "data/cache",
) -> dict[str, Any]:
    failure_rows = failure_rows or []
    coverage_row = coverage_row or {}
    refresh_row = refresh_row or {}
    metadata_row = metadata_row or {}
    symbol = _text(quality_row.get("symbol") or coverage_row.get("symbol") or refresh_row.get("symbol")).zfill(6)
    row_count = _int_value(quality_row.get("rows", coverage_row.get("rows", coverage_row.get("data_rows", 0))))
    first_date = _date_text(quality_row.get("start_date") or coverage_row.get("start_date") or coverage_row.get("listing_date"))
    last_date = _date_text(
        quality_row.get("end_date")
        or coverage_row.get("end_date")
        or coverage_row.get("latest_date")
        or refresh_row.get("latest_cache_date")
    )
    expected_date = _date_text(latest_expected_date or refresh_row.get("latest_expected_date") or coverage_row.get("target_update_date"))
    gap_days = max(
        [
            *[_int_value(row.get("end_date_gap_days", 0)) for row in failure_rows],
            _int_value(refresh_row.get("end_date_gap_days", 0)),
            _gap_days(last_date, expected_date),
        ]
        or [0]
    )

    failure_types = _split_types(quality_row.get("failure_types"))
    for row in failure_rows:
        for item in _split_types(row.get("failure_type")):
            if item not in failure_types:
                failure_types.append(item)
    if _text(quality_row.get("primary_failure_type")) and _text(quality_row.get("primary_failure_type")) not in failure_types:
        failure_types.insert(0, _text(quality_row.get("primary_failure_type")))

    cache_exists = _cache_exists_for(symbol, refresh_row, cache_dir)
    cache_last_date = _date_text(refresh_row.get("latest_cache_date")) or last_date
    history_status = classify_history_status(row_count, min_required_rows=min_required_rows)
    cache_status = classify_cache_staleness(
        cache_exists=cache_exists,
        last_date=cache_last_date,
        latest_expected_date=expected_date,
    )
    avg_amount = _float_value(coverage_row.get("avg_amount_20"))
    liquidity_status = "low_liquidity" if "zero_or_low_liquidity" in failure_types or (avg_amount is not None and avg_amount < min_avg_amount) else "ok"
    if any(item in {"invalid_ohlc", "missing_required_columns"} for item in failure_types):
        price_quality_status = "field_or_ohlc_anomaly"
    elif any(item in {"missing_values", "duplicate_dates", "abnormal_return"} for item in failure_types):
        price_quality_status = "requires_review"
    else:
        price_quality_status = "ok"
    metadata_status = _text(metadata_row.get("data_quality_status") or metadata_row.get("status") or "unknown")
    requires_manual_review = bool(set(failure_types) & MANUAL_REVIEW_TYPES)
    primary = _primary_diagnosis(
        failure_types=failure_types,
        history_status=history_status,
        cache_status=cache_status,
        row_count=row_count,
        min_required_rows=min_required_rows,
        first_date=first_date,
        latest_expected_date=expected_date,
    )
    secondary = [item for item in [*failure_types, liquidity_status if liquidity_status == "low_liquidity" else ""] if item and item != primary]
    strategy_eligibility = classify_strategy_eligibility(
        history_status=history_status,
        cache_status=cache_status,
        failure_types=failure_types,
        requires_manual_review=requires_manual_review,
        liquidity_status=liquidity_status,
    )
    priority = _remediation_priority(
        primary=primary,
        history_status=history_status,
        cache_status=cache_status,
        requires_manual_review=requires_manual_review,
        liquidity_status=liquidity_status,
        metadata_status=metadata_status,
    )
    requires_refresh = priority == "P0_refresh_needed"
    exclude = strategy_eligibility != "eligible"
    reasons = []
    for row in failure_rows:
        reason = _text(row.get("failure_reason"))
        if reason and reason not in reasons:
            reasons.append(reason)
    for field in ["errors", "warnings"]:
        reasons.extend([item for item in _split_types(quality_row.get(field)) if item not in reasons])
    notes = []
    if primary == "new_etf_short_history":
        notes.append("short history is not a low score; it blocks strategy eligibility until enough rows accumulate")
    if primary == "old_etf_cache_incomplete":
        notes.append("old calendar age with too few rows suggests incomplete local cache rather than a new ETF")
    if cache_status in {"stale", "severely_stale"}:
        notes.append("stale cache is a targeted refresh signal, not permission to refresh the full market")
    if "abnormal_return" in failure_types:
        notes.append("abnormal return may reflect adjustment/source issues and requires manual confirmation")

    row = {
        "symbol": symbol,
        "name": _text(quality_row.get("name") or coverage_row.get("name") or metadata_row.get("name")),
        "category": _text(metadata_row.get("inferred_category") or metadata_row.get("category") or coverage_row.get("category")),
        "sub_category": _text(metadata_row.get("sub_category") or coverage_row.get("sub_category")),
        "failure_type": ";".join(failure_types),
        "primary_failure_type": primary,
        "secondary_failure_type": ";".join(dict.fromkeys(secondary)),
        "row_count": row_count,
        "min_required_rows": min_required_rows,
        "first_date": first_date,
        "last_date": last_date,
        "latest_expected_date": expected_date,
        "end_date_gap_days": gap_days,
        "history_status": history_status,
        "cache_status": cache_status,
        "liquidity_status": liquidity_status,
        "price_quality_status": price_quality_status,
        "metadata_status": metadata_status,
        "strategy_eligibility": strategy_eligibility,
        "remediation_priority": priority,
        "recommended_action": _recommended_action(priority, strategy_eligibility, primary),
        "requires_refresh": bool(requires_refresh),
        "requires_manual_review": bool(requires_manual_review),
        "exclude_from_candidate_pool": bool(exclude),
        "reason": "; ".join(reasons),
        "notes": "; ".join(notes),
    }
    return {column: row.get(column, "") for column in QUALITY_DIAGNOSIS_COLUMNS}


def build_quality_remediation_plan(
    *,
    output_dir: str | Path = "output",
    cache_dir: str | Path = "data/cache",
    min_required_rows: int = 250,
    min_avg_amount: float = 20_000_000.0,
    quality_report: pd.DataFrame | None = None,
    failure_summary: pd.DataFrame | None = None,
    coverage_report: pd.DataFrame | None = None,
    cache_refresh_plan: pd.DataFrame | None = None,
    etf_metadata: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    quality = quality_report if quality_report is not None else _read_csv(output_path / "data_quality_report.csv")
    failures = failure_summary if failure_summary is not None else _read_csv(output_path / "data_failure_summary.csv")
    coverage = coverage_report if coverage_report is not None else _read_csv(output_path / "data_coverage_report.csv")
    refresh = cache_refresh_plan if cache_refresh_plan is not None else _read_csv(output_path / "cache_refresh_plan.csv")
    metadata = etf_metadata if etf_metadata is not None else _read_csv(output_path / "etf_metadata.csv")

    expected_date = _latest_expected_date(failures, refresh, coverage, quality)
    if quality.empty:
        symbols = sorted({str(row.get("symbol", "")).zfill(6) for row in failures.to_dict("records") if _text(row.get("symbol"))})
        quality_rows = [{"symbol": symbol, "status": "failed"} for symbol in symbols]
    else:
        quality_rows = [
            row
            for row in quality.to_dict("records")
            if str(row.get("status", "")).strip().lower() == "failed"
        ]

    def by_symbol(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
        if frame.empty or "symbol" not in frame.columns:
            return {}
        return {
            str(row.get("symbol", "")).zfill(6): row
            for row in frame.to_dict("records")
            if _text(row.get("symbol"))
        }

    coverage_by_symbol = by_symbol(coverage)
    refresh_by_symbol = by_symbol(refresh)
    metadata_by_symbol = by_symbol(metadata)
    failures_by_symbol: dict[str, list[dict[str, Any]]] = {}
    if not failures.empty and "symbol" in failures.columns:
        for row in failures.to_dict("records"):
            symbol = str(row.get("symbol", "")).zfill(6)
            if symbol:
                failures_by_symbol.setdefault(symbol, []).append(row)

    rows = [
        diagnose_quality_failure(
            quality_row=row,
            failure_rows=failures_by_symbol.get(str(row.get("symbol", "")).zfill(6), []),
            coverage_row=coverage_by_symbol.get(str(row.get("symbol", "")).zfill(6), {}),
            refresh_row=refresh_by_symbol.get(str(row.get("symbol", "")).zfill(6), {}),
            metadata_row=metadata_by_symbol.get(str(row.get("symbol", "")).zfill(6), {}),
            latest_expected_date=expected_date,
            min_required_rows=min_required_rows,
            min_avg_amount=min_avg_amount,
            cache_dir=cache_dir,
        )
        for row in quality_rows
    ]
    rows.sort(key=lambda row: (str(row["remediation_priority"]), str(row["primary_failure_type"]), str(row["symbol"])))
    return rows


def write_quality_diagnosis_report(
    rows: list[dict[str, Any]],
    *,
    report_path: str | Path = "output/data_quality_diagnosis.csv",
    summary_path: str | Path = "output/data_quality_diagnosis_summary.csv",
) -> tuple[Path, Path]:
    report = Path(report_path)
    summary = Path(summary_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=QUALITY_DIAGNOSIS_COLUMNS).to_csv(report, index=False, encoding="utf-8-sig")
    summary_rows = _summary_rows(rows)
    pd.DataFrame(summary_rows, columns=QUALITY_DIAGNOSIS_SUMMARY_COLUMNS).to_csv(summary, index=False, encoding="utf-8-sig")
    return report, summary


def _examples(frame: pd.DataFrame, mask: pd.Series, limit: int = 5) -> str:
    examples = frame.loc[mask, ["symbol", "name"]].head(limit).to_dict("records")
    return ";".join(f"{item['symbol']} {item['name']}" for item in examples)


def _summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    total = max(1, int(len(frame)))
    specs = [
        ("short_history", frame["history_status"].isin(["short_history", "very_short_history"]), "high", "observe until minimum row count is met", "new or short-history ETF cannot enter candidate scoring"),
        ("stale_cache", frame["cache_status"].isin(["stale", "severely_stale"]), "high", "targeted refresh after backup/compare", "do not refresh the full market"),
        ("missing_cache", frame["cache_status"].eq("missing"), "high", "targeted missing-cache repair", "download only selected symbols if approved"),
        ("abnormal_return", frame["failure_type"].astype(str).str.contains("abnormal_return", regex=False), "high", "manual adjustment/source review", "do not treat jump as alpha"),
        ("low_liquidity", frame["liquidity_status"].eq("low_liquidity"), "medium", "filter before candidate construction", "liquidity filter is not a score penalty"),
        ("manual_review_required", frame["requires_manual_review"].astype(str).str.lower().isin(["true", "1", "yes"]), "high", "manual confirmation required", "quality issue cannot be auto-accepted"),
        ("refresh_needed", frame["requires_refresh"].astype(str).str.lower().isin(["true", "1", "yes"]), "high", "queue targeted refresh candidates", "refresh does not imply QA pass"),
        ("candidate_excluded", frame["exclude_from_candidate_pool"].astype(str).str.lower().isin(["true", "1", "yes"]), "high", "gate before candidate pool", "blocked_short_history is not a low score"),
    ]
    out: list[dict[str, Any]] = []
    for item, mask, severity, action, notes in specs:
        count = int(mask.sum())
        out.append(
            {
                "diagnosis_item": item,
                "count": count,
                "ratio": round(count / total, 6),
                "severity": severity if count else "info",
                "suggested_action": action if count else "no action",
                "examples": _examples(frame, mask),
                "notes": notes,
            }
        )
    for column in ["primary_failure_type", "strategy_eligibility", "remediation_priority"]:
        for value, count in frame[column].value_counts().sort_index().items():
            mask = frame[column].eq(value)
            out.append(
                {
                    "diagnosis_item": f"{column}:{value}",
                    "count": int(count),
                    "ratio": round(int(count) / total, 6),
                    "severity": "high" if column != "remediation_priority" or str(value).startswith("P0") else "medium",
                    "suggested_action": "see per-symbol recommended_action",
                    "examples": _examples(frame, mask),
                    "notes": f"grouped by {column}",
                }
            )
    return out


def summarize_quality_diagnosis(rows: list[dict[str, Any]] | None = None, *, report_path: str | Path | None = None, example_limit: int = 10) -> dict[str, Any]:
    if rows is None:
        frame = _read_csv(report_path or "output/data_quality_diagnosis.csv")
    else:
        frame = pd.DataFrame(rows)
    if frame.empty:
        return {
            "total_failed": 0,
            "short_history_count": 0,
            "stale_cache_count": 0,
            "missing_cache_count": 0,
            "abnormal_return_count": 0,
            "low_liquidity_count": 0,
            "severe_quality_issue_count": 0,
            "candidate_excluded_count": 0,
            "manual_review_required_count": 0,
            "refresh_needed_count": 0,
            "history_status_counts": {},
            "cache_status_counts": {},
            "strategy_eligibility_counts": {},
            "remediation_priority_counts": {},
            "top_blocking_reasons": {},
            "top_examples": [],
        }

    def bool_count(column: str) -> int:
        return int(frame[column].astype(str).str.lower().isin(["true", "1", "yes"]).sum())

    severe_mask = (
        frame["price_quality_status"].isin(["field_or_ohlc_anomaly", "requires_review"])
        | frame["cache_status"].isin(["missing", "severely_stale"])
        | frame["requires_manual_review"].astype(str).str.lower().isin(["true", "1", "yes"])
    )
    top_reasons = frame["primary_failure_type"].value_counts().head(example_limit).to_dict()
    examples = frame.head(example_limit)[
        [
            "symbol",
            "name",
            "primary_failure_type",
            "history_status",
            "cache_status",
            "strategy_eligibility",
            "recommended_action",
        ]
    ].to_dict("records")
    return {
        "total_failed": int(len(frame)),
        "short_history_count": int(frame["history_status"].isin(["short_history", "very_short_history"]).sum()),
        "stale_cache_count": int(frame["cache_status"].isin(["stale", "severely_stale"]).sum()),
        "missing_cache_count": int(frame["cache_status"].eq("missing").sum()),
        "abnormal_return_count": int(frame["failure_type"].astype(str).str.contains("abnormal_return", regex=False).sum()),
        "low_liquidity_count": int(frame["liquidity_status"].eq("low_liquidity").sum()),
        "severe_quality_issue_count": int(severe_mask.sum()),
        "candidate_excluded_count": bool_count("exclude_from_candidate_pool"),
        "manual_review_required_count": bool_count("requires_manual_review"),
        "refresh_needed_count": bool_count("requires_refresh"),
        "history_status_counts": {str(k): int(v) for k, v in frame["history_status"].value_counts().sort_index().to_dict().items()},
        "cache_status_counts": {str(k): int(v) for k, v in frame["cache_status"].value_counts().sort_index().to_dict().items()},
        "strategy_eligibility_counts": {str(k): int(v) for k, v in frame["strategy_eligibility"].value_counts().sort_index().to_dict().items()},
        "remediation_priority_counts": {str(k): int(v) for k, v in frame["remediation_priority"].value_counts().sort_index().to_dict().items()},
        "top_blocking_reasons": {str(k): int(v) for k, v in top_reasons.items()},
        "top_examples": examples,
    }


def merge_quality_diagnosis_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    rows: list[dict[str, Any]] | None = None,
) -> bool:
    path = Path(qa_report_path)
    if not path.exists():
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    data_layer = report.setdefault("data_layer", {})
    summary = summarize_quality_diagnosis(rows, report_path=Path(path).parent / "data_quality_diagnosis.csv")
    data_layer.update(
        {
            "data_quality_diagnosis_report": str(Path(path).parent / "data_quality_diagnosis.csv"),
            "data_quality_diagnosis_summary_report": str(Path(path).parent / "data_quality_diagnosis_summary.csv"),
            "data_quality_diagnosis": summary,
            "short_history_count": summary["short_history_count"],
            "stale_cache_count": summary["stale_cache_count"],
            "severe_quality_issue_count": summary["severe_quality_issue_count"],
            "candidate_excluded_count": summary["candidate_excluded_count"],
            "manual_review_required_count": summary["manual_review_required_count"],
            "refresh_needed_count": summary["refresh_needed_count"],
            "top_blocking_reasons": summary["top_blocking_reasons"],
            "top_examples": summary["top_examples"],
        }
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
