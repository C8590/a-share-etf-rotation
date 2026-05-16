from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import yaml

from data.downloader import download_etf_history
from data.quality import RETURN_WARNING_THRESHOLD, analyze_single_etf
from data.storage import CACHE_META_DIR, DATA_DIR, build_cache_metadata, save_etf_data, write_cache_metadata
from data.trading_calendar import latest_trading_day_on_or_before


MAX_PILOT_REFRESH_COUNT = 11
MAX_MISSING_CACHE_REPAIR_COUNT = 10

REFRESH_PLAN_COLUMNS = [
    "symbol",
    "name",
    "source",
    "current_adjust",
    "cache_file",
    "metadata_file",
    "cache_exists",
    "metadata_exists",
    "latest_cache_date",
    "latest_expected_date",
    "end_date_gap_days",
    "quality_failed",
    "primary_failure_type",
    "adjustment_audit_status",
    "possible_adjustment_issue",
    "refresh_reason",
    "refresh_priority",
    "recommended_action",
    "requires_backup",
    "requires_manual_review",
    "safe_to_auto_refresh",
    "notes",
]

REFRESH_REASONS = [
    "legacy_cache_without_metadata",
    "unknown_adjustment",
    "possible_adjustment_issue",
    "missing_cache",
    "stale_end_date",
    "data_quality_failed",
    "download_failed",
]

PRIORITY_ORDER = [
    "P0_missing_cache",
    "P0_stale_end_date",
    "P0_quality_failed",
    "P1_legacy_unknown_adjustment",
    "P1_possible_adjustment_issue",
    "P2_optional_refresh",
]

PILOT_REFRESH_REPORT_COLUMNS = [
    "run_id",
    "symbol",
    "name",
    "refresh_attempted",
    "refresh_skipped",
    "skip_reason",
    "backup_created",
    "old_cache_exists",
    "old_metadata_exists",
    "new_cache_exists",
    "new_metadata_exists",
    "old_start_date",
    "old_end_date",
    "new_start_date",
    "new_end_date",
    "old_row_count",
    "new_row_count",
    "end_date_improved",
    "row_count_delta",
    "max_abs_close_diff",
    "abnormal_return_before",
    "abnormal_return_after",
    "old_adjust",
    "new_adjust",
    "metadata_written",
    "refresh_status",
    "refresh_reason",
    "requires_manual_review",
    "notes",
]

PILOT_REFRESH_STATUSES = {
    "refreshed_ok",
    "skipped_manual_review",
    "skipped_not_in_plan",
    "skipped_over_limit",
    "download_failed",
    "compare_failed",
    "metadata_missing_after_refresh",
    "unknown",
}

MISSING_CACHE_REPAIR_REPORT_COLUMNS = [
    "run_id",
    "symbol",
    "name",
    "repair_attempted",
    "repair_skipped",
    "skip_reason",
    "old_cache_exists",
    "old_metadata_exists",
    "backup_created",
    "new_cache_exists",
    "new_metadata_exists",
    "new_start_date",
    "new_end_date",
    "new_row_count",
    "new_source",
    "new_adjust",
    "download_method",
    "fallback_used",
    "fallback_chain",
    "repair_status",
    "failure_reason",
    "metadata_written",
    "quality_after_repair",
    "still_missing_cache",
    "requires_manual_review",
    "notes",
]

MISSING_CACHE_REPAIR_STATUSES = {
    "repaired_ok",
    "download_failed",
    "skipped_existing_cache",
    "skipped_not_missing_cache",
    "metadata_missing_after_repair",
    "quality_failed_after_repair",
    "unknown",
}


