from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from data.index_data import INDEX_CACHE_COLUMNS, INDEX_MAP_COLUMNS


ETF_METRICS_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "tracking_index_code",
    "tracking_index_name",
    "benchmark_available",
    "benchmark_status",
    "metric_status",
    "tracking_error",
    "tracking_error_status",
    "relative_return_20d",
    "relative_return_60d",
    "relative_return_120d",
    "benchmark_return_20d",
    "benchmark_return_60d",
    "benchmark_return_120d",
    "etf_return_20d",
    "etf_return_60d",
    "etf_return_120d",
    "discount_premium",
    "discount_premium_status",
    "fund_size",
    "management_fee",
    "custody_fee",
    "latest_amount",
    "computed_at",
    "data_start_date",
    "data_end_date",
    "benchmark_start_date",
    "benchmark_end_date",
    "failure_reason",
    "notes",
]

ETF_METRICS_COVERAGE_COLUMNS = [
    "metric_name",
    "total_count",
    "computable_count",
    "unable_count",
    "coverage_ratio",
    "main_failure_reason",
    "dependency",
    "importance",
    "notes",
]

METRIC_STATUS_VALUES = {
    "ok",
    "unable_to_compute",
    "missing_benchmark",
    "no_index_cache",
    "insufficient_overlap",
    "missing_etf_cache",
    "missing_required_columns",
    "source_unavailable",
    "not_applicable",
    "unknown",
}
MISSING_MARKERS = {"", "unknown", "missing", "unable_to_confirm", "nan", "none", "nat", "<na>"}
DEFAULT_WINDOWS = (20, 60, 120)


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if pd.isna(value):
        return False
    return str(value).strip().lower() not in MISSING_MARKERS


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _number_text(value: Any) -> Any:
    parsed = pd.to_numeric(value, errors="coerce")
    return "" if pd.isna(parsed) else round(float(parsed), 10)


def _read_csv(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kwargs).fillna("")


def _normalize_history(frame: pd.DataFrame, required: list[str]) -> tuple[pd.DataFrame, str]:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        return pd.DataFrame(), f"missing required columns: {', '.join(missing)}"
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["date", "close"]).drop_duplicates("date", keep="last").sort_values("date")
    if work.empty:
        return pd.DataFrame(), "no valid date/close rows"
    return work.reset_index(drop=True), ""


def build_etf_metric_universe(
    metadata: pd.DataFrame | None = None,
    index_map: pd.DataFrame | None = None,
    *,
    metadata_path: str | Path = "output/etf_metadata.csv",
    index_map_path: str | Path = "output/index_map.csv",
    symbols: str | list[str] | None = None,
    max_count: int | None = None,
) -> pd.DataFrame:
    if metadata is None:
        meta_path = Path(metadata_path)
        if meta_path.exists():
            metadata = _read_csv(meta_path, dtype={"symbol": str, "tracking_index_code": str})
        else:
            metadata = pd.DataFrame(columns=["symbol", "name", "category", "sub_category", "fund_size", "management_fee", "custody_fee", "latest_amount"])
    if index_map is None:
        map_path = Path(index_map_path)
        if map_path.exists():
            index_map = _read_csv(map_path, dtype={"symbol": str, "tracking_index_code": str})
        else:
            index_map = pd.DataFrame(columns=INDEX_MAP_COLUMNS)

    meta = metadata.copy().fillna("")
    if "symbol" not in meta.columns:
        meta["symbol"] = ""
    meta["symbol"] = meta["symbol"].astype(str).str.zfill(6)
    for column in ["name", "category", "sub_category", "fund_size", "management_fee", "custody_fee", "latest_amount"]:
        if column not in meta.columns:
            meta[column] = ""

    mapping = index_map.copy().fillna("")
    if not mapping.empty:
        mapping["symbol"] = mapping["symbol"].astype(str).str.zfill(6)
        keep = [
            "symbol",
            "tracking_index_name",
            "tracking_index_code",
            "mapping_method",
            "requires_manual_review",
            "usable_as_benchmark",
            "notes",
        ]
        for column in keep:
            if column not in mapping.columns:
                mapping[column] = ""
        mapping = mapping[keep].drop_duplicates("symbol", keep="first")
        frame = meta.merge(mapping, on="symbol", how="left", suffixes=("", "_map"))
        for column in ["tracking_index_name", "tracking_index_code"]:
            mapped_column = f"{column}_map"
            if mapped_column in frame.columns:
                frame[column] = frame[mapped_column].where(frame[mapped_column].astype(str).str.strip().ne(""), frame.get(column, ""))
                frame = frame.drop(columns=[mapped_column])
    else:
        frame = meta.copy()
        for column in ["tracking_index_name", "tracking_index_code", "mapping_method", "requires_manual_review", "usable_as_benchmark", "notes"]:
            if column not in frame.columns:
                frame[column] = ""

    if symbols:
        requested = [item.strip().zfill(6) for item in symbols.split(",")] if isinstance(symbols, str) else [str(item).zfill(6) for item in symbols]
        requested = [item for item in requested if item]
        frame = frame[frame["symbol"].isin(requested)].copy()
    if "usable_as_benchmark" not in frame.columns:
        frame["usable_as_benchmark"] = False
    frame["_usable_sort"] = frame["usable_as_benchmark"].map(_bool_value)
    frame = frame.sort_values(["_usable_sort", "symbol"], ascending=[False, True]).drop(columns=["_usable_sort"]).reset_index(drop=True)
    if max_count is not None and int(max_count) > 0:
        frame = frame.head(int(max_count)).copy()
    return frame.reset_index(drop=True)


