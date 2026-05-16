from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from data.quality import RETURN_WARNING_THRESHOLD
from data.storage import CACHE_META_DIR, DATA_DIR, get_cache_metadata_path, get_csv_path, load_cache_metadata


AUDIT_COLUMNS = [
    "symbol",
    "name",
    "source",
    "adjust",
    "download_method",
    "fallback_used",
    "cache_file",
    "start_date",
    "end_date",
    "row_count",
    "abnormal_return_count",
    "max_abs_return",
    "max_return_date",
    "possible_adjustment_issue",
    "audit_status",
    "audit_reason",
]

CACHE_METADATA_AUDIT_COLUMNS = [
    "symbol",
    "name",
    "cache_file",
    "metadata_file",
    "metadata_exists",
    "source",
    "adjust",
    "api_name",
    "download_method",
    "fallback_used",
    "downloaded_at",
    "row_count",
    "status",
    "reason",
]

KNOWN_AKSHARE_SOURCES = {
    "akshare.fund_etf_hist_sina",
    "akshare.fund_etf_hist_em.qfq",
    "akshare.fund_etf_hist_em.none",
    "akshare.fund_etf_hist_em.hfq",
}


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _unique_text(values: pd.Series | list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def infer_adjust_from_source(source: str) -> str:
    text = _text(source).lower()
    if not text:
        return ""
    if "qfq" in text or "adjust=qfq" in text:
        return "qfq"
    if "hfq" in text or "adjust=hfq" in text:
        return "hfq"
    if ".none" in text or "adjust=none" in text or 'adjust=""' in text or "adjust=''" in text or "adjust=)" in text:
        return "none"
    if "fund_etf_hist_sina" in text:
        return "unknown"
    if text == "local_cache":
        return "unknown"
    return ""


def infer_download_method(source: str) -> str:
    text = _text(source).lower()
    if not text:
        return "missing_source"
    if "fund_etf_hist_sina" in text:
        return "akshare_sina"
    if "fund_etf_hist_em" in text and "qfq" in text:
        return "akshare_em_chunked_qfq"
    if "fund_etf_hist_em" in text and ("none" in text or "adjust=none" in text or 'adjust=""' in text or "adjust=''" in text or "adjust=)" in text):
        return "akshare_em_chunked_none"
    if "fund_etf_hist_em" in text and "hfq" in text:
        return "akshare_em_chunked_hfq"
    if text == "local_cache":
        return "local_cache"
    return "unknown"


def _is_fallback_source(source: str) -> bool:
    method = infer_download_method(source)
    return method in {"akshare_em_chunked_qfq", "akshare_em_chunked_none", "akshare_em_chunked_hfq"}


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _safe_metadata(symbol: str, meta_dir: str | Path = CACHE_META_DIR) -> tuple[dict[str, Any] | None, str]:
    try:
        return load_cache_metadata(symbol, Path(meta_dir)), ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _return_stats(frame: pd.DataFrame) -> dict[str, Any]:
    if "date" not in frame.columns or "close" not in frame.columns:
        return {
            "abnormal_return_count": 0,
            "max_abs_return": "",
            "max_return_date": "",
        }
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    returns = work.sort_values("date")["close"].pct_change()
    abs_returns = returns.abs()
    abnormal_count = int((abs_returns > RETURN_WARNING_THRESHOLD).sum())
    if abs_returns.dropna().empty:
        return {
            "abnormal_return_count": abnormal_count,
            "max_abs_return": "",
            "max_return_date": "",
        }
    idx = abs_returns.idxmax()
    return {
        "abnormal_return_count": abnormal_count,
        "max_abs_return": float(abs_returns.loc[idx]),
        "max_return_date": _date_text(work.loc[idx, "date"]),
    }


def _quality_abnormal_counts(quality_rows: list[dict[str, Any]] | None, output_dir: str | Path) -> dict[str, int]:
    rows = quality_rows
    if rows is None:
        path = Path(output_dir) / "data_quality_report.csv"
        if not path.exists():
            return {}
        try:
            rows = pd.read_csv(path, dtype={"symbol": str}).fillna("").to_dict("records")
        except Exception:
            return {}
    result: dict[str, int] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).zfill(6)
        text = f"{row.get('warnings', '')}; {row.get('errors', '')}; {row.get('failure_types', '')}"
        if "abnormal_return" in text or "daily close return exceeds" in text:
            result[symbol] = result.get(symbol, 0) + 1
    return result


