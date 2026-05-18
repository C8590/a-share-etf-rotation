from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


SOURCE_LAG_REPORT_COLUMNS = [
    "symbol",
    "name",
    "source",
    "cache_end_date",
    "latest_expected_date",
    "end_date_gap_days",
    "market_max_cache_date",
    "gap_vs_market_max_days",
    "sina_end_date",
    "eastmoney_qfq_status",
    "eastmoney_none_status",
    "source_lag_status",
    "blocker_type",
    "can_be_fixed_by_refresh",
    "can_be_fixed_by_waiting",
    "requires_source_diagnosis",
    "exclude_from_candidate_pool",
    "recommended_action",
    "notes",
]

SOURCE_LAG_SUMMARY_COLUMNS = [
    "summary_item",
    "count",
    "severity",
    "finding",
    "suggested_action",
    "examples",
    "notes",
]

SOURCE_LAG_STATUSES = {
    "source_lag_confirmed",
    "source_unavailable",
    "provider_stale",
    "proxy_blocked",
    "market_wide_lag",
    "unknown",
}

BLOCKING_SOURCE_LAG_STATUSES = {
    "source_lag_confirmed",
    "source_unavailable",
    "provider_stale",
    "proxy_blocked",
}


def _read_csv(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(file_path, dtype={"symbol": str}, encoding="utf-8-sig").fillna("")
    except Exception:
        return pd.DataFrame()


def _read_json(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _int(value: Any, default: int = 0) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(parsed) else int(float(parsed))


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _gap_days(start: Any, end: Any) -> int:
    left = pd.to_datetime(start, errors="coerce")
    right = pd.to_datetime(end, errors="coerce")
    if pd.isna(left) or pd.isna(right):
        return 0
    return max(0, int((right.normalize() - left.normalize()).days))


def _by_symbol(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    return {
        str(row.get("symbol", "")).zfill(6): row
        for row in frame.to_dict("records")
        if _text(row.get("symbol"))
    }


def _latest_expected_date(
    coverage: pd.DataFrame,
    failure_summary: pd.DataFrame,
    governance: dict[str, Any],
) -> str:
    candidates: list[pd.Timestamp] = []
    for frame in [failure_summary, coverage]:
        if frame.empty:
            continue
        for column in ["latest_expected_date", "target_update_date", "latest_date", "end_date"]:
            if column not in frame.columns:
                continue
            values = pd.to_datetime(frame[column], errors="coerce").dropna()
            if not values.empty:
                candidates.append(values.max().normalize())
    if isinstance(governance, dict):
        raw_gap = _int(governance.get("end_date_coverage_gap_days"), 0)
        if raw_gap and not coverage.empty and "end_date" in coverage.columns:
            end_dates = pd.to_datetime(coverage["end_date"], errors="coerce").dropna()
            if not end_dates.empty:
                candidates.append((end_dates.min().normalize() + pd.Timedelta(days=raw_gap)).normalize())
    if candidates:
        return str(max(candidates).date())
    return ""


def _market_max_cache_date(coverage: pd.DataFrame) -> str:
    if coverage.empty or "end_date" not in coverage.columns:
        return ""
    dates = pd.to_datetime(coverage["end_date"], errors="coerce").dropna()
    return "" if dates.empty else str(dates.max().date())


def _source_diag_status(source_diag: pd.DataFrame, symbol: str, check_type: str) -> str:
    if source_diag.empty or "check_type" not in source_diag.columns or "symbol" not in source_diag.columns:
        return "unknown"
    symbol_rows = source_diag[
        source_diag["symbol"].astype(str).str.zfill(6).eq(str(symbol).zfill(6))
        & source_diag["check_type"].astype(str).eq(check_type)
    ]
    global_rows = source_diag[source_diag["check_type"].astype(str).eq(check_type)]
    rows = symbol_rows if not symbol_rows.empty else global_rows
    if rows.empty:
        return "unknown"
    latest = rows.tail(1).iloc[0]
    if _bool(latest.get("success")):
        return "ok"
    error_type = _text(latest.get("error_type")).lower()
    diagnosis = _text(latest.get("diagnosis")).lower()
    if "proxy" in error_type or "proxy" in diagnosis:
        return "proxy_blocked"
    if "timeout" in error_type or "timeout" in diagnosis:
        return "timeout"
    if "empty" in diagnosis or "empty" in _text(latest.get("error_message")).lower():
        return "empty_data"
    return "failed"


def _source_diag_sina_rows(source_diag: pd.DataFrame, symbol: str) -> int:
    if source_diag.empty or "check_type" not in source_diag.columns or "symbol" not in source_diag.columns:
        return 0
    rows = source_diag[
        source_diag["symbol"].astype(str).str.zfill(6).eq(str(symbol).zfill(6))
        & source_diag["check_type"].astype(str).eq("akshare_sina")
    ]
    if rows.empty:
        return 0
    return _int(rows.tail(1).iloc[0].get("row_count"), 0)


def _cache_source(symbol: str, output_dir: Path, current_source: str) -> str:
    if current_source and current_source != "local_cache":
        return current_source
    cache_path = output_dir.parent / "data" / "cache" / f"{str(symbol).zfill(6)}.csv"
    if not cache_path.exists():
        return current_source
    try:
        frame = pd.read_csv(cache_path, dtype={"symbol": str}, usecols=lambda col: col in {"source"})
    except Exception:
        return current_source
    if frame.empty or "source" not in frame.columns:
        return current_source
    values = frame["source"].dropna().astype(str).str.strip()
    values = values[values.ne("")]
    return values.iloc[-1] if not values.empty else current_source


def classify_source_lag_status(
    *,
    source: str,
    cache_end_date: str,
    latest_expected_date: str,
    market_max_cache_date: str,
    end_date_gap_days: int,
    gap_vs_market_max_days: int,
    eastmoney_qfq_status: str = "unknown",
    eastmoney_none_status: str = "unknown",
    sina_row_count: int = 0,
    cache_row_count: int = 0,
    single_symbol_lag: bool = True,
    max_gap_days: int = 10,
    market_gap_threshold_days: int = 5,
) -> str:
    if end_date_gap_days <= max_gap_days:
        return "unknown"
    em_proxy_blocked = eastmoney_qfq_status == "proxy_blocked" or eastmoney_none_status == "proxy_blocked"
    if not single_symbol_lag and gap_vs_market_max_days <= market_gap_threshold_days:
        return "market_wide_lag"
    if gap_vs_market_max_days > market_gap_threshold_days:
        if "sina" in source.lower() and (sina_row_count == 0 or sina_row_count == cache_row_count):
            return "provider_stale"
        if em_proxy_blocked:
            return "proxy_blocked"
        return "source_lag_confirmed"
    if em_proxy_blocked:
        return "proxy_blocked"
    if not cache_end_date or not latest_expected_date or not market_max_cache_date:
        return "source_unavailable"
    return "unknown"


def identify_source_lag_symbols(
    *,
    coverage: pd.DataFrame,
    failure_summary: pd.DataFrame | None = None,
    latest_expected_date: str = "",
    max_gap_days: int = 10,
    market_gap_threshold_days: int = 5,
) -> pd.DataFrame:
    failure_summary = failure_summary if failure_summary is not None else pd.DataFrame()
    if coverage.empty or "symbol" not in coverage.columns:
        return pd.DataFrame()
    frame = coverage.copy()
    if "end_date" not in frame.columns and "latest_date" in frame.columns:
        frame["end_date"] = frame["latest_date"]
    market_max = _market_max_cache_date(frame)
    if not latest_expected_date:
        latest_expected_date = _latest_expected_date(frame, failure_summary, {})
    frame["cache_end_date"] = frame.get("end_date", "").map(_date_text)
    frame["latest_expected_date"] = latest_expected_date
    frame["market_max_cache_date"] = market_max
    frame["end_date_gap_days"] = frame["cache_end_date"].map(lambda value: _gap_days(value, latest_expected_date))
    frame["gap_vs_market_max_days"] = frame["cache_end_date"].map(lambda value: _gap_days(value, market_max))
    stale_symbols: set[str] = set()
    if not failure_summary.empty and "failure_type" in failure_summary.columns and "symbol" in failure_summary.columns:
        stale_symbols = set(
            failure_summary[failure_summary["failure_type"].astype(str).eq("stale_end_date")]["symbol"].astype(str).str.zfill(6)
        )
    mask = (frame["end_date_gap_days"] > max_gap_days) & (
        (frame["gap_vs_market_max_days"] > market_gap_threshold_days)
        | frame["symbol"].astype(str).str.zfill(6).isin(stale_symbols)
    )
    return frame.loc[mask].copy()


def build_source_lag_report(
    *,
    output_dir: str | Path = "output",
    coverage: pd.DataFrame | None = None,
    failure_summary: pd.DataFrame | None = None,
    source_diagnostics: pd.DataFrame | None = None,
    data_governance_status: dict[str, Any] | None = None,
    max_gap_days: int = 10,
    market_gap_threshold_days: int = 5,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    coverage_frame = coverage if coverage is not None else _read_csv(output_path / "data_coverage_report.csv")
    failure_frame = failure_summary if failure_summary is not None else _read_csv(output_path / "data_failure_summary.csv")
    source_diag_frame = source_diagnostics if source_diagnostics is not None else _read_csv(output_path / "source_diagnostics_report.csv")
    governance = data_governance_status if data_governance_status is not None else _read_json(output_path / "data_governance_status.json")
    latest_expected = _latest_expected_date(coverage_frame, failure_frame, governance)
    candidates = identify_source_lag_symbols(
        coverage=coverage_frame,
        failure_summary=failure_frame,
        latest_expected_date=latest_expected,
        max_gap_days=max_gap_days,
        market_gap_threshold_days=market_gap_threshold_days,
    )
    if candidates.empty:
        return []
    max_lag_symbols = set(candidates["symbol"].astype(str).str.zfill(6))
    single_symbol_lag = len(max_lag_symbols) == 1
    rows: list[dict[str, Any]] = []
    for raw in candidates.sort_values(["end_date_gap_days", "symbol"], ascending=[False, True]).to_dict("records"):
        symbol = str(raw.get("symbol", "")).zfill(6)
        cache_rows = _int(raw.get("rows") or raw.get("data_rows"), 0)
        source = _cache_source(symbol, output_path, _text(raw.get("source")) or "local_cache")
        qfq_status = _source_diag_status(source_diag_frame, symbol, "akshare_em_qfq")
        none_status = _source_diag_status(source_diag_frame, symbol, "akshare_em_none")
        sina_rows = _source_diag_sina_rows(source_diag_frame, symbol)
        cache_end = _date_text(raw.get("cache_end_date") or raw.get("end_date") or raw.get("latest_date"))
        market_max = _date_text(raw.get("market_max_cache_date"))
        status = classify_source_lag_status(
            source=source,
            cache_end_date=cache_end,
            latest_expected_date=latest_expected,
            market_max_cache_date=market_max,
            end_date_gap_days=_int(raw.get("end_date_gap_days")),
            gap_vs_market_max_days=_int(raw.get("gap_vs_market_max_days")),
            eastmoney_qfq_status=qfq_status,
            eastmoney_none_status=none_status,
            sina_row_count=sina_rows,
            cache_row_count=cache_rows,
            single_symbol_lag=single_symbol_lag,
            max_gap_days=max_gap_days,
            market_gap_threshold_days=market_gap_threshold_days,
        )
        blocker = status in BLOCKING_SOURCE_LAG_STATUSES
        notes = [
            "single symbol lags the market max cache date" if single_symbol_lag else "multiple symbols lag latest expected date",
            "Sina/cache end date trails market max" if "sina" in source.lower() else "",
            "EastMoney proxy unavailable in diagnostics" if qfq_status == "proxy_blocked" or none_status == "proxy_blocked" else "",
            "ordinary full-market refresh is not the primary fix",
        ]
        row = {
            "symbol": symbol,
            "name": _text(raw.get("name")),
            "source": source,
            "cache_end_date": cache_end,
            "latest_expected_date": latest_expected,
            "end_date_gap_days": _int(raw.get("end_date_gap_days")),
            "market_max_cache_date": market_max,
            "gap_vs_market_max_days": _int(raw.get("gap_vs_market_max_days")),
            "sina_end_date": cache_end if "sina" in source.lower() else "",
            "eastmoney_qfq_status": qfq_status,
            "eastmoney_none_status": none_status,
            "source_lag_status": status if status in SOURCE_LAG_STATUSES else "unknown",
            "blocker_type": "source_lag_blocker" if blocker else "coverage_gap_watch",
            "can_be_fixed_by_refresh": "maybe_after_source_available" if blocker else "maybe",
            "can_be_fixed_by_waiting": "maybe_after_source_available" if blocker else "maybe",
            "requires_source_diagnosis": bool(blocker),
            "exclude_from_candidate_pool": bool(blocker),
            "recommended_action": "keep blocked; diagnose provider/source lag; do not run full-market refresh for this alone" if blocker else "monitor coverage gap",
            "notes": "; ".join(item for item in notes if item),
        }
        rows.append({column: row.get(column, "") for column in SOURCE_LAG_REPORT_COLUMNS})
    return rows


def _examples(frame: pd.DataFrame, mask: pd.Series | None = None, limit: int = 5) -> str:
    if frame.empty or "symbol" not in frame.columns:
        return ""
    subset = frame.loc[mask] if mask is not None else frame
    parts: list[str] = []
    for row in subset.head(limit).to_dict("records"):
        parts.append(f"{str(row.get('symbol', '')).zfill(6)} {_text(row.get('name'))}".strip())
    return ";".join(parts)


def summarize_source_lag(
    rows: list[dict[str, Any]] | pd.DataFrame | None = None,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    frame = pd.DataFrame(rows) if rows is not None else _read_csv(report_path or "output/source_lag_report.csv")
    if frame.empty:
        return {
            "source_lag_report": "output/source_lag_report.csv",
            "source_lag_summary_report": "output/source_lag_summary.csv",
            "source_lag_count": 0,
            "source_lag_blocker_count": 0,
            "source_lag_symbols": [],
            "coverage_gap_driver_symbols": [],
            "source_lag_status_counts": {},
            "next_source_lag_action": "no source lag symbols detected",
            "top_examples": [],
        }
    blocking = frame["exclude_from_candidate_pool"].astype(str).str.lower().isin(["true", "1", "yes"])
    status_counts = frame["source_lag_status"].astype(str).value_counts().sort_index().to_dict()
    symbols = frame["symbol"].astype(str).str.zfill(6).tolist()
    drivers = frame.sort_values("end_date_gap_days", ascending=False)["symbol"].astype(str).str.zfill(6).tolist()
    return {
        "source_lag_report": "output/source_lag_report.csv",
        "source_lag_summary_report": "output/source_lag_summary.csv",
        "source_lag_count": int(len(frame)),
        "source_lag_blocker_count": int(blocking.sum()),
        "source_lag_symbols": symbols,
        "coverage_gap_driver_symbols": drivers,
        "source_lag_status_counts": {str(k): int(v) for k, v in status_counts.items()},
        "next_source_lag_action": "diagnose source lag for " + ",".join(drivers[:5]) + "; keep blocked; do not run full-market refresh for this alone",
        "top_examples": frame.head(10)[["symbol", "name", "source_lag_status", "end_date_gap_days", "recommended_action"]].to_dict("records"),
    }


def build_source_lag_summary_rows(rows: list[dict[str, Any]] | pd.DataFrame) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    summary = summarize_source_lag(frame)
    if frame.empty:
        return [
            {
                "summary_item": "source_lag",
                "count": 0,
                "severity": "info",
                "finding": "No source lag symbols detected.",
                "suggested_action": "Continue normal QA review.",
                "examples": "",
                "notes": "",
            }
        ]
    blocking = frame["exclude_from_candidate_pool"].astype(str).str.lower().isin(["true", "1", "yes"])
    out = [
        {
            "summary_item": "source_lag_blockers",
            "count": summary["source_lag_blocker_count"],
            "severity": "high" if summary["source_lag_blocker_count"] else "info",
            "finding": f"{summary['source_lag_blocker_count']} symbol(s) are blocked by source lag.",
            "suggested_action": summary["next_source_lag_action"],
            "examples": _examples(frame, blocking),
            "notes": "source lag is not cleared by qa_status or candidate gate",
        }
    ]
    for status, count in summary["source_lag_status_counts"].items():
        mask = frame["source_lag_status"].astype(str).eq(status)
        out.append(
            {
                "summary_item": f"source_lag_status:{status}",
                "count": int(count),
                "severity": "high" if status in BLOCKING_SOURCE_LAG_STATUSES else "medium",
                "finding": f"{count} symbol(s) classified as {status}.",
                "suggested_action": "diagnose provider/source lag; do not run full-market refresh for this alone",
                "examples": _examples(frame, mask),
                "notes": "classification is explanatory, not a QA relaxation",
            }
        )
    return out


def write_source_lag_report(
    rows: list[dict[str, Any]],
    *,
    report_path: str | Path = "output/source_lag_report.csv",
    summary_path: str | Path = "output/source_lag_summary.csv",
) -> tuple[Path, Path]:
    report = Path(report_path)
    summary = Path(summary_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=SOURCE_LAG_REPORT_COLUMNS).to_csv(report, index=False, encoding="utf-8-sig")
    pd.DataFrame(build_source_lag_summary_rows(rows), columns=SOURCE_LAG_SUMMARY_COLUMNS).to_csv(summary, index=False, encoding="utf-8-sig")
    return report, summary


def merge_source_lag_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    summary: dict[str, Any] | None = None,
) -> bool:
    path = Path(qa_report_path)
    report = _read_json(path)
    if not report:
        return False
    source_lag = summary if summary is not None else summarize_source_lag(report_path=path.parent / "source_lag_report.csv")
    data_layer = report.setdefault("data_layer", {})
    data_layer["source_lag"] = source_lag
    data_layer.update(
        {
            "source_lag_report": source_lag["source_lag_report"],
            "source_lag_summary_report": source_lag["source_lag_summary_report"],
            "source_lag_count": source_lag["source_lag_count"],
            "source_lag_blocker_count": source_lag["source_lag_blocker_count"],
            "source_lag_symbols": source_lag["source_lag_symbols"],
            "coverage_gap_driver_symbols": source_lag["coverage_gap_driver_symbols"],
            "next_source_lag_action": source_lag["next_source_lag_action"],
        }
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