def _load_etf_cache(symbol: str, cache_dir: str | Path) -> tuple[pd.DataFrame, str, str]:
    path = Path(cache_dir) / f"{str(symbol).zfill(6)}.csv"
    if not path.exists():
        return pd.DataFrame(), "missing_etf_cache", f"missing ETF cache: {path}"
    try:
        frame = _read_csv(path, dtype={"symbol": str})
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), "source_unavailable", str(exc)
    normalized, reason = _normalize_history(frame, ["date", "close"])
    if reason:
        status = "missing_required_columns" if reason.startswith("missing required columns") else "unable_to_compute"
        return pd.DataFrame(), status, reason
    return normalized, "ok", ""


def load_benchmark_for_etf(
    row: dict[str, Any] | pd.Series,
    *,
    index_cache_dir: str | Path = "data/index_cache",
) -> tuple[pd.DataFrame, str, str]:
    record = dict(row)
    code = str(record.get("tracking_index_code", "")).strip()
    usable = _bool_value(record.get("usable_as_benchmark", False))
    requires_review = _bool_value(record.get("requires_manual_review", False))
    method = str(record.get("mapping_method", "")).strip()
    if not _is_present(code) or not usable or requires_review or method == "name_inferred":
        return pd.DataFrame(), "missing_benchmark", "no confirmed benchmark mapping"
    path = Path(index_cache_dir) / f"{code}.csv"
    if not path.exists():
        return pd.DataFrame(), "no_index_cache", f"missing index cache: {path}"
    try:
        frame = _read_csv(path, dtype={"index_code": str})
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), "source_unavailable", str(exc)
    missing = [column for column in INDEX_CACHE_COLUMNS if column not in frame.columns]
    if missing:
        return pd.DataFrame(), "missing_required_columns", f"missing required columns: {', '.join(missing)}"
    normalized, reason = _normalize_history(frame, ["date", "close"])
    if reason:
        status = "missing_required_columns" if reason.startswith("missing required columns") else "unable_to_compute"
        return pd.DataFrame(), status, reason
    return normalized, "ok", ""


def _window_return(frame: pd.DataFrame, days: int, close_column: str = "close") -> Any:
    if frame is None or frame.empty or close_column not in frame.columns:
        return ""
    work = frame.dropna(subset=[close_column]).sort_values("date").reset_index(drop=True)
    if len(work) <= int(days):
        return ""
    start = pd.to_numeric(work[close_column].iloc[-int(days) - 1], errors="coerce")
    end = pd.to_numeric(work[close_column].iloc[-1], errors="coerce")
    if pd.isna(start) or pd.isna(end) or float(start) == 0:
        return ""
    return round(float(end) / float(start) - 1.0, 10)


def _aligned_close(etf_frame: pd.DataFrame, benchmark_frame: pd.DataFrame) -> pd.DataFrame:
    if etf_frame.empty or benchmark_frame.empty:
        return pd.DataFrame(columns=["date", "etf_close", "benchmark_close"])
    return (
        etf_frame[["date", "close"]]
        .rename(columns={"close": "etf_close"})
        .merge(benchmark_frame[["date", "close"]].rename(columns={"close": "benchmark_close"}), on="date", how="inner")
        .dropna(subset=["etf_close", "benchmark_close"])
        .sort_values("date")
        .reset_index(drop=True)
    )