def _status_and_reason(
    *,
    source_values: list[str],
    adjust_values: list[str],
    fallback_used: bool,
    abnormal_return_count: int,
    possible_adjustment_issue: bool,
    frame_loaded: bool,
) -> tuple[str, str]:
    if not frame_loaded:
        return "unknown", "cache file missing or unreadable"
    if not source_values:
        return "error_missing_adjustment", "missing source metadata; adjust cannot be traced"
    if len(adjust_values) > 1:
        return "error_mixed_adjustment", f"mixed adjustment values: {', '.join(adjust_values)}"
    adjust = adjust_values[0] if adjust_values else ""
    if not adjust:
        return "error_missing_adjustment", "source is present but adjustment metadata cannot be inferred"
    if abnormal_return_count > 0:
        if possible_adjustment_issue:
            return "warning_abnormal_return", "abnormal close return may be related to adjustment, dividend, split, fallback, or unadjusted source"
        return "warning_abnormal_return", "abnormal close return detected; manually verify dividend, split, or market event"
    if fallback_used:
        return "warning_fallback_used", "download used a fallback source after the preferred source path"
    if adjust == "unknown":
        return "warning_unknown_adjustment", "source does not expose an explicit qfq/hfq/none adjustment label"
    if adjust in {"qfq", "hfq", "none"}:
        return "ok", f"single adjustment path recorded: {adjust}"
    return "unknown", f"unrecognized adjustment path: {adjust}"


