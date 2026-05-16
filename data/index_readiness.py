from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from data.index_data import INDEX_CACHE_COLUMNS, INDEX_MAP_COLUMNS
from data.schema import validate_index_cache_frame


INDEX_007B_READINESS_COLUMNS = [
    "readiness_item",
    "current_status",
    "passed",
    "blocking",
    "severity",
    "threshold",
    "actual_value",
    "blocker_type",
    "dependency",
    "remediation_action",
    "prerequisite_task",
    "estimated_path",
    "can_be_resolved_by_network",
    "can_be_resolved_by_index_update",
    "can_be_resolved_by_manual_mapping",
    "can_be_resolved_by_schema_fix",
    "notes",
]

INDEX_007B_UNLOCK_PLAN_COLUMNS = [
    "symbol",
    "name",
    "tracking_index_code",
    "tracking_index_name",
    "mapping_method",
    "mapping_confidence",
    "usable_as_benchmark",
    "index_cache_exists",
    "index_cache_schema_valid",
    "index_fetch_success",
    "benchmark_status",
    "etf_metrics_status",
    "tracking_error_status",
    "relative_return_status",
    "required_action",
    "unlock_priority",
    "eligible_for_007b_after_unlock",
    "notes",
]

INDEX_007B_READINESS_SUMMARY_COLUMNS = [
    "summary_item",
    "count",
    "severity",
    "finding",
    "suggested_action",
    "examples",
    "notes",
]

INDEX_007B_BLOCKER_TYPES = {
    "benchmark_dependency",
    "index_cache_missing",
    "schema_invalid",
    "source_network",
    "source_fetch_failed",
    "mapping_unconfirmed",
    "insufficient_overlap",
    "metric_unavailable",
    "fake_benchmark_guard",
    "manual_mapping",
    "unknown",
}

HARD_MAPPING_METHODS = {"config_manual", "metadata_exact"}
NETWORK_MARKERS = (
    "proxyerror",
    "proxy_error",
    "newconnectionerror",
    "httpsconnectionpool",
    "failed to establish a new connection",
    "winerror 10013",
    "unable to connect to proxy",
    "remote end closed connection",
    "connection refused",
    "timeout",
)


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


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _int(value: Any, default: int = 0) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return int(float(parsed))


