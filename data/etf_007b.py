from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ETF_007B_METRICS_REPORT_COLUMNS = [
    "symbol",
    "name",
    "tracking_index_code",
    "tracking_index_name",
    "benchmark_available",
    "benchmark_status",
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
    "overlap_days",
    "data_start_date",
    "data_end_date",
    "benchmark_start_date",
    "benchmark_end_date",
    "computation_status",
    "validation_status",
    "failure_reason",
    "notes",
]

ETF_007B_METRICS_SUMMARY_COLUMNS = [
    "summary_item",
    "count",
    "severity",
    "finding",
    "suggested_action",
    "examples",
    "notes",
]

ETF_007B_VALIDATION_STATUSES = {
    "computed_valid",
    "no_index_cache",
    "missing_benchmark",
    "insufficient_overlap",
    "schema_invalid",
    "source_unavailable",
    "unknown",
}

RELATIVE_RETURN_COLUMNS = ["relative_return_20d", "relative_return_60d", "relative_return_120d"]
BENCHMARK_RETURN_COLUMNS = ["benchmark_return_20d", "benchmark_return_60d", "benchmark_return_120d"]


def _read_csv(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    return pd.read_csv(file_path, encoding="utf-8-sig", **kwargs).fillna("")


def _read_json(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int(value: Any, default: int = 0) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return int(float(parsed))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _has_value(value: Any) -> bool:
    return _text(value) != ""


def _all_present(row: pd.Series | dict[str, Any], columns: list[str]) -> bool:
    return all(_has_value(dict(row).get(column, "")) for column in columns)


def _overlap_days(symbol: str, index_code: str, *, etf_cache_dir: str | Path, index_cache_dir: str | Path) -> str:
    etf_path = Path(etf_cache_dir) / f"{str(symbol).zfill(6)}.csv"
    index_path = Path(index_cache_dir) / f"{str(index_code).strip()}.csv"
    if not etf_path.exists() or not index_path.exists():
        return ""
    try:
        etf = pd.read_csv(etf_path, usecols=["date"], encoding="utf-8-sig")
        benchmark = pd.read_csv(index_path, usecols=["date"], encoding="utf-8-sig")
    except Exception:  # noqa: BLE001
        return ""
    etf_dates = pd.to_datetime(etf["date"], errors="coerce").dropna().dt.normalize()
    benchmark_dates = pd.to_datetime(benchmark["date"], errors="coerce").dropna().dt.normalize()
    overlap = len(set(etf_dates).intersection(set(benchmark_dates)))
    return str(max(0, overlap - 1)) if overlap else ""


def classify_007b_metric_status(row: pd.Series | dict[str, Any]) -> str:
    record = dict(row)
    benchmark_status = _text(record.get("benchmark_status")).lower()
    tracking_status = _text(record.get("tracking_error_status")).lower()
    benchmark_available = _bool(record.get("benchmark_available", False))
    tracking_valid = tracking_status == "ok" and _has_value(record.get("tracking_error"))
    relative_valid = _all_present(record, RELATIVE_RETURN_COLUMNS) and _all_present(record, BENCHMARK_RETURN_COLUMNS)

    if benchmark_available and benchmark_status == "ok" and tracking_valid and relative_valid:
        return "computed_valid"
    if benchmark_status == "no_index_cache" or tracking_status == "no_index_cache":
        return "no_index_cache"
    if benchmark_status == "missing_benchmark" or tracking_status == "missing_benchmark":
        return "missing_benchmark"
    if tracking_status == "insufficient_overlap":
        return "insufficient_overlap"
    if benchmark_status == "missing_required_columns" or tracking_status == "missing_required_columns":
        return "schema_invalid"
    if benchmark_status in {"source_unavailable", "missing_etf_cache"} or tracking_status in {"source_unavailable", "missing_etf_cache"}:
        return "source_unavailable"
    return "unknown"


def build_007b_small_scope_report(
    *,
    output_dir: str | Path = "output",
    etf_cache_dir: str | Path = "data/cache",
    index_cache_dir: str | Path = "data/index_cache",
    etf_metrics: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    metrics = etf_metrics if etf_metrics is not None else _read_csv(output_path / "etf_metrics.csv", dtype={"symbol": str, "tracking_index_code": str})
    rows: list[dict[str, Any]] = []
    for _, item in metrics.iterrows():
        status = classify_007b_metric_status(item)
        symbol = str(item.get("symbol", "")).zfill(6)
        index_code = _text(item.get("tracking_index_code"))
        rows.append(
            {
                "symbol": symbol,
                "name": _text(item.get("name")),
                "tracking_index_code": index_code,
                "tracking_index_name": _text(item.get("tracking_index_name")),
                "benchmark_available": _bool(item.get("benchmark_available", False)),
                "benchmark_status": _text(item.get("benchmark_status")),
                "tracking_error": _text(item.get("tracking_error")) if status == "computed_valid" else "",
                "tracking_error_status": _text(item.get("tracking_error_status")),
                "relative_return_20d": _text(item.get("relative_return_20d")) if status == "computed_valid" else "",
                "relative_return_60d": _text(item.get("relative_return_60d")) if status == "computed_valid" else "",
                "relative_return_120d": _text(item.get("relative_return_120d")) if status == "computed_valid" else "",
                "benchmark_return_20d": _text(item.get("benchmark_return_20d")) if status == "computed_valid" else "",
                "benchmark_return_60d": _text(item.get("benchmark_return_60d")) if status == "computed_valid" else "",
                "benchmark_return_120d": _text(item.get("benchmark_return_120d")) if status == "computed_valid" else "",
                "etf_return_20d": _text(item.get("etf_return_20d")),
                "etf_return_60d": _text(item.get("etf_return_60d")),
                "etf_return_120d": _text(item.get("etf_return_120d")),
                "overlap_days": _overlap_days(symbol, index_code, etf_cache_dir=etf_cache_dir, index_cache_dir=index_cache_dir) if status == "computed_valid" else "",
                "data_start_date": _text(item.get("data_start_date")),
                "data_end_date": _text(item.get("data_end_date")),
                "benchmark_start_date": _text(item.get("benchmark_start_date")) if status == "computed_valid" else "",
                "benchmark_end_date": _text(item.get("benchmark_end_date")) if status == "computed_valid" else "",
                "computation_status": "computed_valid" if status == "computed_valid" else "not_computable",
                "validation_status": status,
                "failure_reason": "" if status == "computed_valid" else _text(item.get("failure_reason")),
                "notes": (
                    "small_scope_007b_only; research_report_only; not connected to factor_score, candidate_gate, strategy, backtest, UI, or compare_signal"
                    if status == "computed_valid"
                    else "not in small-scope 007B; do not fill fake benchmark metrics"
                ),
            }
        )
    return rows


def build_007b_small_scope_summary(
    rows: list[dict[str, Any]] | pd.DataFrame,
    *,
    readiness_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return [
            {
                "summary_item": "total_etfs",
                "count": 0,
                "severity": "high",
                "finding": "No ETF 007B rows exist.",
                "suggested_action": "run compute-etf-metrics, then validate-etf-007b-metrics",
                "examples": "",
                "notes": "small-scope 007B metrics not run",
            }
        ]
    computed = frame[frame["validation_status"].astype(str).eq("computed_valid")]
    no_index_cache = frame[frame["validation_status"].astype(str).eq("no_index_cache")]
    missing_benchmark = frame[frame["validation_status"].astype(str).eq("missing_benchmark")]
    insufficient_overlap = frame[frame["validation_status"].astype(str).eq("insufficient_overlap")]
    scope = str((readiness_summary or {}).get("allowed_to_enter_007b_scope", "small_scope" if not computed.empty else "blocked"))
    full_scope_available = bool((readiness_summary or {}).get("full_scope_available", False))

    def examples(subset: pd.DataFrame) -> str:
        return ";".join(subset["symbol"].astype(str).head(12).tolist()) if not subset.empty else ""

    return [
        {
            "summary_item": "total_etfs",
            "count": int(len(frame)),
            "severity": "info",
            "finding": f"{len(frame)} ETF rows were reviewed for 007B small-scope metrics.",
            "suggested_action": "use validation_status to separate computed rows from guarded unavailable rows",
            "examples": examples(frame),
            "notes": "report reads existing etf_metrics only; it does not refresh cache",
        },
        {
            "summary_item": "computed_valid_count",
            "count": int(len(computed)),
            "severity": "info" if not computed.empty else "high",
            "finding": f"{len(computed)} ETF row(s) have real tracking_error and real relative_return windows.",
            "suggested_action": "limit 007B validation to these rows only",
            "examples": examples(computed),
            "notes": "computed_valid is small-scope evidence, not full-market evidence",
        },
        {
            "summary_item": "tracking_error_valid_count",
            "count": int(frame["tracking_error_status"].astype(str).eq("ok").sum()),
            "severity": "info",
            "finding": "Rows with tracking_error_status=ok.",
            "suggested_action": "do not use rows without tracking_error_status=ok for 007B TE validation",
            "examples": examples(frame[frame["tracking_error_status"].astype(str).eq("ok")]),
            "notes": "must be backed by confirmed benchmark cache",
        },
        {
            "summary_item": "relative_return_valid_count",
            "count": int(frame[RELATIVE_RETURN_COLUMNS].astype(str).apply(lambda row: row.str.strip().ne("").all(), axis=1).sum()),
            "severity": "info",
            "finding": "Rows with all 20d/60d/120d relative returns present.",
            "suggested_action": "use only rows whose relative returns are backed by benchmark_return fields",
            "examples": examples(frame[frame[RELATIVE_RETURN_COLUMNS].astype(str).apply(lambda row: row.str.strip().ne("").all(), axis=1)]),
            "notes": "ETF standalone returns are not relative returns",
        },
        {
            "summary_item": "no_index_cache_count",
            "count": int(len(no_index_cache)),
            "severity": "warning" if not no_index_cache.empty else "info",
            "finding": f"{len(no_index_cache)} ETF row(s) still have confirmed mappings but no usable index cache.",
            "suggested_action": "keep these out of small-scope 007B until schema-valid index cache exists",
            "examples": examples(no_index_cache),
            "notes": "blocks full-scope 007B",
        },
        {
            "summary_item": "missing_benchmark_count",
            "count": int(len(missing_benchmark)),
            "severity": "warning" if not missing_benchmark.empty else "info",
            "finding": f"{len(missing_benchmark)} ETF row(s) still lack confirmed usable benchmark mappings.",
            "suggested_action": "do not fabricate benchmark metrics; confirm mappings first",
            "examples": examples(missing_benchmark),
            "notes": "blocks full-scope 007B",
        },
        {
            "summary_item": "insufficient_overlap_count",
            "count": int(len(insufficient_overlap)),
            "severity": "warning" if not insufficient_overlap.empty else "info",
            "finding": f"{len(insufficient_overlap)} ETF row(s) lack sufficient ETF/index overlap.",
            "suggested_action": "wait for enough overlapping history or review cache coverage",
            "examples": examples(insufficient_overlap),
            "notes": "no zero fill for missing metric windows",
        },
        {
            "summary_item": "scope",
            "count": int(len(computed)),
            "severity": "info" if scope == "small_scope" else "high" if scope == "blocked" else "info",
            "finding": f"007B metric scope is {scope}; full_scope_available={full_scope_available}.",
            "suggested_action": "do not promote small-scope findings to full-scope results",
            "examples": examples(computed),
            "notes": "full-scope requires every benchmark dependency to clear",
        },
    ]


def write_007b_small_scope_report(
    rows: list[dict[str, Any]],
    *,
    report_path: str | Path = "output/etf_007b_metrics_report.csv",
    summary_path: str | Path = "output/etf_007b_metrics_summary.csv",
    readiness_summary: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    report = Path(report_path)
    summary = Path(summary_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=ETF_007B_METRICS_REPORT_COLUMNS).to_csv(report, index=False, encoding="utf-8-sig")
    pd.DataFrame(
        build_007b_small_scope_summary(rows, readiness_summary=readiness_summary),
        columns=ETF_007B_METRICS_SUMMARY_COLUMNS,
    ).to_csv(summary, index=False, encoding="utf-8-sig")
    return report, summary


def summarize_007b_small_scope(
    rows: list[dict[str, Any]] | pd.DataFrame | None = None,
    *,
    report_path: str | Path = "output/etf_007b_metrics_report.csv",
    readiness_summary: dict[str, Any] | None = None,
    example_limit: int = 10,
) -> dict[str, Any]:
    frame = pd.DataFrame(rows) if rows is not None else _read_csv(report_path, dtype={"symbol": str, "tracking_index_code": str})
    readiness = readiness_summary or {}
    empty = {
        "etf_007b_metrics_report": str(report_path),
        "etf_007b_metrics_summary_report": str(Path(report_path).with_name("etf_007b_metrics_summary.csv")),
        "status": "not_run",
        "total_etfs": 0,
        "computed_valid_count": 0,
        "tracking_error_valid_count": 0,
        "relative_return_valid_count": 0,
        "no_index_cache_count": 0,
        "missing_benchmark_count": 0,
        "insufficient_overlap_count": 0,
        "scope": str(readiness.get("allowed_to_enter_007b_scope", "blocked")),
        "full_scope_available": bool(readiness.get("full_scope_available", False)),
        "top_examples": [],
    }
    if frame.empty:
        return empty
    relative_valid = frame[RELATIVE_RETURN_COLUMNS].astype(str).apply(lambda row: row.str.strip().ne("").all(), axis=1)
    computed = frame[frame["validation_status"].astype(str).eq("computed_valid")]
    scope = str(readiness.get("allowed_to_enter_007b_scope", "small_scope" if not computed.empty else "blocked"))
    full_scope = bool(readiness.get("full_scope_available", False))
    if not readiness and (not computed.empty and len(computed) == len(frame)):
        scope = "full_scope"
        full_scope = True
    return {
        "etf_007b_metrics_report": str(report_path),
        "etf_007b_metrics_summary_report": str(Path(report_path).with_name("etf_007b_metrics_summary.csv")),
        "status": "ok",
        "total_etfs": int(len(frame)),
        "computed_valid_count": int(len(computed)),
        "tracking_error_valid_count": int(frame["tracking_error_status"].astype(str).eq("ok").sum()),
        "relative_return_valid_count": int(relative_valid.sum()),
        "no_index_cache_count": int(frame["validation_status"].astype(str).eq("no_index_cache").sum()),
        "missing_benchmark_count": int(frame["validation_status"].astype(str).eq("missing_benchmark").sum()),
        "insufficient_overlap_count": int(frame["validation_status"].astype(str).eq("insufficient_overlap").sum()),
        "scope": scope,
        "full_scope_available": full_scope,
        "top_examples": computed[
            ["symbol", "name", "tracking_index_code", "tracking_index_name", "tracking_error", "relative_return_20d", "relative_return_60d", "relative_return_120d"]
        ].head(example_limit).to_dict("records"),
    }


def merge_007b_small_scope_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    summary: dict[str, Any] | None = None,
) -> bool:
    path = Path(qa_report_path)
    if not path.exists():
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    output_dir = path.parent
    readiness = report.get("data_layer", {}).get("index_007b_readiness", {})
    etf_007b_summary = summary or summarize_007b_small_scope(
        report_path=output_dir / "etf_007b_metrics_report.csv",
        readiness_summary=readiness if isinstance(readiness, dict) else None,
    )
    data_layer = report.setdefault("data_layer", {})
    data_layer["etf_007b_metrics"] = etf_007b_summary
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