def audit_adjustment_frame(
    symbol: str,
    name: str,
    frame: pd.DataFrame | None,
    cache_file: str | Path = "",
    source_hint: str = "",
    quality_abnormal_count: int = 0,
    metadata: dict[str, Any] | None = None,
    metadata_checked: bool = False,
) -> dict[str, Any]:
    if frame is None:
        return {
            "symbol": str(symbol).zfill(6),
            "name": name,
            "source": source_hint,
            "adjust": "",
            "download_method": "missing_cache",
            "fallback_used": False,
            "cache_file": str(cache_file),
            "start_date": "",
            "end_date": "",
            "row_count": 0,
            "abnormal_return_count": int(quality_abnormal_count),
            "max_abs_return": "",
            "max_return_date": "",
            "possible_adjustment_issue": bool(quality_abnormal_count),
            "audit_status": "unknown",
            "audit_reason": "cache file missing or unreadable",
        }

    work = frame.copy()
    if "date" not in work.columns:
        work = work.reset_index()
    source_values = _unique_text(work["source"]) if "source" in work.columns else []
    if not source_values and source_hint:
        source_values = [source_hint]
    stats = _return_stats(work)
    abnormal_return_count = max(int(stats["abnormal_return_count"]), int(quality_abnormal_count))
    dates = pd.to_datetime(work["date"], errors="coerce") if "date" in work.columns else pd.Series(dtype="datetime64[ns]")

    if metadata_checked and metadata is None:
        return {
            "symbol": str(symbol).zfill(6),
            "name": name,
            "source": ";".join(source_values),
            "adjust": "unknown",
            "download_method": "legacy_cache",
            "fallback_used": False,
            "cache_file": str(cache_file),
            "start_date": _date_text(dates.min()) if not dates.empty else "",
            "end_date": _date_text(dates.max()) if not dates.empty else "",
            "row_count": int(len(work)),
            "abnormal_return_count": abnormal_return_count,
            "max_abs_return": stats["max_abs_return"],
            "max_return_date": stats["max_return_date"],
            "possible_adjustment_issue": bool(abnormal_return_count > 0),
            "audit_status": "warning_unknown_adjustment",
            "audit_reason": "legacy cache without metadata; adjustment cannot be confirmed",
        }

    if metadata:
        source_values = _unique_text([metadata.get("source", "")])
        adjust_values = _unique_text([metadata.get("adjust", "")])
        methods = _unique_text([metadata.get("download_method", "")]) or [infer_download_method(source_values[0] if source_values else "")]
        fallback_used = _bool_value(metadata.get("fallback_used", False))
        possible_adjustment_issue = bool(
            abnormal_return_count > 0
            and (fallback_used or not adjust_values or any(item in {"unknown", "none"} for item in adjust_values) or len(adjust_values) > 1)
        )
        status, reason = _status_and_reason(
            source_values=source_values,
            adjust_values=adjust_values,
            fallback_used=fallback_used,
            abnormal_return_count=abnormal_return_count,
            possible_adjustment_issue=possible_adjustment_issue,
            frame_loaded=True,
        )
        return {
            "symbol": str(symbol).zfill(6),
            "name": name,
            "source": ";".join(source_values),
            "adjust": ";".join(adjust_values),
            "download_method": ";".join(dict.fromkeys(methods)),
            "fallback_used": bool(fallback_used),
            "cache_file": str(cache_file),
            "start_date": _date_text(dates.min()) if not dates.empty else "",
            "end_date": _date_text(dates.max()) if not dates.empty else "",
            "row_count": int(len(work)),
            "abnormal_return_count": abnormal_return_count,
            "max_abs_return": stats["max_abs_return"],
            "max_return_date": stats["max_return_date"],
            "possible_adjustment_issue": bool(possible_adjustment_issue),
            "audit_status": status,
            "audit_reason": reason,
        }

    if "adjust" in work.columns:
        explicit_adjusts = _unique_text(work["adjust"])
    else:
        explicit_adjusts = []
    inferred_adjusts = [infer_adjust_from_source(source) for source in source_values]
    inferred_adjusts = [item for item in inferred_adjusts if item]
    adjust_values = explicit_adjusts or inferred_adjusts
    adjust_values = list(dict.fromkeys(adjust_values))

    methods = [infer_download_method(source) for source in source_values] or ["missing_source"]
    fallback_used = any(_is_fallback_source(source) for source in source_values)
    fallback_used = fallback_used or len(set(methods)) > 1
    adjust_text = ";".join(adjust_values)
    possible_adjustment_issue = bool(
        abnormal_return_count > 0
        and (fallback_used or not adjust_values or any(item in {"unknown", "none"} for item in adjust_values) or len(adjust_values) > 1)
    )
    status, reason = _status_and_reason(
        source_values=source_values,
        adjust_values=adjust_values,
        fallback_used=fallback_used,
        abnormal_return_count=abnormal_return_count,
        possible_adjustment_issue=possible_adjustment_issue,
        frame_loaded=True,
    )
    return {
        "symbol": str(symbol).zfill(6),
        "name": name,
        "source": ";".join(source_values),
        "adjust": adjust_text,
        "download_method": ";".join(dict.fromkeys(methods)),
        "fallback_used": bool(fallback_used),
        "cache_file": str(cache_file),
        "start_date": _date_text(dates.min()) if not dates.empty else "",
        "end_date": _date_text(dates.max()) if not dates.empty else "",
        "row_count": int(len(work)),
        "abnormal_return_count": abnormal_return_count,
        "max_abs_return": stats["max_abs_return"],
        "max_return_date": stats["max_return_date"],
        "possible_adjustment_issue": bool(possible_adjustment_issue),
        "audit_status": status,
        "audit_reason": reason,
    }