def compute_tracking_error(
    etf_frame: pd.DataFrame,
    benchmark_frame: pd.DataFrame,
    *,
    min_overlap_days: int = 60,
    annualization_days: int = 252,
) -> dict[str, Any]:
    aligned = _aligned_close(etf_frame, benchmark_frame)
    returns = aligned[["etf_close", "benchmark_close"]].pct_change().dropna()
    overlap = int(len(returns))
    if overlap < int(min_overlap_days):
        return {"value": "", "status": "insufficient_overlap", "overlap_days": overlap}
    diff = returns["etf_close"] - returns["benchmark_close"]
    value = diff.std(ddof=1)
    if pd.isna(value):
        return {"value": "", "status": "unable_to_compute", "overlap_days": overlap}
    return {"value": round(float(value) * (annualization_days**0.5), 10), "status": "ok", "overlap_days": overlap}


def compute_relative_return(etf_frame: pd.DataFrame, benchmark_frame: pd.DataFrame, days: int) -> dict[str, Any]:
    aligned = _aligned_close(etf_frame, benchmark_frame)
    if len(aligned) <= int(days):
        return {"value": "", "status": "insufficient_overlap", "overlap_days": max(0, len(aligned) - 1)}
    etf_ret = _window_return(aligned.rename(columns={"etf_close": "close"}), days)
    bench_ret = _window_return(aligned.rename(columns={"benchmark_close": "close"}), days)
    if etf_ret == "" or bench_ret == "":
        return {"value": "", "status": "unable_to_compute", "overlap_days": max(0, len(aligned) - 1)}
    return {"value": round(float(etf_ret) - float(bench_ret), 10), "status": "ok", "overlap_days": max(0, len(aligned) - 1)}


def compute_benchmark_return(benchmark_frame: pd.DataFrame, days: int) -> dict[str, Any]:
    value = _window_return(benchmark_frame, days)
    if value == "":
        return {"value": "", "status": "insufficient_overlap"}
    return {"value": value, "status": "ok"}


def compute_discount_premium_placeholder(_row: dict[str, Any] | pd.Series | None = None) -> dict[str, Any]:
    return {
        "value": "",
        "status": "source_unavailable",
        "reason": "NAV/IOPV data is not available; price-only data cannot produce discount/premium",
    }