def _float(value: Any, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return float(parsed)


def _is_network_failure(*values: Any) -> bool:
    text = " ".join(str(value or "") for value in values).lower()
    return any(marker in text for marker in NETWORK_MARKERS)


def _bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[column].astype(str).str.lower().isin(["true", "1", "yes", "y"])


def _metric_count(coverage: pd.DataFrame, metric_names: list[str]) -> int:
    if coverage.empty or "metric_name" not in coverage.columns or "computable_count" not in coverage.columns:
        return 0
    counts: list[int] = []
    for metric in metric_names:
        rows = coverage[coverage["metric_name"].astype(str).eq(metric)]
        if not rows.empty:
            counts.append(_int(rows.iloc[0].get("computable_count"), 0))
    return max(counts) if counts else 0


def _coverage_usable_benchmark_count(coverage: pd.DataFrame, qa: dict[str, Any], cache_valid_count: int) -> int:
    if not coverage.empty:
        usable = _bool_series(coverage, "usable_as_benchmark")
        schema_valid = _bool_series(coverage, "schema_valid")
        fetch_success = _bool_series(coverage, "fetch_success")
        return int((usable & schema_valid & fetch_success).sum())
    index_summary = qa.get("data_layer", {}).get("index_data", {}) if isinstance(qa.get("data_layer"), dict) else {}
    if isinstance(index_summary, dict):
        return min(_int(index_summary.get("usable_benchmark_count"), 0), cache_valid_count)
    return 0


def _metric_computable_counts(metrics: pd.DataFrame, coverage: pd.DataFrame) -> dict[str, int]:
    tracking_error = 0
    relative_return = 0
    discount_premium = 0
    no_index_cache = 0
    missing_benchmark = 0
    if not metrics.empty:
        if "tracking_error_status" in metrics.columns:
            tracking_error = int(metrics["tracking_error_status"].astype(str).eq("ok").sum())
        relative_cols = [column for column in metrics.columns if column.startswith("relative_return_")]
        if relative_cols:
            relative_return = int(metrics[relative_cols].astype(str).apply(lambda row: row.str.strip().ne("").any(), axis=1).sum())
        if "discount_premium_status" in metrics.columns:
            discount_premium = int(metrics["discount_premium_status"].astype(str).eq("ok").sum())
        if "benchmark_status" in metrics.columns:
            status = metrics["benchmark_status"].astype(str)
            no_index_cache = int(status.eq("no_index_cache").sum())
            missing_benchmark = int(status.eq("missing_benchmark").sum())
    if tracking_error == 0:
        tracking_error = _metric_count(coverage, ["tracking_error"])
    if relative_return == 0:
        relative_return = _metric_count(coverage, ["relative_return_20d", "relative_return_60d", "relative_return_120d"])
    if discount_premium == 0:
        discount_premium = _metric_count(coverage, ["discount_premium"])
    return {
        "tracking_error_computable_count": tracking_error,
        "relative_return_computable_count": relative_return,
        "discount_premium_available_count": discount_premium,
        "no_index_cache_count": no_index_cache,
        "missing_benchmark_count": missing_benchmark,
    }


def _confirmed_mapping_mask(index_map: pd.DataFrame) -> pd.Series:
    if index_map.empty:
        return pd.Series(False, index=index_map.index)
    method = index_map.get("mapping_method", pd.Series("", index=index_map.index)).astype(str)
    confidence = pd.to_numeric(index_map.get("confidence", pd.Series(0, index=index_map.index)), errors="coerce").fillna(0.0)
    review = _bool_series(index_map, "requires_manual_review")
    usable = _bool_series(index_map, "usable_as_benchmark")
    code = index_map.get("tracking_index_code", pd.Series("", index=index_map.index)).astype(str).str.strip()
    return method.isin(HARD_MAPPING_METHODS) & (confidence >= 0.80) & (~review) & usable & code.ne("") & code.ne("unable_to_confirm")


def _index_cache_status(index_codes: list[str], index_cache_dir: str | Path) -> dict[str, dict[str, Any]]:
    cache_dir = Path(index_cache_dir)
    result: dict[str, dict[str, Any]] = {}
    for code in sorted({str(item).strip() for item in index_codes if str(item).strip() and str(item).strip() != "unable_to_confirm"}):
        path = cache_dir / f"{code}.csv"
        exists = path.exists()
        schema_valid = False
        reason = ""
        if exists:
            try:
                frame = pd.read_csv(path, dtype={"index_code": str}, encoding="utf-8-sig").fillna("")
                validate_index_cache_frame(frame, f"index cache {code}")
                schema_valid = not frame.empty
                if schema_valid and "index_code" in frame.columns:
                    codes = frame["index_code"].astype(str).str.strip()
                    schema_valid = bool(codes.eq(code).all())
                    if not schema_valid:
                        reason = "index_code column does not match cache filename"
            except Exception as exc:  # noqa: BLE001
                schema_valid = False
                reason = str(exc)
        result[code] = {
            "path": str(path),
            "exists": exists,
            "schema_valid": schema_valid,
            "reason": reason,
        }
    return result


def classify_007b_blocker(readiness_item: str, *, dependency: str = "", current_status: str = "") -> dict[str, str]:
    item = readiness_item.lower()
    dep = dependency.lower()
    status = current_status.lower()
    if "fake" in item:
        return {"blocker_type": "fake_benchmark_guard", "prerequisite_task": "remove invalid benchmark evidence; never use ETF price as benchmark"}
    if "schema" in item:
        return {"blocker_type": "schema_invalid", "prerequisite_task": "fix index cache schema and rerun compute-etf-metrics"}
    if "cache" in item or "benchmark" in item:
        return {"blocker_type": "index_cache_missing", "prerequisite_task": "run update-index-data after source diagnostics pass"}
    if "network" in item or "proxy" in item or "network" in status:
        return {"blocker_type": "source_network", "prerequisite_task": "fix network/proxy and rerun diagnose-index-source"}
    if "fetch" in item or "source" in dep:
        return {"blocker_type": "source_fetch_failed", "prerequisite_task": "rerun diagnose-index-source and update-index-data in a network-enabled environment"}
    if "mapping" in item:
        return {"blocker_type": "mapping_unconfirmed", "prerequisite_task": "confirm mapping manually or via trusted metadata"}
    if "overlap" in item:
        return {"blocker_type": "insufficient_overlap", "prerequisite_task": "wait for sufficient ETF and benchmark overlap after real cache exists"}
    if "tracking_error" in item or "relative_return" in item:
        return {"blocker_type": "metric_unavailable", "prerequisite_task": "compute ETF metrics only after schema-valid benchmark cache exists"}
    return {"blocker_type": "unknown", "prerequisite_task": "review 007B readiness evidence"}


def _row(
    *,
    readiness_item: str,
    current_status: str,
    passed: bool,
    blocking: bool,
    severity: str,
    threshold: str,
    actual_value: str,
    blocker_type: str,
    dependency: str,
    remediation_action: str,
    prerequisite_task: str,
    estimated_path: str,
    can_be_resolved_by_network: bool = False,
    can_be_resolved_by_index_update: bool = False,
    can_be_resolved_by_manual_mapping: bool = False,
    can_be_resolved_by_schema_fix: bool = False,
    notes: str = "",
) -> dict[str, Any]:
    if blocker_type not in INDEX_007B_BLOCKER_TYPES:
        blocker_type = "unknown"
    return {
        "readiness_item": readiness_item,
        "current_status": current_status,
        "passed": bool(passed),
        "blocking": bool(blocking),
        "severity": severity,
        "threshold": threshold,
        "actual_value": actual_value,
        "blocker_type": blocker_type,
        "dependency": dependency,
        "remediation_action": remediation_action,
        "prerequisite_task": prerequisite_task,
        "estimated_path": estimated_path,
        "can_be_resolved_by_network": bool(can_be_resolved_by_network),
        "can_be_resolved_by_index_update": bool(can_be_resolved_by_index_update),
        "can_be_resolved_by_manual_mapping": bool(can_be_resolved_by_manual_mapping),
        "can_be_resolved_by_schema_fix": bool(can_be_resolved_by_schema_fix),
        "notes": notes,
    }


def _coverage_by_code(coverage: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if coverage.empty or "tracking_index_code" not in coverage.columns:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for _, row in coverage.iterrows():
        code = str(row.get("tracking_index_code", "")).strip()
        if code and code not in rows:
            rows[code] = row.to_dict()
    return rows


def _metrics_by_symbol(metrics: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if metrics.empty or "symbol" not in metrics.columns:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for _, row in metrics.iterrows():
        symbol = str(row.get("symbol", "")).zfill(6)
        if symbol.strip("0") and symbol not in rows:
            rows[symbol] = row.to_dict()
    return rows


def _source_counts(index_coverage: pd.DataFrame, diagnostics: pd.DataFrame) -> dict[str, int]:
    fetch_success = int(_bool_series(index_coverage, "fetch_success").sum()) if not index_coverage.empty else 0
    schema_valid = int(_bool_series(index_coverage, "schema_valid").sum()) if not index_coverage.empty else 0
    usable_benchmark = int(_bool_series(index_coverage, "usable_as_benchmark").sum()) if not index_coverage.empty else 0

    network_failures = 0
    eastmoney_failures = 0
    csindex_success = 0
    for frame, code_column in [(index_coverage, "tracking_index_code"), (diagnostics, "index_code")]:
        if frame.empty:
            continue
        for _, row in frame.iterrows():
            family = str(row.get("source_family", "")).lower()
            success = _bool(row.get("fetch_success", row.get("call_success", False)))
            row_schema_valid = _bool(row.get("schema_valid", False))
            usable_source = _bool(row.get("usable_as_benchmark", row.get("usable_as_index_source", False)))
            network_failure = _is_network_failure(row.get("failure_type", ""), row.get("failure_reason", ""), row.get("notes", ""))
            if network_failure and not success:
                network_failures += 1
            if family == "eastmoney" and network_failure and not success:
                eastmoney_failures += 1
            if family == "csindex" and success and row_schema_valid and usable_source:
                csindex_success += 1
    return {
        "fetch_success_count": fetch_success,
        "schema_valid_coverage_count": schema_valid,
        "usable_benchmark_count": usable_benchmark,
        "network_failure_count": network_failures,
        "eastmoney_failure_count": eastmoney_failures,
        "csindex_success_count": csindex_success,
    }


def _fake_benchmark_violations(
    index_map: pd.DataFrame,
    metrics: pd.DataFrame,
    cache_status: dict[str, dict[str, Any]],
) -> list[str]:
    violations: list[str] = []
    if not index_map.empty:
        usable = _bool_series(index_map, "usable_as_benchmark")
        method = index_map.get("mapping_method", pd.Series("", index=index_map.index)).astype(str)
        invalid_usable = index_map[usable & ~method.isin(HARD_MAPPING_METHODS)]
        violations.extend(invalid_usable.get("symbol", pd.Series(dtype=str)).astype(str).str.zfill(6).tolist())
    if metrics.empty:
        return sorted(set(violations))
    map_by_symbol = {str(row.get("symbol", "")).zfill(6): row.to_dict() for _, row in index_map.iterrows()} if not index_map.empty else {}
    for _, row in metrics.iterrows():
        symbol = str(row.get("symbol", "")).zfill(6)
        benchmark_available = _bool(row.get("benchmark_available", False))
        if not benchmark_available:
            continue
        code = str(row.get("tracking_index_code", "")).strip()
        mapped = map_by_symbol.get(symbol, {})
        mapping_confirmed = (
            str(mapped.get("mapping_method", "")) in HARD_MAPPING_METHODS
            and _bool(mapped.get("usable_as_benchmark", False))
            and not _bool(mapped.get("requires_manual_review", False))
        )
        cache_valid = bool(cache_status.get(code, {}).get("schema_valid"))
        if symbol == code or not mapping_confirmed or not cache_valid:
            violations.append(symbol)
    return sorted(set(violations))


def build_007b_readiness_check(
    *,
    output_dir: str | Path = "output",
    index_cache_dir: str | Path = "data/index_cache",
    index_map: pd.DataFrame | None = None,
    index_coverage: pd.DataFrame | None = None,
    index_source_diagnostics: pd.DataFrame | None = None,
    etf_metrics: pd.DataFrame | None = None,
    etf_metrics_coverage: pd.DataFrame | None = None,
    qa_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    mapping = index_map if index_map is not None else _read_csv(output_path / "index_map.csv", dtype={"symbol": str, "tracking_index_code": str})
    coverage = index_coverage if index_coverage is not None else _read_csv(output_path / "index_data_coverage.csv", dtype={"tracking_index_code": str})
    diagnostics = index_source_diagnostics if index_source_diagnostics is not None else _read_csv(output_path / "index_source_diagnostics.csv", dtype={"index_code": str})
    metrics = etf_metrics if etf_metrics is not None else _read_csv(output_path / "etf_metrics.csv", dtype={"symbol": str, "tracking_index_code": str})
    metrics_coverage = etf_metrics_coverage if etf_metrics_coverage is not None else _read_csv(output_path / "etf_metrics_coverage.csv")
    qa = qa_report if qa_report is not None else _read_json(output_path / "qa_report.json")

    for column in INDEX_MAP_COLUMNS:
        if column not in mapping.columns:
            mapping[column] = ""
    confirmed = _confirmed_mapping_mask(mapping)
    confirmed_codes = mapping.loc[confirmed, "tracking_index_code"].astype(str).str.strip().tolist()
    cache_status = _index_cache_status(confirmed_codes, index_cache_dir)
    cache_exists_count = sum(1 for item in cache_status.values() if item["exists"])
    cache_valid_count = sum(1 for item in cache_status.values() if item["schema_valid"])
    source_counts = _source_counts(coverage, diagnostics)
    metric_counts = _metric_computable_counts(metrics, metrics_coverage)
    tracking_error_count = metric_counts["tracking_error_computable_count"]
    relative_return_count = metric_counts["relative_return_computable_count"]
    overlap_count = 0
    if not metrics.empty:
        overlap_count = int(_bool_series(metrics, "benchmark_available").sum())
    fake_violations = _fake_benchmark_violations(mapping, metrics, cache_status)
    network_blocked = source_counts["network_failure_count"] > 0 and cache_valid_count == 0
    hard_mapping_count = int(confirmed.sum()) if not mapping.empty else 0
    inferred_count = int(mapping.get("mapping_method", pd.Series("", index=mapping.index)).astype(str).isin(["name_inferred", "unable_to_confirm"]).sum()) if not mapping.empty else 0
    usable_benchmark_count = _coverage_usable_benchmark_count(coverage, qa, cache_valid_count)
    partial_cache_missing_count = max(0, len(cache_status) - cache_valid_count)
    unavailable_codes = sorted(
        code
        for code, item in cache_status.items()
        if not item.get("schema_valid")
    )

    rows = [
        _row(
            readiness_item="usable_benchmark_count",
            current_status="passed" if usable_benchmark_count > 0 else "blocked",
            passed=usable_benchmark_count > 0,
            blocking=usable_benchmark_count == 0,
            severity="high" if usable_benchmark_count == 0 else "info",
            threshold="> 0 schema-valid benchmark cache",
            actual_value=str(usable_benchmark_count),
            blocker_type="benchmark_dependency",
            dependency="index_map + index_data_coverage + data/index_cache",
            remediation_action="do not enter 007B until at least one confirmed benchmark has a schema-valid index cache",
            prerequisite_task="diagnose-index-source -> update-index-data -> compute-etf-metrics",
            estimated_path="network-enabled index update must produce usable_benchmark_count > 0",
            can_be_resolved_by_network=network_blocked,
            can_be_resolved_by_index_update=hard_mapping_count > 0,
            notes="counted directly from index_data_coverage rows where usable_as_benchmark, schema_valid, and fetch_success are true; qa_report is fallback only when coverage is missing",
        ),
        _row(
            readiness_item="index_cache_exists",
            current_status="passed" if cache_exists_count > 0 else "blocked",
            passed=cache_exists_count > 0,
            blocking=cache_exists_count == 0,
            severity="high" if cache_exists_count == 0 else "info",
            threshold="> 0 confirmed index cache files",
            actual_value=f"{cache_exists_count}/{len(cache_status)}",
            blocker_type="index_cache_missing",
            dependency="data/index_cache/{index_code}.csv",
            remediation_action="create real index cache via update-index-data; do not write placeholder cache",
            prerequisite_task="run update-index-data only after source diagnostics pass",
            estimated_path="confirmed index code has data/index_cache/{index_code}.csv",
            can_be_resolved_by_network=network_blocked,
            can_be_resolved_by_index_update=hard_mapping_count > 0,
            notes="missing cache blocks benchmark-relative metrics",
        ),
        _row(
            readiness_item="index_cache_schema_valid",
            current_status="passed" if cache_valid_count > 0 else "blocked",
            passed=cache_valid_count > 0,
            blocking=cache_valid_count == 0,
            severity="high" if cache_valid_count == 0 else "info",
            threshold="> 0 schema-valid confirmed index cache files",
            actual_value=f"{cache_valid_count}/{len(cache_status)}",
            blocker_type="schema_invalid" if cache_exists_count else "index_cache_missing",
            dependency="data/index_cache + data/schema.py",
            remediation_action="validate required OHLCV/index columns before ETF metrics consume the cache",
            prerequisite_task="fix schema or rerun update-index-data with normalized index output",
            estimated_path="validate_index_cache_frame passes for at least one confirmed benchmark",
            can_be_resolved_by_index_update=hard_mapping_count > 0,
            can_be_resolved_by_schema_fix=cache_exists_count > 0,
            notes="schema-valid means required columns, parseable dates, numeric OHLCV, and matching index_code",
        ),
        _row(
            readiness_item="index_data_fetch_success",
            current_status="passed" if source_counts["fetch_success_count"] > 0 else "blocked",
            passed=source_counts["fetch_success_count"] > 0,
            blocking=source_counts["fetch_success_count"] == 0 and cache_valid_count == 0,
            severity="high" if source_counts["fetch_success_count"] == 0 and cache_valid_count == 0 else "info",
            threshold="> 0 successful index fetch or existing valid cache",
            actual_value=str(source_counts["fetch_success_count"]),
            blocker_type="source_fetch_failed",
            dependency="index_data_coverage.csv + index_source_diagnostics.csv",
            remediation_action="rerun source diagnostics in network/proxy-enabled environment before updating index cache",
            prerequisite_task="diagnose-index-source",
            estimated_path="at least one trusted source fetches schema-valid index data",
            can_be_resolved_by_network=network_blocked,
            can_be_resolved_by_index_update=True,
            notes="fetch success alone is not enough; it must lead to real schema-valid cache",
        ),
        _row(
            readiness_item="benchmark_mapping_confidence",
            current_status="passed" if hard_mapping_count > 0 else "blocked",
            passed=hard_mapping_count > 0,
            blocking=hard_mapping_count == 0,
            severity="high" if hard_mapping_count == 0 else "warning" if inferred_count else "info",
            threshold="config_manual or metadata_exact, confidence >= 0.80, no manual review",
            actual_value=f"hard_mapping={hard_mapping_count}; inferred_or_unconfirmed={inferred_count}",
            blocker_type="mapping_unconfirmed",
            dependency="index_map.csv",
            remediation_action="use only confirmed benchmark mappings as hard 007B benchmarks",
            prerequisite_task="manual mapping or trusted metadata confirmation",
            estimated_path="name_inferred and unable_to_confirm remain non-hard mappings",
            can_be_resolved_by_manual_mapping=hard_mapping_count == 0 or inferred_count > 0,
            notes="name_inferred/unable_to_confirm must not unlock tracking error or relative return",
        ),
        _row(
            readiness_item="etf_to_benchmark_mapping_available",
            current_status="passed" if hard_mapping_count > 0 else "blocked",
            passed=hard_mapping_count > 0,
            blocking=hard_mapping_count == 0,
            severity="high" if hard_mapping_count == 0 else "info",
            threshold="> 0 confirmed ETF to benchmark mapping",
            actual_value=str(hard_mapping_count),
            blocker_type="manual_mapping",
            dependency="index_map.csv",
            remediation_action="confirm ETF-to-index mapping before any benchmark-relative calculation",
            prerequisite_task="update config/index_map.yaml or trusted metadata source, then rerun update-index-data",
            estimated_path="confirmed mapping exists before cache or metrics are considered",
            can_be_resolved_by_manual_mapping=True,
            notes="mapping availability is necessary but not sufficient without real index cache",
        ),
        _row(
            readiness_item="overlap_days_available",
            current_status="passed" if overlap_count > 0 else "blocked",
            passed=overlap_count > 0,
            blocking=overlap_count == 0,
            severity="high" if overlap_count == 0 else "info",
            threshold="> 0 ETF rows with real benchmark overlap",
            actual_value=str(overlap_count),
            blocker_type="insufficient_overlap",
            dependency="etf_metrics.csv",
            remediation_action="compute ETF metrics only after ETF cache and schema-valid benchmark cache overlap",
            prerequisite_task="compute-etf-metrics after index cache is valid",
            estimated_path="benchmark_available=True for at least one ETF with sufficient overlap",
            can_be_resolved_by_index_update=True,
            notes="do not infer overlap from ETF returns alone",
        ),
        _row(
            readiness_item="tracking_error_computable_count",
            current_status="passed" if tracking_error_count > 0 else "blocked",
            passed=tracking_error_count > 0,
            blocking=tracking_error_count == 0,
            severity="high" if tracking_error_count == 0 else "info",
            threshold="> 0",
            actual_value=str(tracking_error_count),
            blocker_type="metric_unavailable",
            dependency="etf_metrics_coverage.csv",
            remediation_action="tracking_error remains forbidden until real benchmark-relative metric coverage is positive",
            prerequisite_task="schema-valid index cache -> compute-etf-metrics",
            estimated_path="tracking_error computable_count > 0",
            can_be_resolved_by_index_update=True,
            notes="do not calculate real tracking_error in this precheck",
        ),
        _row(
            readiness_item="relative_return_computable_count",
            current_status="passed" if relative_return_count > 0 else "blocked",
            passed=relative_return_count > 0,
            blocking=relative_return_count == 0,
            severity="high" if relative_return_count == 0 else "info",
            threshold="> 0",
            actual_value=str(relative_return_count),
            blocker_type="metric_unavailable",
            dependency="etf_metrics_coverage.csv",
            remediation_action="relative_return remains forbidden until real benchmark-relative metric coverage is positive",
            prerequisite_task="schema-valid index cache -> compute-etf-metrics",
            estimated_path="any relative_return window computable_count > 0",
            can_be_resolved_by_index_update=True,
            notes="do not calculate real relative_return in this precheck",
        ),
        _row(
            readiness_item="index_source_network_available",
            current_status="blocked" if network_blocked else "passed",
            passed=not network_blocked,
            blocking=network_blocked,
            severity="high" if network_blocked else "info",
            threshold="no network/proxy blocker when no valid cache exists",
            actual_value=f"network_failures={source_counts['network_failure_count']}",
            blocker_type="source_network",
            dependency="index_source_diagnostics.csv",
            remediation_action="run diagnose-index-source in a network/proxy-enabled environment",
            prerequisite_task="fix proxy/network path before update-index-data",
            estimated_path="source diagnostics show at least one usable source before cache update",
            can_be_resolved_by_network=network_blocked,
            notes="network/proxy failures must not be bypassed with synthetic cache",
        ),
        _row(
            readiness_item="eastmoney_proxy_failure",
            current_status="blocked" if source_counts["eastmoney_failure_count"] > 0 and cache_valid_count == 0 else "passed",
            passed=not (source_counts["eastmoney_failure_count"] > 0 and cache_valid_count == 0),
            blocking=source_counts["eastmoney_failure_count"] > 0 and cache_valid_count == 0,
            severity="high" if source_counts["eastmoney_failure_count"] > 0 and cache_valid_count == 0 else "info",
            threshold="EastMoney failures resolved or alternate valid cache exists",
            actual_value=str(source_counts["eastmoney_failure_count"]),
            blocker_type="source_network",
            dependency="EastMoney-backed index source candidates",
            remediation_action="fix EastMoney proxy/network path or use another source that produces schema-valid cache",
            prerequisite_task="diagnose-index-source",
            estimated_path="update-index-data succeeds without relying on failed proxy path",
            can_be_resolved_by_network=source_counts["eastmoney_failure_count"] > 0,
            can_be_resolved_by_index_update=True,
            notes="EastMoney failures are source blockers, not permission to fake a benchmark",
        ),
        _row(
            readiness_item="csindex_available",
            current_status="passed" if source_counts["csindex_success_count"] > 0 else "warning",
            passed=source_counts["csindex_success_count"] > 0,
            blocking=False,
            severity="info" if source_counts["csindex_success_count"] > 0 else "warning",
            threshold="CSIndex candidate usable as source when it fetches schema-valid rows",
            actual_value=str(source_counts["csindex_success_count"]),
            blocker_type="source_fetch_failed" if source_counts["csindex_success_count"] == 0 else "benchmark_dependency",
            dependency="CSIndex source diagnostics",
            remediation_action="prefer CSIndex only when diagnostics and update-index-data produce schema-valid cache",
            prerequisite_task="diagnose-index-source -> update-index-data",
            estimated_path="CSIndex source success must materialize into data/index_cache",
            can_be_resolved_by_network=source_counts["csindex_success_count"] == 0,
            can_be_resolved_by_index_update=True,
            notes="source candidate availability does not by itself unlock 007B",
        ),
        _row(
            readiness_item="no_fake_benchmark_guard",
            current_status="passed" if not fake_violations else "blocked",
            passed=not fake_violations,
            blocking=bool(fake_violations),
            severity="high" if fake_violations else "info",
            threshold="0 violations",
            actual_value=str(len(fake_violations)),
            blocker_type="fake_benchmark_guard",
            dependency="index_map + etf_metrics + data/index_cache",
            remediation_action="stop any benchmark-relative calculation that lacks confirmed mapping and real schema-valid index cache",
            prerequisite_task="replace invalid evidence with confirmed mapping and real index cache",
            estimated_path="never use ETF own price as benchmark",
            notes="violations=" + ";".join(fake_violations[:10]) if fake_violations else "guard passed; ETF own prices are not treated as benchmark",
        ),
        _row(
            readiness_item="partial_index_cache_missing_count",
            current_status="warning" if partial_cache_missing_count > 0 else "passed",
            passed=partial_cache_missing_count == 0,
            blocking=False,
            severity="warning" if partial_cache_missing_count > 0 else "info",
            threshold="0 for full-scope 007B; warnings allowed for small-scope 007B",
            actual_value=str(partial_cache_missing_count),
            blocker_type="index_cache_missing",
            dependency="data/index_cache + index_data_coverage.csv",
            remediation_action="keep unavailable benchmark codes out of small-scope 007B until real schema-valid cache exists",
            prerequisite_task="rerun update-index-data only in a network-enabled environment; do not synthesize cache",
            estimated_path="full-scope requires every confirmed benchmark cache to validate",
            can_be_resolved_by_network=partial_cache_missing_count > 0,
            can_be_resolved_by_index_update=partial_cache_missing_count > 0,
            notes="blocks full-scope only; small-scope may use schema-valid cached benchmarks; unavailable_codes=" + ";".join(unavailable_codes),
        ),
        _row(
            readiness_item="missing_benchmark_count",
            current_status="warning" if metric_counts["missing_benchmark_count"] > 0 else "passed",
            passed=metric_counts["missing_benchmark_count"] == 0,
            blocking=False,
            severity="warning" if metric_counts["missing_benchmark_count"] > 0 else "info",
            threshold="0 for full-scope 007B; warnings allowed for small-scope 007B",
            actual_value=str(metric_counts["missing_benchmark_count"]),
            blocker_type="mapping_unconfirmed",
            dependency="etf_metrics.csv + index_map.csv",
            remediation_action="do not fill fake benchmarks for ETFs without confirmed mappings",
            prerequisite_task="manual mapping or trusted metadata confirmation for missing benchmark rows",
            estimated_path="full-scope requires confirmed benchmark mappings for the broader ETF universe",
            can_be_resolved_by_manual_mapping=metric_counts["missing_benchmark_count"] > 0,
            notes="blocks full-scope only; it does not block small-scope 007B for ETFs with confirmed benchmark metrics",
        ),
        _row(
            readiness_item="discount_premium_available_count",
            current_status="warning" if metric_counts["discount_premium_available_count"] == 0 else "passed",
            passed=metric_counts["discount_premium_available_count"] > 0,
            blocking=False,
            severity="warning" if metric_counts["discount_premium_available_count"] == 0 else "info",
            threshold="> 0 for discount/premium research; not required for benchmark-only small-scope 007B",
            actual_value=str(metric_counts["discount_premium_available_count"]),
            blocker_type="metric_unavailable",
            dependency="NAV or IOPV source",
            remediation_action="keep discount/premium disabled until NAV/IOPV source exists",
            prerequisite_task="separate NAV/IOPV source task",
            estimated_path="discount_premium_status == ok for at least one ETF",
            notes="not a blocker for small-scope benchmark-relative 007B; price-only data cannot produce discount/premium",
        ),
    ]
    return rows


def build_index_unlock_plan(
    *,
    output_dir: str | Path = "output",
    index_cache_dir: str | Path = "data/index_cache",
    index_map: pd.DataFrame | None = None,
    index_coverage: pd.DataFrame | None = None,
    index_source_diagnostics: pd.DataFrame | None = None,
    etf_metrics: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    mapping = index_map if index_map is not None else _read_csv(output_path / "index_map.csv", dtype={"symbol": str, "tracking_index_code": str})
    coverage = index_coverage if index_coverage is not None else _read_csv(output_path / "index_data_coverage.csv", dtype={"tracking_index_code": str})
    diagnostics = index_source_diagnostics if index_source_diagnostics is not None else _read_csv(output_path / "index_source_diagnostics.csv", dtype={"index_code": str})
    metrics = etf_metrics if etf_metrics is not None else _read_csv(output_path / "etf_metrics.csv", dtype={"symbol": str, "tracking_index_code": str})
    for column in INDEX_MAP_COLUMNS:
        if column not in mapping.columns:
            mapping[column] = ""
    coverage_by_code = _coverage_by_code(coverage)
    metrics_by_symbol = _metrics_by_symbol(metrics)
    confirmed = _confirmed_mapping_mask(mapping)
    cache_status = _index_cache_status(mapping.loc[confirmed, "tracking_index_code"].astype(str).tolist(), index_cache_dir)
    diag_network_by_code: dict[str, bool] = {}
    if not diagnostics.empty and "index_code" in diagnostics.columns:
        for code, group in diagnostics.groupby(diagnostics["index_code"].astype(str)):
            diag_network_by_code[code] = any(
                _is_network_failure(row.get("failure_type", ""), row.get("failure_reason", ""), row.get("notes", ""))
                for _, row in group.iterrows()
            )

    rows: list[dict[str, Any]] = []
    for idx, row in mapping.reset_index(drop=True).iterrows():
        symbol = str(row.get("symbol", "")).zfill(6)
        code = str(row.get("tracking_index_code", "")).strip()
        method = str(row.get("mapping_method", "")).strip()
        confidence = _float(row.get("confidence"), 0.0)
        requires_review = _bool(row.get("requires_manual_review", False))
        mapping_confirmed = bool(confirmed.iloc[idx]) if idx < len(confirmed) else False
        cache = cache_status.get(code, {"exists": False, "schema_valid": False, "reason": ""})
        cov = coverage_by_code.get(code, {})
        metric = metrics_by_symbol.get(symbol, {})
        fetch_success = _bool(cov.get("fetch_success", False)) and _bool(cov.get("schema_valid", False))
        network_issue = diag_network_by_code.get(code, False) or _is_network_failure(cov.get("failure_reason", ""), cov.get("notes", ""))

        if not mapping_confirmed:
            unlock_priority = "P3_manual_review" if method in {"name_inferred", "unable_to_confirm"} or requires_review else "P1_validate_mapping"
            required_action = "confirm ETF-to-benchmark mapping; name_inferred/unable_to_confirm cannot be a hard benchmark"
            benchmark_status = "mapping_unconfirmed"
        elif not cache["exists"]:
            unlock_priority = "P0_get_index_cache"
            required_action = "run diagnose-index-source, then update-index-data in network/proxy-enabled environment"
            benchmark_status = "no_index_cache"
        elif not cache["schema_valid"]:
            unlock_priority = "P1_fix_index_schema"
            required_action = "fix or regenerate index cache so required schema validates"
            benchmark_status = "schema_invalid"
        elif not fetch_success and network_issue:
            unlock_priority = "P2_wait_for_network"
            required_action = "rerun source diagnostics/update after network path is stable; keep existing valid cache guarded"
            benchmark_status = "source_needs_recheck"
        else:
            unlock_priority = "no_action"
            required_action = "confirmed mapping and valid cache are present; compute ETF metrics before 007B scope decision"
            benchmark_status = "benchmark_cache_ready"

        tracking_status = str(metric.get("tracking_error_status", "not_computed") or "not_computed")
        relative_available = any(str(metric.get(name, "")).strip() for name in ["relative_return_20d", "relative_return_60d", "relative_return_120d"])
        relative_status = "ok" if relative_available and _bool(metric.get("benchmark_available", False)) else str(metric.get("benchmark_status", "not_computed") or "not_computed")
        notes = []
        if network_issue:
            notes.append("network/proxy source action required")
        if method in {"name_inferred", "unable_to_confirm"}:
            notes.append("not a hard benchmark mapping")
        if cache.get("reason"):
            notes.append(str(cache["reason"]))
        if mapping_confirmed and not cache["schema_valid"]:
            notes.append("eligible for small-scope 007B only after real schema-valid benchmark cache exists")

        rows.append(
            {
                "symbol": symbol,
                "name": _text(row.get("etf_name") or row.get("name")),
                "tracking_index_code": code,
                "tracking_index_name": _text(row.get("tracking_index_name")),
                "mapping_method": method,
                "mapping_confidence": f"{confidence:.4f}",
                "usable_as_benchmark": bool(mapping_confirmed),
                "index_cache_exists": bool(cache["exists"]),
                "index_cache_schema_valid": bool(cache["schema_valid"]),
                "index_fetch_success": bool(fetch_success),
                "benchmark_status": benchmark_status,
                "etf_metrics_status": str(metric.get("metric_status", "not_computed") or "not_computed"),
                "tracking_error_status": tracking_status,
                "relative_return_status": relative_status,
                "required_action": required_action,
                "unlock_priority": unlock_priority,
                "eligible_for_007b_after_unlock": bool(mapping_confirmed),
                "notes": "; ".join(notes),
            }
        )
    return rows


def build_007b_readiness_summary(
    rows: list[dict[str, Any]] | pd.DataFrame,
    unlock_plan: list[dict[str, Any]] | pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    plan = pd.DataFrame(unlock_plan) if unlock_plan is not None else pd.DataFrame()
    if frame.empty:
        return []
    bool_col = lambda name: frame[name].astype(str).str.lower().isin(["true", "1", "yes"])
    blocking = frame[bool_col("blocking")]
    warnings = frame[~bool_col("blocking") & ~bool_col("passed")]
    summary = [
        {
            "summary_item": "blocking_items",
            "count": int(len(blocking)),
            "severity": "high" if not blocking.empty else "info",
            "finding": f"{len(blocking)} readiness item(s) block 007B entry.",
            "suggested_action": "; ".join(blocking["prerequisite_task"].head(3).astype(str).tolist()) if not blocking.empty else "no action",
            "examples": ";".join(blocking["readiness_item"].head(8).astype(str).tolist()),
            "notes": "007B readiness aggregation",
        },
        {
            "summary_item": "warning_items",
            "count": int(len(warnings)),
            "severity": "warning" if not warnings.empty else "info",
            "finding": f"{len(warnings)} readiness warning item(s) remain.",
            "suggested_action": "; ".join(warnings["prerequisite_task"].head(3).astype(str).tolist()) if not warnings.empty else "no action",
            "examples": ";".join(warnings["readiness_item"].head(8).astype(str).tolist()),
            "notes": "warnings do not override hard blockers",
        },
    ]
    for blocker_type, count in blocking["blocker_type"].value_counts().sort_index().items():
        subset = blocking[blocking["blocker_type"].eq(blocker_type)]
        summary.append(
            {
                "summary_item": f"blocker_type:{blocker_type}",
                "count": int(count),
                "severity": "high",
                "finding": f"{count} blocking item(s) have blocker_type={blocker_type}.",
                "suggested_action": "; ".join(subset["remediation_action"].head(3).astype(str).tolist()),
                "examples": ";".join(subset["readiness_item"].head(8).astype(str).tolist()),
                "notes": "grouped 007B blockers",
            }
        )
    if not plan.empty and "unlock_priority" in plan.columns:
        for priority, count in plan["unlock_priority"].value_counts().sort_index().items():
            subset = plan[plan["unlock_priority"].eq(priority)]
            summary.append(
                {
                    "summary_item": f"unlock_priority:{priority}",
                    "count": int(count),
                    "severity": "high" if str(priority).startswith("P0") else "warning" if str(priority).startswith("P1") else "info",
                    "finding": f"{count} ETF/index mapping row(s) have unlock_priority={priority}.",
                    "suggested_action": "; ".join(subset["required_action"].head(3).astype(str).tolist()),
                    "examples": ";".join(subset["symbol"].head(8).astype(str).tolist()),
                    "notes": "ETF-level unlock plan aggregation",
                }
            )
    return summary


def write_007b_readiness_report(
    rows: list[dict[str, Any]],
    unlock_plan: list[dict[str, Any]],
    *,
    report_path: str | Path = "output/index_007b_readiness.csv",
    unlock_plan_path: str | Path = "output/index_007b_unlock_plan.csv",
    summary_path: str | Path = "output/index_007b_readiness_summary.csv",
) -> tuple[Path, Path, Path]:
    report = Path(report_path)
    plan = Path(unlock_plan_path)
    summary = Path(summary_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    plan.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=INDEX_007B_READINESS_COLUMNS).to_csv(report, index=False, encoding="utf-8-sig")
    pd.DataFrame(unlock_plan, columns=INDEX_007B_UNLOCK_PLAN_COLUMNS).to_csv(plan, index=False, encoding="utf-8-sig")
    pd.DataFrame(build_007b_readiness_summary(rows, unlock_plan), columns=INDEX_007B_READINESS_SUMMARY_COLUMNS).to_csv(summary, index=False, encoding="utf-8-sig")
    return report, plan, summary


def summarize_007b_readiness(
    rows: list[dict[str, Any]] | pd.DataFrame | None = None,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    frame = pd.DataFrame(rows) if rows is not None else _read_csv(report_path or "output/index_007b_readiness.csv")
    if frame.empty:
        return {
            "index_007b_readiness_report": "output/index_007b_readiness.csv",
            "index_007b_unlock_plan_report": "output/index_007b_unlock_plan.csv",
            "index_007b_readiness_summary_report": "output/index_007b_readiness_summary.csv",
            "readiness_status": "not_run",
            "allowed_to_enter_007b": False,
            "allowed_to_enter_007b_scope": "blocked",
            "full_scope_available": False,
            "blocking_items": [],
            "warning_items": [],
            "usable_benchmark_count": 0,
            "index_cache_valid_count": 0,
            "tracking_error_computable_count": 0,
            "relative_return_computable_count": 0,
            "no_index_cache_count": 0,
            "missing_benchmark_count": 0,
            "discount_premium_available_count": 0,
            "top_blockers": [],
            "next_recommended_action": "run check-index-007b-readiness after index and ETF metric reports exist",
        }
    bool_col = lambda name: frame[name].astype(str).str.lower().isin(["true", "1", "yes"])
    blocking = frame[bool_col("blocking")]
    warnings = frame[~bool_col("blocking") & ~bool_col("passed")]
    top_fields = ["readiness_item", "blocker_type", "actual_value", "remediation_action", "prerequisite_task"]
    actual = {str(row["readiness_item"]): str(row["actual_value"]) for _, row in frame.iterrows()}
    allowed = blocking.empty
    full_scope_available = allowed and warnings.empty
    scope = "full_scope" if full_scope_available else "small_scope" if allowed else "blocked"
    if not blocking.empty:
        next_action = str(blocking.iloc[0].get("prerequisite_task") or blocking.iloc[0].get("remediation_action"))
    elif not warnings.empty:
        next_action = "enter 007B only for ETFs with confirmed benchmark metrics; keep warning rows out of full-scope work"
    else:
        next_action = "007B readiness clean; restrict initial work to ETFs with real schema-valid benchmark cache"
    return {
        "index_007b_readiness_report": "output/index_007b_readiness.csv",
        "index_007b_unlock_plan_report": "output/index_007b_unlock_plan.csv",
        "index_007b_readiness_summary_report": "output/index_007b_readiness_summary.csv",
        "readiness_status": "ready_full_scope" if full_scope_available else "ready_small_scope" if allowed else "blocked",
        "allowed_to_enter_007b": bool(allowed),
        "allowed_to_enter_007b_scope": scope,
        "full_scope_available": bool(full_scope_available),
        "blocking_items": blocking["readiness_item"].astype(str).tolist(),
        "warning_items": warnings["readiness_item"].astype(str).tolist(),
        "usable_benchmark_count": _int(actual.get("usable_benchmark_count"), 0),
        "index_cache_valid_count": _int(str(actual.get("index_cache_schema_valid", "0")).split("/", 1)[0], 0),
        "tracking_error_computable_count": _int(actual.get("tracking_error_computable_count"), 0),
        "relative_return_computable_count": _int(actual.get("relative_return_computable_count"), 0),
        "no_index_cache_count": _int(actual.get("partial_index_cache_missing_count"), 0),
        "missing_benchmark_count": _int(actual.get("missing_benchmark_count"), 0),
        "discount_premium_available_count": _int(actual.get("discount_premium_available_count"), 0),
        "top_blockers": blocking[top_fields].head(10).to_dict("records"),
        "next_recommended_action": next_action,
    }


def merge_007b_readiness_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    summary: dict[str, Any] | None = None,
) -> bool:
    path = Path(qa_report_path)
    if not path.exists():
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    output_dir = path.parent
    readiness_summary = summary or summarize_007b_readiness(report_path=output_dir / "index_007b_readiness.csv")
    data_layer = report.setdefault("data_layer", {})
    data_layer["index_007b_readiness"] = readiness_summary
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