def build_adjustment_audit(
    etf_pool: list[dict[str, str]],
    output_dir: str | Path = "output",
    cache_dir: str | Path = DATA_DIR,
    quality_rows: list[dict[str, Any]] | None = None,
    coverage_rows: list[dict[str, Any]] | None = None,
    cache_meta_dir: str | Path = CACHE_META_DIR,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    cache_path = Path(cache_dir)
    quality_abnormal = _quality_abnormal_counts(quality_rows, output_path)
    coverage_by_symbol: dict[str, dict[str, Any]] = {}
    if coverage_rows is None and (output_path / "data_coverage_report.csv").exists():
        try:
            coverage_rows = pd.read_csv(output_path / "data_coverage_report.csv", dtype={"symbol": str}).fillna("").to_dict("records")
        except Exception:
            coverage_rows = []
    for row in coverage_rows or []:
        symbol = str(row.get("symbol", "")).zfill(6)
        if symbol:
            coverage_by_symbol[symbol] = row

    rows: list[dict[str, Any]] = []
    for etf in etf_pool:
        symbol = str(etf["symbol"]).zfill(6)
        name = str(etf.get("name", ""))
        cache_file = get_csv_path(symbol, cache_path)
        source_hint = _text(coverage_by_symbol.get(symbol, {}).get("source", ""))
        try:
            frame = pd.read_csv(cache_file, dtype={"symbol": str}) if cache_file.exists() else None
        except Exception:
            frame = None
        metadata, metadata_error = _safe_metadata(symbol, cache_meta_dir)
        rows.append(
            audit_adjustment_frame(
                symbol=symbol,
                name=name,
                frame=frame,
                cache_file=cache_file,
                source_hint=source_hint,
                quality_abnormal_count=quality_abnormal.get(symbol, 0),
                metadata=metadata,
                metadata_checked=not metadata_error,
            )
        )

    output_path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=AUDIT_COLUMNS).to_csv(output_path / "adjustment_audit.csv", index=False, encoding="utf-8-sig")
    return rows


def audit_cache_metadata(
    etf_pool: list[dict[str, str]],
    output_dir: str | Path = "output",
    cache_dir: str | Path = DATA_DIR,
    cache_meta_dir: str | Path = CACHE_META_DIR,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cache_path = Path(cache_dir)
    meta_path = Path(cache_meta_dir)
    for etf in etf_pool:
        symbol = str(etf["symbol"]).zfill(6)
        name = str(etf.get("name", ""))
        cache_file = get_csv_path(symbol, cache_path)
        metadata_file = get_cache_metadata_path(symbol, meta_path)
        metadata_exists = metadata_file.exists()
        cache_exists = cache_file.exists()
        row_count = 0
        if cache_exists:
            try:
                row_count = int(len(pd.read_csv(cache_file, dtype={"symbol": str})))
            except Exception:
                row_count = 0

        metadata: dict[str, Any] = {}
        read_error = ""
        if metadata_exists:
            try:
                metadata = load_cache_metadata(symbol, meta_path) or {}
            except Exception as exc:  # noqa: BLE001
                read_error = str(exc)

        reasons: list[str] = []
        status = "unknown"
        if not cache_exists:
            status = "error_missing_cache"
            reasons.append("cache file missing")
        elif not metadata_exists:
            status = "warning_legacy_cache_without_metadata"
            reasons.append("legacy cache without metadata")
        elif read_error:
            status = "unknown"
            reasons.append(f"metadata unreadable: {read_error}")
        else:
            meta_symbol = str(metadata.get("symbol", "")).zfill(6)
            meta_cache_file = Path(str(metadata.get("cache_file", ""))).name
            meta_row_count = pd.to_numeric(metadata.get("row_count", None), errors="coerce")
            if meta_symbol and meta_symbol != symbol:
                reasons.append(f"metadata symbol {meta_symbol} does not match {symbol}")
            if meta_cache_file and meta_cache_file != cache_file.name:
                reasons.append(f"metadata cache_file {meta_cache_file} does not match {cache_file.name}")
            if not pd.isna(meta_row_count) and int(float(meta_row_count)) != row_count:
                reasons.append(f"metadata row_count {int(float(meta_row_count))} does not match cache row_count {row_count}")
            adjust = _text(metadata.get("adjust", ""))
            if reasons:
                status = "warning_metadata_cache_mismatch"
            elif adjust in {"", "unknown"}:
                status = "warning_unknown_adjustment"
                reasons.append("metadata exists but adjustment is unknown")
            else:
                status = "ok"
                reasons.append("metadata matches cache and adjustment is explicit")

        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "cache_file": str(cache_file),
                "metadata_file": str(metadata_file),
                "metadata_exists": bool(metadata_exists),
                "source": _text(metadata.get("source", "")),
                "adjust": _text(metadata.get("adjust", "unknown" if cache_exists and not metadata_exists else "")),
                "api_name": _text(metadata.get("api_name", "")),
                "download_method": _text(metadata.get("download_method", "legacy_cache" if cache_exists and not metadata_exists else "")),
                "fallback_used": _bool_value(metadata.get("fallback_used", False)),
                "downloaded_at": _text(metadata.get("downloaded_at", "")),
                "row_count": int(row_count),
                "status": status,
                "reason": "; ".join(reasons),
            }
        )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=CACHE_METADATA_AUDIT_COLUMNS).to_csv(output_path / "cache_metadata_audit.csv", index=False, encoding="utf-8-sig")
    return rows