def _metric_row(
    record: dict[str, Any],
    *,
    etf_frame: pd.DataFrame,
    etf_status: str,
    etf_reason: str,
    benchmark_frame: pd.DataFrame,
    benchmark_status: str,
    benchmark_reason: str,
    min_overlap_days: int,
    computed_at: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {column: "" for column in ETF_METRICS_COLUMNS}
    row.update(
        {
            "symbol": str(record.get("symbol", "")).zfill(6),
            "name": str(record.get("name", record.get("etf_name", ""))),
            "category": str(record.get("category", "")),
            "sub_category": str(record.get("sub_category", "")),
            "tracking_index_code": str(record.get("tracking_index_code", "")),
            "tracking_index_name": str(record.get("tracking_index_name", "")),
            "benchmark_available": benchmark_status == "ok",
            "benchmark_status": benchmark_status,
            "fund_size": record.get("fund_size", ""),
            "management_fee": record.get("management_fee", ""),
            "custody_fee": record.get("custody_fee", ""),
            "latest_amount": record.get("latest_amount", ""),
            "computed_at": computed_at,
            "data_start_date": _date_text(etf_frame["date"].min()) if etf_status == "ok" else "",
            "data_end_date": _date_text(etf_frame["date"].max()) if etf_status == "ok" else "",
            "benchmark_start_date": _date_text(benchmark_frame["date"].min()) if benchmark_status == "ok" else "",
            "benchmark_end_date": _date_text(benchmark_frame["date"].max()) if benchmark_status == "ok" else "",
        }
    )
    for window in DEFAULT_WINDOWS:
        row[f"etf_return_{window}d"] = _number_text(_window_return(etf_frame, window)) if etf_status == "ok" else ""

    failure_reasons = [reason for reason in [etf_reason, benchmark_reason] if reason]
    if etf_status != "ok":
        row["tracking_error_status"] = etf_status
        row["metric_status"] = "unable_to_compute"
    elif benchmark_status != "ok":
        row["tracking_error_status"] = benchmark_status
        row["metric_status"] = "unable_to_compute"
    else:
        te = compute_tracking_error(etf_frame, benchmark_frame, min_overlap_days=min_overlap_days)
        row["tracking_error"] = _number_text(te["value"])
        row["tracking_error_status"] = str(te["status"])
        if te["status"] != "ok":
            failure_reasons.append(f"overlap_days={te.get('overlap_days', 0)} < min_overlap_days={min_overlap_days}")
        for window in DEFAULT_WINDOWS:
            bench_ret = compute_benchmark_return(benchmark_frame, window)
            rel_ret = compute_relative_return(etf_frame, benchmark_frame, window)
            row[f"benchmark_return_{window}d"] = _number_text(bench_ret["value"])
            row[f"relative_return_{window}d"] = _number_text(rel_ret["value"])
        row["metric_status"] = "ok" if row["tracking_error_status"] == "ok" else str(row["tracking_error_status"])

    discount = compute_discount_premium_placeholder(record)
    row["discount_premium"] = _number_text(discount["value"])
    row["discount_premium_status"] = str(discount["status"])
    if discount.get("reason"):
        failure_reasons.append(str(discount["reason"]))
    row["failure_reason"] = "; ".join(dict.fromkeys(failure_reasons))
    row["notes"] = "benchmark metrics require confirmed mapping and real index cache; ETF returns use ETF cache only"
    return {column: row.get(column, "") for column in ETF_METRICS_COLUMNS}


def _main_failure(frame: pd.DataFrame, status_column: str) -> str:
    if status_column not in frame.columns or frame.empty:
        return "unknown"
    statuses = frame[status_column].astype(str)
    failures = statuses[statuses.ne("ok") & statuses.ne("")]
    if failures.empty:
        return ""
    return str(failures.value_counts().idxmax())


def build_etf_metrics_coverage(metrics: pd.DataFrame) -> pd.DataFrame:
    total = int(len(metrics))
    rows: list[dict[str, Any]] = []

    def add(name: str, computable: int, reason_col: str, dependency: str, importance: str, notes: str) -> None:
        rows.append(
            {
                "metric_name": name,
                "total_count": total,
                "computable_count": int(computable),
                "unable_count": max(0, total - int(computable)),
                "coverage_ratio": 0.0 if total == 0 else round(int(computable) / total, 4),
                "main_failure_reason": _main_failure(metrics, reason_col),
                "dependency": dependency,
                "importance": importance,
                "notes": notes,
            }
        )

    add("tracking_error", int(metrics.get("tracking_error_status", pd.Series(dtype=str)).astype(str).eq("ok").sum()), "tracking_error_status", "ETF cache + confirmed benchmark index cache", "P1", "annualized standard deviation of ETF minus benchmark daily returns")
    for window in DEFAULT_WINDOWS:
        add(f"relative_return_{window}d", int(metrics.get(f"relative_return_{window}d", pd.Series(dtype=str)).astype(str).str.strip().ne("").sum()), "benchmark_status", "ETF cache + confirmed benchmark index cache", "P1", "ETF return minus benchmark return over the window")
        add(f"benchmark_return_{window}d", int(metrics.get(f"benchmark_return_{window}d", pd.Series(dtype=str)).astype(str).str.strip().ne("").sum()), "benchmark_status", "confirmed benchmark index cache", "P1", "benchmark return over the window")
        add(f"etf_return_{window}d", int(metrics.get(f"etf_return_{window}d", pd.Series(dtype=str)).astype(str).str.strip().ne("").sum()), "metric_status", "ETF cache", "P2", "standalone ETF return; not a benchmark-relative metric")
    add("discount_premium", int(metrics.get("discount_premium_status", pd.Series(dtype=str)).astype(str).eq("ok").sum()), "discount_premium_status", "NAV or IOPV source", "P2", "unavailable until NAV/IOPV data exists")
    return pd.DataFrame(rows, columns=ETF_METRICS_COVERAGE_COLUMNS)


def compute_etf_metrics(
    *,
    metadata_path: str | Path = "output/etf_metadata.csv",
    index_map_path: str | Path = "output/index_map.csv",
    index_coverage_path: str | Path = "output/index_data_coverage.csv",
    etf_cache_dir: str | Path = "data/cache",
    index_cache_dir: str | Path = "data/index_cache",
    symbols: str | list[str] | None = None,
    max_count: int | None = None,
    min_overlap_days: int = 60,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    universe = build_etf_metric_universe(
        metadata_path=metadata_path,
        index_map_path=index_map_path,
        symbols=symbols,
        max_count=max_count,
    )
    computed_at = _now_text()
    rows: list[dict[str, Any]] = []
    _ = Path(index_coverage_path)  # Kept as an explicit dependency for the public interface.
    for record in universe.to_dict("records"):
        etf_frame, etf_status, etf_reason = _load_etf_cache(str(record.get("symbol", "")), etf_cache_dir)
        benchmark_frame, benchmark_status, benchmark_reason = load_benchmark_for_etf(record, index_cache_dir=index_cache_dir)
        rows.append(
            _metric_row(
                record,
                etf_frame=etf_frame,
                etf_status=etf_status,
                etf_reason=etf_reason,
                benchmark_frame=benchmark_frame,
                benchmark_status=benchmark_status,
                benchmark_reason=benchmark_reason,
                min_overlap_days=min_overlap_days,
                computed_at=computed_at,
            )
        )
    metrics = pd.DataFrame(rows, columns=ETF_METRICS_COLUMNS)
    coverage = build_etf_metrics_coverage(metrics)
    return metrics, coverage


def write_etf_metrics_report(
    metrics: pd.DataFrame,
    coverage: pd.DataFrame | None = None,
    *,
    metrics_path: str | Path = "output/etf_metrics.csv",
    coverage_path: str | Path = "output/etf_metrics_coverage.csv",
) -> tuple[Path, Path]:
    metric_output = Path(metrics_path)
    coverage_output = Path(coverage_path)
    metric_output.parent.mkdir(parents=True, exist_ok=True)
    coverage_output.parent.mkdir(parents=True, exist_ok=True)
    metrics[ETF_METRICS_COLUMNS].to_csv(metric_output, index=False, encoding="utf-8-sig")
    cov = coverage if coverage is not None else build_etf_metrics_coverage(metrics)
    cov[ETF_METRICS_COVERAGE_COLUMNS].to_csv(coverage_output, index=False, encoding="utf-8-sig")
    return metric_output, coverage_output


def summarize_etf_metrics(
    *,
    metrics_path: str | Path = "output/etf_metrics.csv",
    coverage_path: str | Path = "output/etf_metrics_coverage.csv",
    example_limit: int = 10,
) -> dict[str, Any]:
    metric_path = Path(metrics_path)
    cov_path = Path(coverage_path)
    empty = {
        "status": "not_run",
        "etf_metrics_report": str(metric_path),
        "etf_metrics_coverage_report": str(cov_path),
        "total_etfs": 0,
        "metrics_computable_count": 0,
        "tracking_error_computable_count": 0,
        "relative_return_computable_count": 0,
        "discount_premium_available_count": 0,
        "no_index_cache_count": 0,
        "missing_benchmark_count": 0,
        "insufficient_overlap_count": 0,
        "source_unavailable_count": 0,
        "top_examples": [],
    }
    if not metric_path.exists() or not cov_path.exists():
        return empty
    metrics = _read_csv(metric_path, dtype={"symbol": str, "tracking_index_code": str})
    if metrics.empty:
        return empty | {"status": "ok"}
    relative_cols = [column for column in metrics.columns if column.startswith("relative_return_")]
    examples = metrics[["symbol", "name", "benchmark_status", "tracking_error_status", "failure_reason"]].head(example_limit).to_dict("records")
    return {
        "status": "ok",
        "etf_metrics_report": str(metric_path),
        "etf_metrics_coverage_report": str(cov_path),
        "total_etfs": int(len(metrics)),
        "metrics_computable_count": int(metrics["metric_status"].astype(str).eq("ok").sum()),
        "tracking_error_computable_count": int(metrics["tracking_error_status"].astype(str).eq("ok").sum()),
        "relative_return_computable_count": int(metrics[relative_cols].astype(str).apply(lambda row: row.str.strip().ne("").any(), axis=1).sum()) if relative_cols else 0,
        "discount_premium_available_count": int(metrics["discount_premium_status"].astype(str).eq("ok").sum()),
        "no_index_cache_count": int(metrics["benchmark_status"].astype(str).eq("no_index_cache").sum()),
        "missing_benchmark_count": int(metrics["benchmark_status"].astype(str).eq("missing_benchmark").sum()),
        "insufficient_overlap_count": int(metrics["tracking_error_status"].astype(str).eq("insufficient_overlap").sum()),
        "source_unavailable_count": int(
            metrics["benchmark_status"].astype(str).eq("source_unavailable").sum()
            + metrics["discount_premium_status"].astype(str).eq("source_unavailable").sum()
        ),
        "top_examples": examples,
    }