def _read_csv(path: Path, *, dtype: dict[str, Any] | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=dtype or str, encoding="utf-8-sig").fillna("")


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int_value(value: Any) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0 if pd.isna(parsed) else int(float(parsed))


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _split_reason(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def _join_reasons(reasons: list[str]) -> str:
    ordered = [reason for reason in REFRESH_REASONS if reason in set(reasons)]
    extras = [reason for reason in reasons if reason not in ordered]
    return ";".join([*ordered, *extras])


def _priority_from_reasons(reasons: list[str]) -> str:
    if "missing_cache" in reasons or "download_failed" in reasons:
        return "P0_missing_cache"
    if "stale_end_date" in reasons:
        return "P0_stale_end_date"
    if "data_quality_failed" in reasons:
        return "P0_quality_failed"
    if "possible_adjustment_issue" in reasons:
        return "P1_possible_adjustment_issue"
    if "legacy_cache_without_metadata" in reasons or "unknown_adjustment" in reasons:
        return "P1_legacy_unknown_adjustment"
    return "P2_optional_refresh"


def _recommended_action(priority: str, reasons: list[str]) -> str:
    if priority == "P0_missing_cache":
        return "pilot download missing cache with backup/audit logging before adding to formal universe"
    if priority == "P0_stale_end_date":
        return "pilot incremental refresh after backing up current cache and compare date coverage"
    if priority == "P0_quality_failed":
        return "manual triage quality failure, then targeted refresh only if source issue is plausible"
    if priority == "P1_possible_adjustment_issue":
        return "manual adjustment review before refresh; compare return jumps before accepting new cache"
    if priority == "P1_legacy_unknown_adjustment":
        return "batch pilot refresh to create metadata sidecar, then compare adjustment audit"
    if "unknown_adjustment" in reasons:
        return "review metadata/source path and refresh only in a controlled batch"
    return "optional refresh after higher-priority batches are stable"


def _manual_review_required(reasons: list[str], priority: str) -> bool:
    risky = {"possible_adjustment_issue", "data_quality_failed", "download_failed"}
    return bool(set(reasons) & risky or priority in {"P0_quality_failed", "P1_possible_adjustment_issue"})


def classify_refresh_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    status = str(candidate.get("metadata_status", ""))
    adjustment_status = str(candidate.get("adjustment_audit_status", ""))
    adjust = str(candidate.get("current_adjust", "")).strip()
    primary_failure_type = str(candidate.get("primary_failure_type", "")).strip()

    if not _bool_value(candidate.get("cache_exists", False)) or status == "error_missing_cache":
        reasons.append("missing_cache")
    if status == "warning_legacy_cache_without_metadata" or (
        _bool_value(candidate.get("cache_exists", False)) and not _bool_value(candidate.get("metadata_exists", False))
    ):
        reasons.append("legacy_cache_without_metadata")
    if adjust in {"", "unknown"} or adjustment_status == "warning_unknown_adjustment":
        reasons.append("unknown_adjustment")
    if _bool_value(candidate.get("possible_adjustment_issue", False)):
        reasons.append("possible_adjustment_issue")
    if _bool_value(candidate.get("stale_end_date", False)) or "stale_end_date" in _split_reason(candidate.get("failure_types", "")):
        reasons.append("stale_end_date")
    if _bool_value(candidate.get("quality_failed", False)):
        reasons.append("data_quality_failed")
    if primary_failure_type == "download_failed" or "download_failed" in _split_reason(candidate.get("failure_types", "")):
        reasons.append("download_failed")

    reasons = list(dict.fromkeys(reasons))
    priority = _priority_from_reasons(reasons)
    requires_backup = _bool_value(candidate.get("cache_exists", False))
    requires_manual_review = _manual_review_required(reasons, priority)
    safe_to_auto_refresh = bool(
        reasons
        and requires_backup
        and not requires_manual_review
        and priority in {"P0_stale_end_date", "P1_legacy_unknown_adjustment", "P2_optional_refresh"}
    )
    notes: list[str] = []
    if not reasons:
        notes.append("no refresh signal from current audits")
    if "missing_cache" in reasons:
        notes.append("no existing cache to back up; treat as targeted download, not overwrite")
    if "possible_adjustment_issue" in reasons:
        notes.append("price jump may be adjustment/source related; compare returns manually")
    if "data_quality_failed" in reasons:
        notes.append("quality gate failed; do not auto-accept refreshed data")
    if "legacy_cache_without_metadata" in reasons:
        notes.append("refresh goal is metadata traceability, not performance improvement")

    return {
        "refresh_reason": _join_reasons(reasons),
        "refresh_priority": priority,
        "recommended_action": _recommended_action(priority, reasons),
        "requires_backup": bool(requires_backup),
        "requires_manual_review": bool(requires_manual_review),
        "safe_to_auto_refresh": bool(safe_to_auto_refresh),
        "notes": "; ".join(notes),
    }


def _latest_expected_from_reports(output_dir: Path) -> str:
    failure = _read_csv(output_dir / "data_failure_summary.csv", dtype={"symbol": str})
    if not failure.empty and "latest_expected_date" in failure.columns:
        dates = pd.to_datetime(failure["latest_expected_date"], errors="coerce").dropna()
        if not dates.empty:
            return str(dates.max().date())
    try:
        return str(latest_trading_day_on_or_before(pd.Timestamp.today()).date())
    except Exception:
        return ""


def _failure_maps(output_dir: Path) -> tuple[dict[str, int], dict[str, str]]:
    failure = _read_csv(output_dir / "data_failure_summary.csv", dtype={"symbol": str})
    gap_by_symbol: dict[str, int] = {}
    types_by_symbol: dict[str, list[str]] = {}
    if failure.empty or "symbol" not in failure.columns:
        return {}, {}
    for row in failure.to_dict("records"):
        symbol = str(row.get("symbol", "")).zfill(6)
        failure_type = str(row.get("failure_type", "")).strip()
        if not symbol:
            continue
        if failure_type:
            types_by_symbol.setdefault(symbol, [])
            if failure_type not in types_by_symbol[symbol]:
                types_by_symbol[symbol].append(failure_type)
        gap_by_symbol[symbol] = max(gap_by_symbol.get(symbol, 0), _int_value(row.get("end_date_gap_days", 0)))
    return gap_by_symbol, {symbol: ";".join(values) for symbol, values in types_by_symbol.items()}


def _load_frame(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    return pd.read_csv(file_path, dtype={"symbol": str}, encoding="utf-8-sig")


def _frame_bounds(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame.reset_index() if "date" not in frame.columns else frame.copy()
    dates = pd.to_datetime(work.get("date", pd.Series(dtype=object)), errors="coerce").dropna()
    return {
        "start_date": _date_text(dates.min()) if not dates.empty else "",
        "end_date": _date_text(dates.max()) if not dates.empty else "",
        "row_count": int(len(work)),
    }


def _abnormal_return_count(frame: pd.DataFrame) -> int:
    if frame.empty or "close" not in frame.columns:
        return 0
    close = pd.to_numeric(frame["close"], errors="coerce")
    returns = close.pct_change().abs()
    return int((returns > RETURN_WARNING_THRESHOLD).sum())


def compare_cache_before_after(before: pd.DataFrame, after: pd.DataFrame) -> dict[str, Any]:
    before_frame = before.reset_index() if "date" not in before.columns else before.copy()
    after_frame = after.reset_index() if "date" not in after.columns else after.copy()
    before_dates = pd.to_datetime(before_frame.get("date", pd.Series(dtype=object)), errors="coerce").dropna()
    after_dates = pd.to_datetime(after_frame.get("date", pd.Series(dtype=object)), errors="coerce").dropna()
    result = {
        "before_rows": int(len(before_frame)),
        "after_rows": int(len(after_frame)),
        "row_count_delta": int(len(after_frame) - len(before_frame)),
        "before_start_date": _date_text(before_dates.min()) if not before_dates.empty else "",
        "before_end_date": _date_text(before_dates.max()) if not before_dates.empty else "",
        "after_start_date": _date_text(after_dates.min()) if not after_dates.empty else "",
        "after_end_date": _date_text(after_dates.max()) if not after_dates.empty else "",
        "overlap_row_count": 0,
        "max_close_abs_diff": "",
        "max_close_pct_diff": "",
    }
    if "date" not in before_frame.columns or "date" not in after_frame.columns or "close" not in before_frame.columns or "close" not in after_frame.columns:
        return result
    left = before_frame[["date", "close"]].copy()
    right = after_frame[["date", "close"]].copy()
    left["date"] = pd.to_datetime(left["date"], errors="coerce")
    right["date"] = pd.to_datetime(right["date"], errors="coerce")
    left["close"] = pd.to_numeric(left["close"], errors="coerce")
    right["close"] = pd.to_numeric(right["close"], errors="coerce")
    merged = left.merge(right, on="date", how="inner", suffixes=("_before", "_after")).dropna()
    result["overlap_row_count"] = int(len(merged))
    if not merged.empty:
        diff = (merged["close_after"] - merged["close_before"]).abs()
        pct = diff / merged["close_before"].abs().replace(0, pd.NA)
        result["max_close_abs_diff"] = float(diff.max())
        result["max_close_pct_diff"] = "" if pct.dropna().empty else float(pct.max())
    return result


def compare_refreshed_cache(before: pd.DataFrame, after: pd.DataFrame) -> dict[str, Any]:
    comparison = compare_cache_before_after(before, after)
    old_end = pd.to_datetime(comparison.get("before_end_date", ""), errors="coerce")
    new_end = pd.to_datetime(comparison.get("after_end_date", ""), errors="coerce")
    comparison.update(
        {
            "end_date_improved": bool(not pd.isna(old_end) and not pd.isna(new_end) and new_end > old_end),
            "abnormal_return_before": _abnormal_return_count(before),
            "abnormal_return_after": _abnormal_return_count(after),
        }
    )
    return comparison


def build_refresh_plan(
    etf_pool: list[dict[str, Any]],
    output_dir: str | Path = "output",
    cache_dir: str | Path = DATA_DIR,
    cache_meta_dir: str | Path = "data/cache_meta",
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    cache_path = Path(cache_dir)
    meta_path = Path(cache_meta_dir)
    metadata_audit = _read_csv(output_path / "cache_metadata_audit.csv", dtype={"symbol": str})
    adjustment_audit = _read_csv(output_path / "adjustment_audit.csv", dtype={"symbol": str})
    quality = _read_csv(output_path / "data_quality_report.csv", dtype={"symbol": str})
    gap_by_symbol, failure_types_by_symbol = _failure_maps(output_path)
    latest_expected_date = _latest_expected_from_reports(output_path)

    metadata_by_symbol = {
        str(row.get("symbol", "")).zfill(6): row
        for row in metadata_audit.to_dict("records")
        if str(row.get("symbol", "")).strip()
    }
    adjustment_by_symbol = {
        str(row.get("symbol", "")).zfill(6): row
        for row in adjustment_audit.to_dict("records")
        if str(row.get("symbol", "")).strip()
    }
    quality_by_symbol = {
        str(row.get("symbol", "")).zfill(6): row
        for row in quality.to_dict("records")
        if str(row.get("symbol", "")).strip()
    }

    rows: list[dict[str, Any]] = []
    for etf in etf_pool:
        symbol = str(etf.get("symbol", "")).zfill(6)
        if not symbol:
            continue
        name = str(etf.get("name", ""))
        meta_row = metadata_by_symbol.get(symbol, {})
        adjust_row = adjustment_by_symbol.get(symbol, {})
        quality_row = quality_by_symbol.get(symbol, {})
        cache_file = str(meta_row.get("cache_file") or (cache_path / f"{symbol}.csv"))
        metadata_file = str(meta_row.get("metadata_file") or (meta_path / f"{symbol}.json"))
        cache_exists = Path(cache_file).exists()
        metadata_exists = _bool_value(meta_row.get("metadata_exists", Path(metadata_file).exists()))
        latest_cache_date = _date_text(
            meta_row.get("end_date")
            or adjust_row.get("end_date")
            or quality_row.get("end_date")
            or etf.get("latest_date", "")
        )
        gap_days = gap_by_symbol.get(symbol, 0)
        if not gap_days and latest_expected_date and latest_cache_date:
            gap_days = max(0, int((pd.Timestamp(latest_expected_date) - pd.Timestamp(latest_cache_date)).days))
        quality_failed = str(quality_row.get("status", "")).strip() == "failed"
        primary_failure_type = str(quality_row.get("primary_failure_type", "") or "").strip()
        failure_types = failure_types_by_symbol.get(symbol, "")
        if not primary_failure_type and failure_types:
            primary_failure_type = failure_types.split(";")[0]
        base = {
            "symbol": symbol,
            "name": str(meta_row.get("name") or adjust_row.get("name") or quality_row.get("name") or name),
            "source": str(meta_row.get("source") or adjust_row.get("source") or ""),
            "current_adjust": str(meta_row.get("adjust") or adjust_row.get("adjust") or ""),
            "cache_file": cache_file,
            "metadata_file": metadata_file,
            "cache_exists": bool(cache_exists),
            "metadata_exists": bool(metadata_exists),
            "latest_cache_date": latest_cache_date,
            "latest_expected_date": latest_expected_date,
            "end_date_gap_days": int(gap_days),
            "quality_failed": bool(quality_failed),
            "primary_failure_type": primary_failure_type,
            "adjustment_audit_status": str(adjust_row.get("audit_status", "")),
            "possible_adjustment_issue": _bool_value(adjust_row.get("possible_adjustment_issue", False)),
            "metadata_status": str(meta_row.get("status", "")),
            "failure_types": failure_types,
        }
        classified = classify_refresh_candidate(base)
        row = {**base, **classified}
        rows.append({column: row.get(column, "") for column in REFRESH_PLAN_COLUMNS})

    rows.sort(key=lambda row: (PRIORITY_ORDER.index(str(row["refresh_priority"])) if row["refresh_priority"] in PRIORITY_ORDER else 999, row["symbol"]))
    return rows


def write_refresh_plan(rows: list[dict[str, Any]], path: str | Path = "output/cache_refresh_plan.csv") -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=REFRESH_PLAN_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def summarize_refresh_plan(rows: list[dict[str, Any]], example_limit: int = 10) -> dict[str, Any]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {
            "total_candidates": 0,
            "priority_counts": {},
            "reason_counts": {},
            "safe_to_auto_refresh_count": 0,
            "manual_review_required_count": 0,
            "top_examples": [],
        }
    candidate_mask = frame["refresh_reason"].fillna("").astype(str).str.strip().ne("")
    candidates = frame[candidate_mask].copy()
    reason_counts: dict[str, int] = {}
    for text in candidates["refresh_reason"].fillna("").astype(str):
        for reason in _split_reason(text):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    priority_counts = candidates["refresh_priority"].value_counts().sort_index().to_dict()
    safe_count = int(candidates["safe_to_auto_refresh"].astype(str).str.lower().isin(["true", "1", "yes"]).sum())
    manual_count = int(candidates["requires_manual_review"].astype(str).str.lower().isin(["true", "1", "yes"]).sum())
    examples = candidates.head(example_limit)[
        [
            "symbol",
            "name",
            "refresh_reason",
            "refresh_priority",
            "recommended_action",
            "requires_manual_review",
        ]
    ].to_dict("records")
    return {
        "total_candidates": int(len(candidates)),
        "priority_counts": {str(k): int(v) for k, v in priority_counts.items()},
        "reason_counts": {str(k): int(v) for k, v in sorted(reason_counts.items())},
        "safe_to_auto_refresh_count": safe_count,
        "manual_review_required_count": manual_count,
        "top_examples": examples,
    }


def _load_plan(path: str | Path = "output/cache_refresh_plan.csv") -> pd.DataFrame:
    plan = _read_csv(Path(path), dtype={"symbol": str})
    if not plan.empty and "symbol" in plan.columns:
        plan["symbol"] = plan["symbol"].astype(str).str.zfill(6)
    return plan


def _core_symbols(config_path: str | Path = "config/etf_universe.yaml") -> list[str]:
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    symbols = ((raw.get("presets") or {}).get("core_11") or {}).get("symbols") or []
    return [str(symbol).zfill(6) for symbol in symbols]


def build_pilot_refresh_symbols(
    *,
    pool: str | None = None,
    symbols: str | list[str] | None = None,
    max_count: int = MAX_PILOT_REFRESH_COUNT,
    include_manual_review: bool = False,
    plan_path: str | Path = "output/cache_refresh_plan.csv",
    config_path: str | Path = "config/etf_universe.yaml",
) -> list[dict[str, Any]]:
    if max_count > MAX_PILOT_REFRESH_COUNT:
        raise ValueError(f"pilot refresh max_count must be <= {MAX_PILOT_REFRESH_COUNT}")
    if not pool and not symbols:
        raise ValueError("pilot refresh requires --pool core_11 or explicit --symbols")
    if pool and pool != "core_11":
        raise ValueError("pilot refresh only supports --pool core_11")

    if symbols:
        if isinstance(symbols, str):
            requested = [item.strip().zfill(6) for item in symbols.split(",") if item.strip()]
        else:
            requested = [str(item).zfill(6) for item in symbols]
    else:
        requested = _core_symbols(config_path)
    requested = list(dict.fromkeys(requested))

    plan = _load_plan(plan_path)
    plan_by_symbol = {
        str(row.get("symbol", "")).zfill(6): row
        for row in plan.to_dict("records")
        if str(row.get("symbol", "")).strip()
    }
    rows: list[dict[str, Any]] = []
    for idx, symbol in enumerate(requested):
        plan_row = dict(plan_by_symbol.get(symbol, {}))
        if plan_row:
            plan_row["symbol"] = symbol
        else:
            plan_row = {
                "symbol": symbol,
                "name": "",
                "refresh_reason": "",
                "refresh_priority": "",
                "requires_manual_review": False,
                "cache_file": str(Path("data/cache") / f"{symbol}.csv"),
                "metadata_file": str(Path("data/cache_meta") / f"{symbol}.json"),
            }
        plan_row["_requested_order"] = idx
        plan_row["_over_limit"] = idx >= max_count
        plan_row["_included_by_manual_review_override"] = bool(include_manual_review and _bool_value(plan_row.get("requires_manual_review", False)))
        rows.append(plan_row)
    return rows


def backup_cache_file(symbol: str, backup_dir: str | Path, cache_dir: str | Path = DATA_DIR) -> dict[str, Any]:
    source = Path(cache_dir) / f"{str(symbol).zfill(6)}.csv"
    if not source.exists():
        return {"source": str(source), "backup": "", "exists": False, "copied": False}
    destination = Path(backup_dir) / "cache" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {"source": str(source), "backup": str(destination), "exists": True, "copied": True}


def backup_metadata_file(symbol: str, backup_dir: str | Path, cache_meta_dir: str | Path = CACHE_META_DIR) -> dict[str, Any]:
    source = Path(cache_meta_dir) / f"{str(symbol).zfill(6)}.json"
    if not source.exists():
        return {"source": str(source), "backup": "", "exists": False, "copied": False}
    destination = Path(backup_dir) / "cache_meta" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {"source": str(source), "backup": str(destination), "exists": True, "copied": True}


def _write_manifest(
    *,
    backup_dir: Path,
    run_id: str,
    symbols: list[str],
    backup_files: list[dict[str, Any]],
    command: str,
    notes: str,
) -> Path:
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "symbols": symbols,
        "backup_files": backup_files,
        "command": command,
        "notes": notes,
    }
    path = backup_dir / "refresh_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _empty_report_row(run_id: str, plan_row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(plan_row.get("symbol", "")).zfill(6)
    return {
        "run_id": run_id,
        "symbol": symbol,
        "name": plan_row.get("name", ""),
        "refresh_attempted": False,
        "refresh_skipped": False,
        "skip_reason": "",
        "backup_created": False,
        "old_cache_exists": False,
        "old_metadata_exists": False,
        "new_cache_exists": False,
        "new_metadata_exists": False,
        "old_start_date": "",
        "old_end_date": "",
        "new_start_date": "",
        "new_end_date": "",
        "old_row_count": 0,
        "new_row_count": 0,
        "end_date_improved": False,
        "row_count_delta": 0,
        "max_abs_close_diff": "",
        "abnormal_return_before": 0,
        "abnormal_return_after": 0,
        "old_adjust": plan_row.get("current_adjust", ""),
        "new_adjust": "",
        "metadata_written": False,
        "refresh_status": "unknown",
        "refresh_reason": plan_row.get("refresh_reason", ""),
        "requires_manual_review": _bool_value(plan_row.get("requires_manual_review", False)),
        "notes": "",
    }


def write_pilot_refresh_report(rows: list[dict[str, Any]], path: str | Path = "output/pilot_refresh_report.csv") -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=PILOT_REFRESH_REPORT_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def summarize_pilot_refresh(
    rows: list[dict[str, Any]] | None = None,
    report_path: str | Path = "output/pilot_refresh_report.csv",
    example_limit: int = 10,
) -> dict[str, Any]:
    if rows is None:
        path = Path(report_path)
        if not path.exists():
            return {
                "status": "not_run",
                "report": str(path),
                "last_run_id": "",
                "attempted_count": 0,
                "refreshed_ok_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "metadata_written_count": 0,
                "end_date_improved_count": 0,
                "top_examples": [],
            }
        frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    else:
        frame = pd.DataFrame(rows)
    if frame.empty:
        return {
            "status": "not_run",
            "report": str(report_path),
            "last_run_id": "",
            "attempted_count": 0,
            "refreshed_ok_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "metadata_written_count": 0,
            "end_date_improved_count": 0,
            "top_examples": [],
        }
    status = frame["refresh_status"].astype(str)
    attempted = frame["refresh_attempted"].astype(str).str.lower().isin(["true", "1", "yes"])
    skipped = frame["refresh_skipped"].astype(str).str.lower().isin(["true", "1", "yes"])
    failed_statuses = {"download_failed", "compare_failed", "metadata_missing_after_refresh"}
    metadata_written = frame["metadata_written"].astype(str).str.lower().isin(["true", "1", "yes"])
    improved = frame["end_date_improved"].astype(str).str.lower().isin(["true", "1", "yes"])
    examples = frame.head(example_limit)[["symbol", "name", "refresh_status", "skip_reason", "notes"]].to_dict("records")
    return {
        "status": "ok",
        "report": str(report_path),
        "last_run_id": str(frame.iloc[-1].get("run_id", "")),
        "attempted_count": int(attempted.sum()),
        "refreshed_ok_count": int(status.eq("refreshed_ok").sum()),
        "skipped_count": int(skipped.sum()),
        "failed_count": int(status.isin(failed_statuses).sum()),
        "metadata_written_count": int(metadata_written.sum()),
        "end_date_improved_count": int(improved.sum()),
        "top_examples": examples,
    }


def build_missing_cache_symbols(
    *,
    symbols: str | list[str] | None = None,
    max_count: int = MAX_MISSING_CACHE_REPAIR_COUNT,
    plan_path: str | Path = "output/cache_refresh_plan.csv",
) -> list[dict[str, Any]]:
    if max_count > MAX_MISSING_CACHE_REPAIR_COUNT:
        raise ValueError(f"missing cache repair max_count must be <= {MAX_MISSING_CACHE_REPAIR_COUNT}")
    plan = _load_plan(plan_path)
    if plan.empty:
        return []
    plan_by_symbol = {
        str(row.get("symbol", "")).zfill(6): row
        for row in plan.to_dict("records")
        if str(row.get("symbol", "")).strip()
    }
    if symbols:
        if isinstance(symbols, str):
            requested = [item.strip().zfill(6) for item in symbols.split(",") if item.strip()]
        else:
            requested = [str(item).zfill(6) for item in symbols]
    else:
        requested = [
            str(row.get("symbol", "")).zfill(6)
            for row in plan.to_dict("records")
            if str(row.get("refresh_priority", "")).strip() == "P0_missing_cache"
        ]
    requested = list(dict.fromkeys(requested))

    rows: list[dict[str, Any]] = []
    for idx, symbol in enumerate(requested):
        plan_row = dict(plan_by_symbol.get(symbol, {}))
        if plan_row:
            plan_row["symbol"] = symbol
        else:
            plan_row = {
                "symbol": symbol,
                "name": "",
                "refresh_priority": "",
                "refresh_reason": "",
                "requires_manual_review": False,
            }
        plan_row["_requested_order"] = idx
        plan_row["_over_limit"] = idx >= max_count
        rows.append(plan_row)
    return rows


def _empty_missing_cache_repair_row(run_id: str, plan_row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(plan_row.get("symbol", "")).zfill(6)
    return {
        "run_id": run_id,
        "symbol": symbol,
        "name": plan_row.get("name", ""),
        "repair_attempted": False,
        "repair_skipped": False,
        "skip_reason": "",
        "old_cache_exists": False,
        "old_metadata_exists": False,
        "backup_created": False,
        "new_cache_exists": False,
        "new_metadata_exists": False,
        "new_start_date": "",
        "new_end_date": "",
        "new_row_count": 0,
        "new_source": "",
        "new_adjust": "",
        "download_method": "",
        "fallback_used": False,
        "fallback_chain": "",
        "repair_status": "unknown",
        "failure_reason": "",
        "metadata_written": False,
        "quality_after_repair": "",
        "still_missing_cache": True,
        "requires_manual_review": _bool_value(plan_row.get("requires_manual_review", False)),
        "notes": "",
    }


def write_missing_cache_repair_report(rows: list[dict[str, Any]], path: str | Path = "output/missing_cache_repair_report.csv") -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=MISSING_CACHE_REPAIR_REPORT_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def summarize_missing_cache_repair(
    rows: list[dict[str, Any]] | None = None,
    report_path: str | Path = "output/missing_cache_repair_report.csv",
    example_limit: int = 10,
) -> dict[str, Any]:
    if rows is None:
        path = Path(report_path)
        if not path.exists():
            return {
                "status": "not_run",
                "report": str(path),
                "last_run_id": "",
                "attempted_count": 0,
                "repaired_ok_count": 0,
                "download_failed_count": 0,
                "still_missing_cache_count": 0,
                "metadata_written_count": 0,
                "quality_failed_after_repair_count": 0,
                "top_examples": [],
            }
        frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    else:
        frame = pd.DataFrame(rows)
    if frame.empty:
        return {
            "status": "not_run",
            "report": str(report_path),
            "last_run_id": "",
            "attempted_count": 0,
            "repaired_ok_count": 0,
            "download_failed_count": 0,
            "still_missing_cache_count": 0,
            "metadata_written_count": 0,
            "quality_failed_after_repair_count": 0,
            "top_examples": [],
        }
    status = frame["repair_status"].astype(str)
    attempted = frame["repair_attempted"].astype(str).str.lower().isin(["true", "1", "yes"])
    metadata_written = frame["metadata_written"].astype(str).str.lower().isin(["true", "1", "yes"])
    still_missing = frame["still_missing_cache"].astype(str).str.lower().isin(["true", "1", "yes"])
    examples = frame.head(example_limit)[["symbol", "name", "repair_status", "skip_reason", "failure_reason", "notes"]].to_dict("records")
    return {
        "status": "ok",
        "report": str(report_path),
        "last_run_id": str(frame.iloc[-1].get("run_id", "")),
        "attempted_count": int(attempted.sum()),
        "repaired_ok_count": int(status.eq("repaired_ok").sum()),
        "download_failed_count": int(status.eq("download_failed").sum()),
        "still_missing_cache_count": int(still_missing.sum()),
        "metadata_written_count": int(metadata_written.sum()),
        "quality_failed_after_repair_count": int(status.eq("quality_failed_after_repair").sum()),
        "top_examples": examples,
    }


def repair_missing_cache(
    *,
    symbols: str | list[str] | None = None,
    max_count: int = MAX_MISSING_CACHE_REPAIR_COUNT,
    dry_run: bool = False,
    output_dir: str | Path = "output",
    cache_dir: str | Path = DATA_DIR,
    cache_meta_dir: str | Path = CACHE_META_DIR,
    backup_root: str | Path = "data/cache_backup",
    plan_path: str | Path = "output/cache_refresh_plan.csv",
    command: str = "",
    downloader: Callable[..., tuple[pd.DataFrame, str, dict[str, Any]]] = download_etf_history,
) -> tuple[list[dict[str, Any]], Path | None]:
    selected = build_missing_cache_symbols(symbols=symbols, max_count=max_count, plan_path=plan_path)
    run_id = "missing_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(backup_root) / run_id
    rows: list[dict[str, Any]] = []
    backup_files: list[dict[str, Any]] = []
    attempted_symbols: list[str] = []

    for plan_row in selected:
        symbol = str(plan_row.get("symbol", "")).zfill(6)
        row = _empty_missing_cache_repair_row(run_id, plan_row)
        cache_path = Path(cache_dir) / f"{symbol}.csv"
        meta_path = Path(cache_meta_dir) / f"{symbol}.json"
        old_cache_exists = cache_path.exists()
        old_metadata_exists = meta_path.exists()
        row.update({"old_cache_exists": old_cache_exists, "old_metadata_exists": old_metadata_exists, "still_missing_cache": not old_cache_exists})

        if plan_row.get("_over_limit"):
            row.update({"repair_skipped": True, "skip_reason": "selected symbol is over max_count", "repair_status": "unknown"})
            rows.append(row)
            continue
        if str(plan_row.get("refresh_priority", "")).strip() != "P0_missing_cache":
            row.update({"repair_skipped": True, "skip_reason": "symbol is not P0_missing_cache", "repair_status": "skipped_not_missing_cache"})
            rows.append(row)
            continue
        if old_cache_exists:
            row.update({"repair_skipped": True, "skip_reason": "cache already exists", "repair_status": "skipped_existing_cache", "still_missing_cache": False})
            rows.append(row)
            continue
        if dry_run:
            row.update({"repair_skipped": True, "skip_reason": "dry_run=True", "repair_status": "unknown", "notes": "dry-run only; cache not modified"})
            rows.append(row)
            continue

        row["repair_attempted"] = True
        attempted_symbols.append(symbol)
        cache_backup = backup_cache_file(symbol, backup_dir, cache_dir)
        meta_backup = backup_metadata_file(symbol, backup_dir, cache_meta_dir)
        backup_files.extend([{"symbol": symbol, "kind": "cache", **cache_backup}, {"symbol": symbol, "kind": "metadata", **meta_backup}])
        row["backup_created"] = bool(cache_backup["copied"] or meta_backup["copied"])
        try:
            df, source, download_meta = downloader(symbol=symbol, start_date="20190101", end_date=None, retries=2, retry_delay=2.0)
            saved_path = save_etf_data(symbol, df, data_dir=Path(cache_dir), name=str(plan_row.get("name", "")), source=source)
            metadata = build_cache_metadata(symbol, df, name=str(plan_row.get("name", "")), source=source, cache_file=saved_path, **download_meta)
            metadata["created_by"] = "ETF-GAP-003E"
            metadata_path = write_cache_metadata(symbol, metadata, Path(cache_meta_dir))
            new_cache = _load_frame(saved_path)
            new_bounds = _frame_bounds(new_cache)
            quality = analyze_single_etf(symbol, str(plan_row.get("name", "")), new_cache)
            metadata_exists = metadata_path.exists()
            new_cache_exists = Path(saved_path).exists()
            if not metadata_exists:
                status = "metadata_missing_after_repair"
            elif quality.status == "failed":
                status = "quality_failed_after_repair"
            else:
                status = "repaired_ok"
            row.update(
                {
                    "new_cache_exists": new_cache_exists,
                    "new_metadata_exists": metadata_exists,
                    "new_start_date": new_bounds["start_date"],
                    "new_end_date": new_bounds["end_date"],
                    "new_row_count": new_bounds["row_count"],
                    "new_source": source,
                    "new_adjust": metadata.get("adjust", ""),
                    "download_method": metadata.get("download_method", ""),
                    "fallback_used": bool(metadata.get("fallback_used", False)),
                    "fallback_chain": ";".join(str(item) for item in metadata.get("fallback_chain", [])),
                    "repair_status": status,
                    "failure_reason": "; ".join(quality.errors),
                    "metadata_written": bool(metadata_exists),
                    "quality_after_repair": quality.status,
                    "still_missing_cache": not new_cache_exists,
                    "notes": f"source={source}; backup_dir={backup_dir}",
                }
            )
        except Exception as exc:  # noqa: BLE001
            row.update(
                {
                    "repair_status": "download_failed",
                    "failure_reason": str(exc),
                    "new_cache_exists": cache_path.exists(),
                    "new_metadata_exists": meta_path.exists(),
                    "still_missing_cache": not cache_path.exists(),
                }
            )
        rows.append(row)

    manifest_path: Path | None = None
    if not dry_run:
        manifest_path = _write_manifest(
            backup_dir=backup_dir,
            run_id=run_id,
            symbols=attempted_symbols,
            backup_files=backup_files,
            command=command,
            notes="ETF-GAP-003E missing cache repair backup manifest",
        )
    write_missing_cache_repair_report(rows, Path(output_dir) / "missing_cache_repair_report.csv")
    return rows, manifest_path


def run_pilot_refresh(
    *,
    pool: str | None = None,
    symbols: str | list[str] | None = None,
    max_count: int = MAX_PILOT_REFRESH_COUNT,
    dry_run: bool = False,
    include_manual_review: bool = False,
    output_dir: str | Path = "output",
    cache_dir: str | Path = DATA_DIR,
    cache_meta_dir: str | Path = CACHE_META_DIR,
    backup_root: str | Path = "data/cache_backup",
    plan_path: str | Path = "output/cache_refresh_plan.csv",
    config_path: str | Path = "config/etf_universe.yaml",
    command: str = "",
    downloader: Callable[..., tuple[pd.DataFrame, str, dict[str, Any]]] = download_etf_history,
) -> tuple[list[dict[str, Any]], Path | None]:
    selected = build_pilot_refresh_symbols(
        pool=pool,
        symbols=symbols,
        max_count=max_count,
        include_manual_review=include_manual_review,
        plan_path=plan_path,
        config_path=config_path,
    )
    run_id = "pilot_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(backup_root) / run_id
    rows: list[dict[str, Any]] = []
    backup_files: list[dict[str, Any]] = []
    attempted_symbols: list[str] = []

    for plan_row in selected:
        symbol = str(plan_row.get("symbol", "")).zfill(6)
        row = _empty_report_row(run_id, plan_row)
        cache_path = Path(cache_dir) / f"{symbol}.csv"
        meta_path = Path(cache_meta_dir) / f"{symbol}.json"
        old_cache = _load_frame(cache_path)
        old_bounds = _frame_bounds(old_cache)
        row.update(
            {
                "old_cache_exists": cache_path.exists(),
                "old_metadata_exists": meta_path.exists(),
                "old_start_date": old_bounds["start_date"],
                "old_end_date": old_bounds["end_date"],
                "old_row_count": old_bounds["row_count"],
                "abnormal_return_before": _abnormal_return_count(old_cache),
            }
        )

        if plan_row.get("_over_limit"):
            row.update({"refresh_skipped": True, "skip_reason": "selected symbol is over max_count", "refresh_status": "skipped_over_limit"})
            rows.append(row)
            continue
        if not plan_row.get("refresh_reason"):
            row.update({"refresh_skipped": True, "skip_reason": "symbol is not in cache_refresh_plan candidates", "refresh_status": "skipped_not_in_plan"})
            rows.append(row)
            continue
        if _bool_value(plan_row.get("requires_manual_review", False)) and not include_manual_review:
            row.update({"refresh_skipped": True, "skip_reason": "requires_manual_review=True", "refresh_status": "skipped_manual_review"})
            rows.append(row)
            continue
        if dry_run:
            row.update({"refresh_skipped": True, "skip_reason": "dry_run=True", "refresh_status": "unknown", "notes": "dry-run only; cache not modified"})
            rows.append(row)
            continue

        row["refresh_attempted"] = True
        attempted_symbols.append(symbol)
        cache_backup = backup_cache_file(symbol, backup_dir, cache_dir)
        meta_backup = backup_metadata_file(symbol, backup_dir, cache_meta_dir)
        backup_files.extend([{"symbol": symbol, "kind": "cache", **cache_backup}, {"symbol": symbol, "kind": "metadata", **meta_backup}])
        row["backup_created"] = bool(cache_backup["copied"] or meta_backup["copied"])
        try:
            df, source, download_meta = downloader(symbol=symbol, start_date="20190101", end_date=None, retries=2, retry_delay=2.0)
            saved_path = save_etf_data(symbol, df, data_dir=Path(cache_dir), name=str(plan_row.get("name", "")), source=source)
            metadata = build_cache_metadata(symbol, df, name=str(plan_row.get("name", "")), source=source, cache_file=saved_path, **download_meta)
            metadata["created_by"] = "ETF-GAP-003D"
            write_cache_metadata(symbol, metadata, Path(cache_meta_dir))
            new_cache = _load_frame(saved_path)
            new_bounds = _frame_bounds(new_cache)
            comparison = compare_refreshed_cache(old_cache, new_cache)
            new_metadata_exists = meta_path.exists()
            row.update(
                {
                    "new_cache_exists": Path(saved_path).exists(),
                    "new_metadata_exists": new_metadata_exists,
                    "new_start_date": new_bounds["start_date"],
                    "new_end_date": new_bounds["end_date"],
                    "new_row_count": new_bounds["row_count"],
                    "end_date_improved": comparison["end_date_improved"],
                    "row_count_delta": comparison["row_count_delta"],
                    "max_abs_close_diff": comparison["max_close_abs_diff"],
                    "abnormal_return_after": comparison["abnormal_return_after"],
                    "new_adjust": metadata.get("adjust", ""),
                    "metadata_written": bool(new_metadata_exists),
                    "refresh_status": "refreshed_ok" if new_metadata_exists else "metadata_missing_after_refresh",
                    "notes": f"source={source}; backup_dir={backup_dir}",
                }
            )
        except Exception as exc:  # noqa: BLE001
            row.update({"refresh_status": "download_failed", "notes": str(exc)})
        rows.append(row)

    manifest_path: Path | None = None
    if not dry_run:
        manifest_path = _write_manifest(
            backup_dir=backup_dir,
            run_id=run_id,
            symbols=attempted_symbols,
            backup_files=backup_files,
            command=command,
            notes="ETF-GAP-003D pilot refresh backup manifest",
        )
    write_pilot_refresh_report(rows, Path(output_dir) / "pilot_refresh_report.csv")
    return rows, manifest_path