def summarize_cache_metadata_audit(rows: list[dict[str, Any]], example_limit: int = 10) -> dict[str, Any]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {
            "total_cache_files": 0,
            "metadata_exists_count": 0,
            "legacy_cache_without_metadata_count": 0,
            "unknown_adjustment_count": 0,
            "metadata_cache_mismatch_count": 0,
            "top_examples": [],
        }
    cache_exists = frame["status"].ne("error_missing_cache")
    metadata_exists = frame["metadata_exists"].astype(str).str.lower().isin(["true", "1", "yes"])
    legacy = frame["status"].eq("warning_legacy_cache_without_metadata")
    unknown = frame["adjust"].fillna("").astype(str).isin(["", "unknown"])
    mismatch = frame["status"].eq("warning_metadata_cache_mismatch")
    examples = frame[frame["status"].ne("ok")].head(example_limit)
    return {
        "total_cache_files": int(cache_exists.sum()),
        "metadata_exists_count": int(metadata_exists.sum()),
        "legacy_cache_without_metadata_count": int(legacy.sum()),
        "unknown_adjustment_count": int(frame[unknown]["symbol"].astype(str).nunique()),
        "metadata_cache_mismatch_count": int(mismatch.sum()),
        "top_examples": examples[["symbol", "name", "cache_file", "metadata_file", "status", "reason"]].to_dict("records"),
    }


def summarize_adjustment_audit(rows: list[dict[str, Any]], example_limit: int = 10) -> dict[str, Any]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {
            "total_checked": 0,
            "unknown_adjustment_count": 0,
            "fallback_used_count": 0,
            "abnormal_return_symbols": [],
            "possible_adjustment_issue_count": 0,
            "top_examples": [],
        }
    unknown_mask = frame["adjust"].fillna("").astype(str).isin(["", "unknown"])
    fallback_mask = frame["fallback_used"].astype(str).str.lower().isin(["true", "1", "yes"])
    abnormal = frame[pd.to_numeric(frame["abnormal_return_count"], errors="coerce").fillna(0) > 0]
    issue_mask = frame["possible_adjustment_issue"].astype(str).str.lower().isin(["true", "1", "yes"])
    examples = frame[frame["audit_status"].ne("ok")].head(example_limit)
    return {
        "total_checked": int(len(frame)),
        "unknown_adjustment_count": int(frame[unknown_mask]["symbol"].astype(str).nunique()),
        "fallback_used_count": int(frame[fallback_mask]["symbol"].astype(str).nunique()),
        "abnormal_return_symbols": abnormal["symbol"].astype(str).drop_duplicates().head(30).tolist(),
        "possible_adjustment_issue_count": int(frame[issue_mask]["symbol"].astype(str).nunique()),
        "top_examples": examples[["symbol", "name", "source", "adjust", "audit_status", "audit_reason"]].to_dict("records"),
    }
