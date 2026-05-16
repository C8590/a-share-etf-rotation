from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


FACTOR_SCORE_REPORT_COLUMNS = [
    "symbol",
    "name",
    "total_score",
    "score_status",
    "enabled_factor_count",
    "used_factor_count",
    "skipped_factor_count",
    "failed_factor_count",
    "missing_required_factor_count",
    "rank",
    "computed_at",
    "notes",
]

FACTOR_SCORE_DETAIL_COLUMNS = [
    "symbol",
    "name",
    "factor_name",
    "raw_value",
    "normalized_value",
    "weight",
    "direction",
    "weighted_score",
    "factor_status",
    "missing_policy",
    "source",
    "reason",
]

FACTOR_SCORE_AUDIT_COLUMNS = [
    "audit_item",
    "status",
    "severity",
    "count",
    "ratio",
    "affected_symbols",
    "finding",
    "suggested_action",
    "notes",
]

FACTOR_SCORE_GATE_COLUMNS = [
    "gate_item",
    "status",
    "severity",
    "threshold",
    "actual_value",
    "passed",
    "blocking",
    "finding",
    "suggested_action",
    "notes",
]

FACTOR_STATUS_VALUES = {
    "used",
    "skipped_missing_optional",
    "missing_required",
    "disabled",
    "source_unavailable",
    "invalid_value",
    "insufficient_coverage",
    "unknown",
}
SCORE_STATUS_VALUES = {"ok", "unable_to_score", "missing_required_factor", "no_used_factors", "unknown"}
DIRECTIONS = {"higher_better", "lower_better"}
MISSING_POLICIES = {"skip", "fail", "neutral"}
SOURCES = {"strategy_signal", "etf_metrics", "etf_metadata", "data_quality"}
MISSING_MARKERS = {"", "unknown", "missing", "unable_to_confirm", "nan", "none", "nat", "<na>", "not_applicable"}
FACTOR_AUDIT_HIGH_ITEMS = {
    "source_unavailable_factor_count",
    "short_history_bias",
    "missing_benchmark_dependency",
    "missing_nav_iopv_dependency",
}
DEFAULT_FACTOR_SCORE_GATE_THRESHOLDS = {
    "min_computable_ratio": 0.80,
    "max_unable_to_score_ratio": 0.20,
    "min_score_computable_count": 30,
    "factor_coverage_minimum": 0.80,
}
BENCHMARK_DEPENDENT_FACTORS = {"tracking_error", "relative_return_60d"}
NAV_IOPV_DEPENDENT_FACTORS = {"discount_premium"}
METADATA_DEPENDENT_FACTORS = {"fund_size", "management_fee"}


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    enabled: bool
    weight: float
    direction: str
    required: bool
    missing_policy: str
    source: str
    field: str
    min_coverage_required: float = 0.0
    notes: str = ""
    status_field: str = ""


@dataclass(frozen=True)
class FactorScoreResult:
    symbol: str
    name: str
    total_score: float | None
    score_status: str
    enabled_factor_count: int
    used_factor_count: int
    skipped_factor_count: int
    failed_factor_count: int
    missing_required_factor_count: int
    notes: str


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


def _read_csv(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    return pd.read_csv(file_path, encoding="utf-8-sig", **kwargs).fillna("")


def load_factor_config(path: str | Path = "config/factor_score.yaml") -> list[FactorDefinition]:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    factor_items = raw.get("factors", raw if isinstance(raw, list) else []) or []
    definitions: list[FactorDefinition] = []
    for item in factor_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        definitions.append(
            FactorDefinition(
                name=name,
                enabled=_bool_value(item.get("enabled", False)),
                weight=float(pd.to_numeric(item.get("weight", 0.0), errors="coerce") or 0.0),
                direction=str(item.get("direction", "higher_better")).strip(),
                required=_bool_value(item.get("required", False)),
                missing_policy=str(item.get("missing_policy", "skip")).strip(),
                source=str(item.get("source", "")).strip(),
                field=str(item.get("field", name)).strip(),
                min_coverage_required=float(pd.to_numeric(item.get("min_coverage_required", 0.0), errors="coerce") or 0.0),
                notes=str(item.get("notes", "")).strip(),
                status_field=str(item.get("status_field", "")).strip(),
            )
        )
    validate_factor_config(definitions)
    return definitions


def validate_factor_config(definitions: list[FactorDefinition]) -> None:
    names: set[str] = set()
    for factor in definitions:
        if factor.name in names:
            raise ValueError(f"duplicate factor name: {factor.name}")
        names.add(factor.name)
        if factor.direction not in DIRECTIONS:
            raise ValueError(f"{factor.name}.direction must be one of {sorted(DIRECTIONS)}")
        if factor.missing_policy not in MISSING_POLICIES:
            raise ValueError(f"{factor.name}.missing_policy must be one of {sorted(MISSING_POLICIES)}")
        if factor.source not in SOURCES:
            raise ValueError(f"{factor.name}.source must be one of {sorted(SOURCES)}")
        if factor.weight < 0:
            raise ValueError(f"{factor.name}.weight must be non-negative")
        if not 0.0 <= factor.min_coverage_required <= 1.0:
            raise ValueError(f"{factor.name}.min_coverage_required must be between 0 and 1")
        if factor.missing_policy == "neutral" and factor.required:
            raise ValueError(f"{factor.name} cannot be required with missing_policy=neutral")


def normalize_factor(values: pd.Series, direction: str = "higher_better") -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    result = pd.Series(pd.NA, index=values.index, dtype="Float64")
    if valid.empty:
        return result
    min_value = float(valid.min())
    max_value = float(valid.max())
    if max_value == min_value:
        result.loc[valid.index] = 0.5
        return result
    if direction == "higher_better":
        result.loc[valid.index] = (valid - min_value) / (max_value - min_value)
    elif direction == "lower_better":
        result.loc[valid.index] = (max_value - valid) / (max_value - min_value)
    else:
        raise ValueError(f"Unsupported direction: {direction}")
    return result


def _latest_indicator_cache(path: str | Path) -> pd.DataFrame:
    frame = _read_csv(path, dtype={"symbol": str})
    if frame.empty or "symbol" not in frame.columns:
        return pd.DataFrame()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    if "signal_date" in frame.columns:
        latest = str(frame["signal_date"].astype(str).max())
        frame = frame[frame["signal_date"].astype(str).eq(latest)].copy()
    return frame.drop_duplicates("symbol", keep="last")


def load_factor_source_frames(
    *,
    strategy_signal_path: str | Path = "output/compare_signal.csv",
    indicator_cache_path: str | Path = "data/cache/indicator_cache.csv",
    etf_metrics_path: str | Path = "output/etf_metrics.csv",
    etf_metadata_path: str | Path = "output/etf_metadata.csv",
    data_quality_path: str | Path = "output/data_quality_report.csv",
) -> dict[str, pd.DataFrame]:
    compare = _read_csv(strategy_signal_path, dtype={"symbol": str})
    if not compare.empty and "symbol" in compare.columns:
        compare["symbol"] = compare["symbol"].astype(str).str.zfill(6)
        compare = compare.drop_duplicates("symbol", keep="first")
    indicators = _latest_indicator_cache(indicator_cache_path)
    if not compare.empty and not indicators.empty:
        strategy_signal = compare.set_index("symbol").combine_first(indicators.set_index("symbol")).reset_index()
    elif not compare.empty:
        strategy_signal = compare
    else:
        strategy_signal = indicators

    frames = {
        "strategy_signal": strategy_signal,
        "etf_metrics": _read_csv(etf_metrics_path, dtype={"symbol": str, "tracking_index_code": str}),
        "etf_metadata": _read_csv(etf_metadata_path, dtype={"symbol": str, "tracking_index_code": str}),
        "data_quality": _read_csv(data_quality_path, dtype={"symbol": str}),
    }
    for key, frame in list(frames.items()):
        if frame.empty or "symbol" not in frame.columns:
            frames[key] = pd.DataFrame()
            continue
        frame = frame.copy()
        frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
        frames[key] = frame.drop_duplicates("symbol", keep="first")
    return frames


def _build_universe(frames: dict[str, pd.DataFrame], symbols: str | list[str] | None, max_count: int | None) -> pd.DataFrame:
    parts = []
    for frame in frames.values():
        if not frame.empty and "symbol" in frame.columns:
            keep = ["symbol"] + [column for column in ["name", "rank"] if column in frame.columns]
            parts.append(frame[keep].copy())
    if not parts:
        return pd.DataFrame(columns=["symbol", "name"])
    universe = pd.concat(parts, ignore_index=True).fillna("")
    universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    if "rank" not in universe.columns:
        universe["rank"] = pd.NA
    universe["rank_sort"] = pd.to_numeric(universe["rank"], errors="coerce")
    universe = universe.sort_values(["rank_sort", "symbol"], na_position="last").drop_duplicates("symbol", keep="first")
    if symbols:
        requested = [item.strip().zfill(6) for item in symbols.split(",")] if isinstance(symbols, str) else [str(item).zfill(6) for item in symbols]
        requested = [item for item in requested if item]
        universe = universe[universe["symbol"].isin(requested)].copy()
    universe = universe.sort_values("symbol").reset_index(drop=True)
    if max_count is not None and int(max_count) > 0:
        universe = universe.head(int(max_count)).copy()
    if "name" not in universe.columns:
        universe["name"] = universe["symbol"]
    universe["name"] = universe["name"].where(universe["name"].astype(str).str.strip().ne(""), universe["symbol"])
    return universe[["symbol", "name"]].reset_index(drop=True)


def _source_index(frames: dict[str, pd.DataFrame], source: str) -> pd.DataFrame:
    frame = frames.get(source, pd.DataFrame())
    if frame.empty or "symbol" not in frame.columns:
        return pd.DataFrame()
    return frame.set_index("symbol", drop=False)


def _factor_coverage(factor: FactorDefinition, source_frame: pd.DataFrame, symbols: pd.Series) -> float:
    if source_frame.empty or factor.field not in source_frame.columns:
        return 0.0
    indexed = source_frame.set_index("symbol", drop=False)
    values = indexed.reindex(symbols.astype(str).str.zfill(6))[factor.field]
    return round(float(values.map(_is_present).mean()), 4) if len(values) else 0.0


def _missing_status_from_source(factor: FactorDefinition, source_row: pd.Series | None) -> tuple[str, str]:
    if source_row is not None and factor.status_field and factor.status_field in source_row.index:
        status = str(source_row.get(factor.status_field, "")).strip()
        if status and status != "ok":
            return "source_unavailable", f"{factor.status_field}={status}"
    if factor.required or factor.missing_policy == "fail":
        return "missing_required", "required factor is missing"
    if factor.missing_policy == "neutral":
        return "used", "missing value handled as neutral by explicit config"
    return "skipped_missing_optional", "optional factor is missing"


def compute_factor_score(
    symbol: str,
    name: str,
    factor: FactorDefinition,
    source_row: pd.Series | None,
    normalized_value: Any,
    coverage_ratio: float = 1.0,
) -> dict[str, Any]:
    base = {
        "symbol": str(symbol).zfill(6),
        "name": name,
        "factor_name": factor.name,
        "raw_value": "",
        "normalized_value": "",
        "weight": factor.weight,
        "direction": factor.direction,
        "weighted_score": "",
        "factor_status": "unknown",
        "missing_policy": factor.missing_policy,
        "source": factor.source,
        "reason": "",
    }
    if not factor.enabled:
        return {**base, "factor_status": "disabled", "reason": "factor disabled by config"}
    if source_row is None:
        status, reason = _missing_status_from_source(factor, source_row)
        return {**base, "factor_status": "source_unavailable" if status == "skipped_missing_optional" else status, "reason": f"source unavailable: {factor.source}"}
    if factor.field not in source_row.index:
        status, _reason = _missing_status_from_source(factor, source_row)
        return {**base, "factor_status": "source_unavailable" if status == "skipped_missing_optional" else status, "reason": f"field unavailable: {factor.field}"}
    if coverage_ratio < factor.min_coverage_required:
        return {
            **base,
            "factor_status": "insufficient_coverage",
            "reason": f"coverage {coverage_ratio:.4f} < required {factor.min_coverage_required:.4f}",
        }
    raw_value = source_row.get(factor.field, "")
    base["raw_value"] = raw_value
    if not _is_present(raw_value):
        status, reason = _missing_status_from_source(factor, source_row)
        if status == "used":
            normalized = 0.5
            return {**base, "normalized_value": normalized, "weighted_score": normalized * factor.weight, "factor_status": "used", "reason": reason}
        return {**base, "factor_status": status, "reason": reason}
    parsed = pd.to_numeric(raw_value, errors="coerce")
    if pd.isna(parsed):
        return {**base, "factor_status": "invalid_value", "reason": f"non-numeric value: {raw_value}"}
    normalized = pd.to_numeric(normalized_value, errors="coerce")
    if pd.isna(normalized):
        return {**base, "factor_status": "invalid_value", "reason": "normalization unavailable"}
    return {
        **base,
        "normalized_value": round(float(normalized), 10),
        "weighted_score": round(float(normalized) * factor.weight, 10),
        "factor_status": "used",
        "reason": "ok",
    }


def compute_multi_factor_score(
    frames: dict[str, pd.DataFrame],
    definitions: list[FactorDefinition],
    *,
    symbols: str | list[str] | None = None,
    max_count: int | None = None,
    computed_at: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    validate_factor_config(definitions)
    universe = _build_universe(frames, symbols, max_count)
    computed = computed_at or _now_text()
    if universe.empty:
        return pd.DataFrame(columns=FACTOR_SCORE_REPORT_COLUMNS), pd.DataFrame(columns=FACTOR_SCORE_DETAIL_COLUMNS)

    normalized_by_factor: dict[str, pd.Series] = {}
    coverage_by_factor: dict[str, float] = {}
    for factor in definitions:
        source_frame = frames.get(factor.source, pd.DataFrame())
        coverage_by_factor[factor.name] = _factor_coverage(factor, source_frame, universe["symbol"]) if factor.enabled else 0.0
        if not factor.enabled or source_frame.empty or factor.field not in source_frame.columns:
            normalized_by_factor[factor.name] = pd.Series(pd.NA, index=universe["symbol"].astype(str).str.zfill(6), dtype="Float64")
            continue
        indexed = source_frame.set_index("symbol", drop=False)
        values = indexed.reindex(universe["symbol"].astype(str).str.zfill(6))[factor.field]
        normalized_by_factor[factor.name] = normalize_factor(values, factor.direction)

    detail_rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    enabled_factor_count = sum(1 for factor in definitions if factor.enabled)
    source_indexes = {source: _source_index(frames, source) for source in SOURCES}
    for _, item in universe.iterrows():
        symbol = str(item["symbol"]).zfill(6)
        name = str(item.get("name") or symbol)
        for factor in definitions:
            source_index = source_indexes.get(factor.source, pd.DataFrame())
            source_row = source_index.loc[symbol] if not source_index.empty and symbol in source_index.index else None
            normalized_value = normalized_by_factor[factor.name].get(symbol, pd.NA)
            detail_rows.append(
                compute_factor_score(
                    symbol,
                    name,
                    factor,
                    source_row,
                    normalized_value,
                    coverage_ratio=coverage_by_factor[factor.name],
                )
            )

        symbol_details = pd.DataFrame([row for row in detail_rows if row["symbol"] == symbol])
        used = symbol_details[symbol_details["factor_status"].eq("used")].copy()
        failed = symbol_details[symbol_details["factor_status"].isin(["missing_required", "invalid_value", "insufficient_coverage"])]
        missing_required_count = int(symbol_details["factor_status"].eq("missing_required").sum())
        used_weight = pd.to_numeric(used["weight"], errors="coerce").sum() if not used.empty else 0.0
        weighted_sum = pd.to_numeric(used["weighted_score"], errors="coerce").sum() if not used.empty else 0.0
        total_score: Any = ""
        if missing_required_count:
            status = "missing_required_factor"
            notes = "one or more required factors are missing"
        elif used.empty or used_weight <= 0:
            status = "no_used_factors"
            notes = "no enabled factor produced a usable score"
        else:
            status = "ok"
            total_score = round(float(weighted_sum) / float(used_weight), 10)
            notes = explain_factor_score(symbol_details)
        report_rows.append(
            {
                "symbol": symbol,
                "name": name,
                "total_score": total_score,
                "score_status": status,
                "enabled_factor_count": enabled_factor_count,
                "used_factor_count": int(len(used)),
                "skipped_factor_count": int(symbol_details["factor_status"].isin(["skipped_missing_optional", "source_unavailable", "disabled"]).sum()),
                "failed_factor_count": int(len(failed)),
                "missing_required_factor_count": missing_required_count,
                "rank": "",
                "computed_at": computed,
                "notes": notes,
            }
        )
    report = pd.DataFrame(report_rows, columns=FACTOR_SCORE_REPORT_COLUMNS)
    ok_mask = report["score_status"].eq("ok")
    if ok_mask.any():
        ranks = pd.to_numeric(report.loc[ok_mask, "total_score"], errors="coerce").rank(ascending=False, method="first").astype(int)
        report.loc[ok_mask, "rank"] = ranks.astype(str)
    detail = pd.DataFrame(detail_rows, columns=FACTOR_SCORE_DETAIL_COLUMNS)
    return report, detail


def explain_factor_score(details: pd.DataFrame) -> str:
    if details.empty:
        return "no factor details"
    used = details[details["factor_status"].eq("used")]["factor_name"].astype(str).tolist()
    skipped = details[~details["factor_status"].eq("used")]
    skipped_parts = [
        f"{row['factor_name']}:{row['factor_status']}"
        for _, row in skipped.iterrows()
        if str(row.get("factor_status", "")) != "disabled"
    ]
    parts = []
    if used:
        parts.append("used=" + ",".join(used))
    if skipped_parts:
        parts.append("skipped=" + ",".join(skipped_parts[:8]))
    return "; ".join(parts) if parts else "no enabled factor used"


def compute_factor_score_reports(
    *,
    config_path: str | Path = "config/factor_score.yaml",
    symbols: str | list[str] | None = None,
    max_count: int | None = 50,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    definitions = load_factor_config(config_path)
    frames = load_factor_source_frames()
    return compute_multi_factor_score(frames, definitions, symbols=symbols, max_count=max_count)


def write_factor_score_reports(
    report: pd.DataFrame,
    detail: pd.DataFrame,
    *,
    report_path: str | Path = "output/factor_score_report.csv",
    detail_path: str | Path = "output/factor_score_detail.csv",
) -> tuple[Path, Path]:
    out_report = Path(report_path)
    out_detail = Path(detail_path)
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_detail.parent.mkdir(parents=True, exist_ok=True)
    report[FACTOR_SCORE_REPORT_COLUMNS].to_csv(out_report, index=False, encoding="utf-8-sig")
    detail[FACTOR_SCORE_DETAIL_COLUMNS].to_csv(out_detail, index=False, encoding="utf-8-sig")
    return out_report, out_detail


def _ratio(count: int | float, total: int | float) -> float:
    return round(float(count) / float(total), 4) if float(total or 0) else 0.0


def _symbol_list(frame: pd.DataFrame, limit: int = 10) -> str:
    if frame.empty or "symbol" not in frame.columns:
        return ""
    values = frame["symbol"].astype(str).str.zfill(6).drop_duplicates().head(limit).tolist()
    return ",".join(values)


def _status_counts_text(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    counts = frame[column].astype(str).replace("", "blank").value_counts().head(5).to_dict()
    return "; ".join(f"{key}={int(value)}" for key, value in counts.items())


def _audit_row(
    audit_item: str,
    status: str,
    severity: str,
    count: int,
    ratio: float,
    affected_symbols: str,
    finding: str,
    suggested_action: str,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "audit_item": audit_item,
        "status": status,
        "severity": severity,
        "count": int(count),
        "ratio": round(float(ratio), 4),
        "affected_symbols": affected_symbols,
        "finding": finding,
        "suggested_action": suggested_action,
        "notes": notes,
    }


def build_factor_score_audit(
    report: pd.DataFrame,
    detail: pd.DataFrame,
    *,
    etf_metrics: pd.DataFrame | None = None,
    etf_metadata: pd.DataFrame | None = None,
    data_quality: pd.DataFrame | None = None,
    example_limit: int = 10,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    report = report.copy().fillna("")
    detail = detail.copy().fillna("")
    metrics = (etf_metrics.copy().fillna("") if etf_metrics is not None else pd.DataFrame())
    metadata = (etf_metadata.copy().fillna("") if etf_metadata is not None else pd.DataFrame())
    quality = (data_quality.copy().fillna("") if data_quality is not None else pd.DataFrame())
    for frame in [report, detail, metrics, metadata, quality]:
        if not frame.empty and "symbol" in frame.columns:
            frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    report_symbols = set(report["symbol"].astype(str).str.zfill(6).tolist()) if "symbol" in report.columns else set()
    if report_symbols:
        if not metrics.empty and "symbol" in metrics.columns:
            metrics = metrics[metrics["symbol"].isin(report_symbols)].copy()
        if not metadata.empty and "symbol" in metadata.columns:
            metadata = metadata[metadata["symbol"].isin(report_symbols)].copy()
        if not quality.empty and "symbol" in quality.columns:
            quality = quality[quality["symbol"].isin(report_symbols)].copy()

    total_symbols = int(len(report))
    ok = report[report.get("score_status", pd.Series(dtype=str)).astype(str).eq("ok")].copy()
    unable = report[~report.get("score_status", pd.Series(dtype=str)).astype(str).eq("ok")].copy()
    computable_count = int(len(ok))
    unable_count = int(len(unable))

    rows.append(
        _audit_row(
            "score_computable_count",
            "ok" if computable_count else "blocked",
            "info" if computable_count else "high",
            computable_count,
            _ratio(computable_count, total_symbols),
            _symbol_list(ok, example_limit),
            f"{computable_count} of {total_symbols} symbols have factor_score score_status=ok.",
            "Use this only as a research coverage snapshot until blocking dependencies are fixed.",
            "total_symbols=" + str(total_symbols),
        )
    )
    rows.append(
        _audit_row(
            "unable_to_score_count",
            "warning" if unable_count else "ok",
            "warning" if unable_count else "info",
            unable_count,
            _ratio(unable_count, total_symbols),
            _symbol_list(unable, example_limit),
            f"{unable_count} of {total_symbols} symbols cannot be scored with current usable factors.",
            "Inspect factor_score_detail.csv before treating the report as a candidate pool.",
            _status_counts_text(unable, "score_status"),
        )
    )

    missing_required = detail[detail.get("factor_status", pd.Series(dtype=str)).astype(str).eq("missing_required")].copy()
    rows.append(
        _audit_row(
            "missing_required_factor_count",
            "blocked" if not missing_required.empty else "ok",
            "high" if not missing_required.empty else "info",
            int(len(missing_required)),
            _ratio(len(missing_required), len(detail)),
            _symbol_list(missing_required, example_limit),
            f"{len(missing_required)} symbol-factor rows are missing required factors.",
            "Do not score affected ETFs until required inputs are present or the config is changed intentionally.",
            _status_counts_text(missing_required, "factor_name"),
        )
    )

    optional_skipped = detail[detail.get("factor_status", pd.Series(dtype=str)).astype(str).eq("skipped_missing_optional")].copy()
    rows.append(
        _audit_row(
            "optional_factor_skipped_count",
            "warning" if not optional_skipped.empty else "ok",
            "warning" if not optional_skipped.empty else "info",
            int(len(optional_skipped)),
            _ratio(len(optional_skipped), len(detail)),
            _symbol_list(optional_skipped, example_limit),
            f"{len(optional_skipped)} optional symbol-factor rows were skipped instead of filled.",
            "Keep optional missing values out of the denominator; add sources before using them in decisions.",
            _status_counts_text(optional_skipped, "factor_name"),
        )
    )

    source_unavailable = detail[detail.get("factor_status", pd.Series(dtype=str)).astype(str).eq("source_unavailable")].copy()
    source_factor_names: list[str] = []
    if "factor_name" in detail.columns:
        for factor_name, factor_rows in detail.groupby("factor_name", sort=True):
            statuses = factor_rows["factor_status"].astype(str)
            if not factor_rows.empty and statuses.eq("source_unavailable").all():
                source_factor_names.append(str(factor_name))
    all_factor_names = sorted(detail["factor_name"].astype(str).unique().tolist()) if "factor_name" in detail.columns else []
    rows.append(
        _audit_row(
            "source_unavailable_factor_count",
            "blocked" if source_factor_names else "ok",
            "high" if source_factor_names else "info",
            len(source_factor_names),
            _ratio(len(source_factor_names), len(all_factor_names)),
            _symbol_list(source_unavailable, example_limit),
            f"{len(source_factor_names)} configured factor(s) are fully source_unavailable: {', '.join(source_factor_names) or 'none'}.",
            "Fix source dependencies before promoting these factors from audit evidence into strategy scoring.",
            f"source_unavailable_rows={len(source_unavailable)}; " + _status_counts_text(source_unavailable, "reason"),
        )
    )

    disabled = detail[detail.get("factor_status", pd.Series(dtype=str)).astype(str).eq("disabled")].copy()
    disabled_factor_names = sorted(disabled["factor_name"].astype(str).unique().tolist()) if not disabled.empty else []
    rows.append(
        _audit_row(
            "disabled_factor_count",
            "warning" if disabled_factor_names else "ok",
            "warning" if disabled_factor_names else "info",
            len(disabled_factor_names),
            _ratio(len(disabled_factor_names), len(all_factor_names)),
            _symbol_list(disabled, example_limit),
            f"{len(disabled_factor_names)} configured factor(s) are disabled: {', '.join(disabled_factor_names) or 'none'}.",
            "Leave disabled until the source fields are independently confirmed with high coverage.",
            f"disabled_rows={len(disabled)}",
        )
    )

    if "factor_name" in detail.columns:
        for factor_name, factor_rows in detail.groupby("factor_name", sort=True):
            used_rows = factor_rows[factor_rows["factor_status"].astype(str).eq("used")]
            source_rows = factor_rows[factor_rows["factor_status"].astype(str).eq("source_unavailable")]
            disabled_rows = factor_rows[factor_rows["factor_status"].astype(str).eq("disabled")]
            status = "ok"
            severity = "info"
            if len(source_rows) == len(factor_rows) and not factor_rows.empty:
                status = "blocked"
                severity = "high"
            elif not disabled_rows.empty:
                status = "disabled"
                severity = "warning"
            elif used_rows.empty:
                status = "warning"
                severity = "warning"
            elif len(used_rows) < total_symbols:
                status = "warning"
                severity = "warning"
            rows.append(
                _audit_row(
                    "factor_coverage_by_name",
                    status,
                    severity,
                    int(len(used_rows)),
                    _ratio(len(used_rows), total_symbols),
                    _symbol_list(factor_rows[~factor_rows["factor_status"].astype(str).eq("used")], example_limit),
                    f"{factor_name} is used for {len(used_rows)} of {total_symbols} symbols.",
                    "Treat low-coverage factors as explanatory only until coverage is improved.",
                    _status_counts_text(factor_rows, "factor_status"),
                )
            )

    if not ok.empty:
        rank_numeric = pd.to_numeric(ok.get("rank", pd.Series(dtype=str)), errors="coerce")
        score_numeric = pd.to_numeric(ok.get("total_score", pd.Series(dtype=str)), errors="coerce")
        top = ok.assign(_rank=rank_numeric).sort_values(["_rank", "symbol"]).head(example_limit)
        bottom = ok.assign(_score=score_numeric).sort_values(["_score", "symbol"]).head(example_limit)
        rows.append(
            _audit_row(
                "top_score_symbols",
                "info",
                "info",
                int(len(top)),
                _ratio(len(top), computable_count),
                _symbol_list(top, example_limit),
                "Top ranked symbols are sorted by the current factor_score total_score.",
                "Read together with used_factor_count and missing-source rows; do not use as a recommendation list.",
                "",
            )
        )
        rows.append(
            _audit_row(
                "bottom_score_symbols",
                "info",
                "info",
                int(len(bottom)),
                _ratio(len(bottom), computable_count),
                _symbol_list(bottom, example_limit),
                "Bottom ranked computable symbols are the weakest among currently scoreable ETFs.",
                "Use as diagnostic evidence for factor behavior, not as a trading instruction.",
                "",
            )
        )
    else:
        rows.append(_audit_row("top_score_symbols", "blocked", "high", 0, 0.0, "", "No scoreable symbols exist.", "Fix source coverage first."))
        rows.append(_audit_row("bottom_score_symbols", "blocked", "high", 0, 0.0, "", "No scoreable symbols exist.", "Fix source coverage first."))

    completeness_rows = detail[detail.get("factor_name", pd.Series(dtype=str)).astype(str).eq("data_completeness")].copy()
    completeness_used = completeness_rows[completeness_rows.get("factor_status", pd.Series(dtype=str)).astype(str).eq("used")]
    completeness_status = "warning" if len(completeness_used) == computable_count and computable_count < total_symbols else "ok"
    rows.append(
        _audit_row(
            "data_completeness_bias",
            completeness_status,
            "warning" if completeness_status == "warning" else "info",
            int(len(completeness_used)),
            _ratio(len(completeness_used), total_symbols),
            _symbol_list(completeness_used, example_limit),
            f"data_completeness participates in {len(completeness_used)} score(s), matching {computable_count} computable symbols.",
            "Do not interpret the current score as purely return/risk based; separate coverage gates from alpha factors before strategy use.",
            _status_counts_text(completeness_rows, "factor_status"),
        )
    )

    if not quality.empty and "rows" in quality.columns:
        quality_joined = report.merge(
            quality[["symbol", "rows", "status", "primary_failure_type"]],
            on="symbol",
            how="left",
            suffixes=("", "_quality"),
        )
        quality_joined["rows_numeric"] = pd.to_numeric(quality_joined["rows"], errors="coerce")
        short_history = quality_joined[
            quality_joined["score_status"].astype(str).eq("ok")
            & (
                quality_joined["rows_numeric"].lt(250)
                | quality_joined["primary_failure_type"].astype(str).eq("insufficient_rows")
            )
        ]
        short_ratio = _ratio(len(short_history), computable_count)
        short_severity = "high" if short_ratio >= 0.5 and len(short_history) else ("warning" if len(short_history) else "info")
        rows.append(
            _audit_row(
                "short_history_bias",
                "blocked" if short_severity == "high" else ("warning" if short_severity == "warning" else "ok"),
                short_severity,
                int(len(short_history)),
                short_ratio,
                _symbol_list(short_history, example_limit),
                f"{len(short_history)} of {computable_count} scoreable symbols have short history or insufficient_rows quality flags.",
                "Require enough price history before allowing scoreable symbols into a candidate strategy.",
                _status_counts_text(short_history, "primary_failure_type"),
            )
        )
    else:
        rows.append(
            _audit_row(
                "short_history_bias",
                "warning",
                "warning",
                0,
                0.0,
                "",
                "data_quality_report.csv is unavailable, so short-history bias cannot be audited.",
                "Run qa-check or data quality checks before strategy promotion.",
            )
        )

    benchmark_factor_rows = source_unavailable[source_unavailable["factor_name"].astype(str).isin(["tracking_error", "relative_return_60d"])].copy()
    benchmark_symbols = benchmark_factor_rows["symbol"].drop_duplicates() if not benchmark_factor_rows.empty else pd.Series(dtype=str)
    benchmark_notes = _status_counts_text(metrics, "benchmark_status") if not metrics.empty else _status_counts_text(benchmark_factor_rows, "reason")
    rows.append(
        _audit_row(
            "missing_benchmark_dependency",
            "blocked" if len(benchmark_symbols) else "ok",
            "high" if len(benchmark_symbols) else "info",
            int(len(benchmark_symbols)),
            _ratio(len(benchmark_symbols), total_symbols),
            _symbol_list(benchmark_factor_rows, example_limit),
            f"Benchmark-dependent factors are unavailable for {len(benchmark_symbols)} symbol(s).",
            "Confirm benchmark mapping and schema-valid index cache before using tracking_error or relative_return_60d.",
            benchmark_notes,
        )
    )

    nav_rows = source_unavailable[source_unavailable["factor_name"].astype(str).eq("discount_premium")].copy()
    nav_symbols = nav_rows["symbol"].drop_duplicates() if not nav_rows.empty else pd.Series(dtype=str)
    rows.append(
        _audit_row(
            "missing_nav_iopv_dependency",
            "blocked" if len(nav_symbols) else "ok",
            "high" if len(nav_symbols) else "info",
            int(len(nav_symbols)),
            _ratio(len(nav_symbols), total_symbols),
            _symbol_list(nav_rows, example_limit),
            f"discount_premium is unavailable for {len(nav_symbols)} symbol(s).",
            "Add a trusted NAV or IOPV source; exchange prices alone cannot produce discount/premium.",
            _status_counts_text(metrics, "discount_premium_status") if not metrics.empty else _status_counts_text(nav_rows, "reason"),
        )
    )

    metadata_factor_rows = disabled[disabled["factor_name"].astype(str).isin(["fund_size", "management_fee"])].copy()
    metadata_low = pd.DataFrame()
    if not metadata.empty:
        metadata_fields = [column for column in ["fund_size", "management_fee"] if column in metadata.columns]
        if metadata_fields:
            mask = pd.Series(False, index=metadata.index)
            for field in metadata_fields:
                mask = mask | ~metadata[field].map(_is_present)
            metadata_low = metadata[mask].copy()
    rows.append(
        _audit_row(
            "metadata_low_coverage_dependency",
            "warning" if not metadata_factor_rows.empty or not metadata_low.empty else "ok",
            "warning" if not metadata_factor_rows.empty or not metadata_low.empty else "info",
            int(len(metadata_low) if not metadata_low.empty else len(metadata_factor_rows["symbol"].drop_duplicates()) if not metadata_factor_rows.empty else 0),
            _ratio(len(metadata_low) if not metadata_low.empty else len(metadata_factor_rows["symbol"].drop_duplicates()) if not metadata_factor_rows.empty else 0, total_symbols),
            _symbol_list(metadata_low if not metadata_low.empty else metadata_factor_rows, example_limit),
            "fund_size and management_fee are disabled or low coverage in current metadata.",
            "Keep size and fee out of scoring until metadata sources confirm these fields with high coverage.",
            _status_counts_text(metadata, "data_quality_status") if not metadata.empty else _status_counts_text(metadata_factor_rows, "factor_name"),
        )
    )

    return pd.DataFrame(rows, columns=FACTOR_SCORE_AUDIT_COLUMNS)


def write_factor_score_audit(audit: pd.DataFrame, *, audit_path: str | Path = "output/factor_score_audit.csv") -> Path:
    out_audit = Path(audit_path)
    out_audit.parent.mkdir(parents=True, exist_ok=True)
    audit[FACTOR_SCORE_AUDIT_COLUMNS].to_csv(out_audit, index=False, encoding="utf-8-sig")
    return out_audit


def build_factor_score_audit_from_files(
    *,
    report_path: str | Path = "output/factor_score_report.csv",
    detail_path: str | Path = "output/factor_score_detail.csv",
    etf_metrics_path: str | Path = "output/etf_metrics.csv",
    etf_metadata_path: str | Path = "output/etf_metadata.csv",
    data_quality_path: str | Path = "output/data_quality_report.csv",
) -> pd.DataFrame:
    return build_factor_score_audit(
        _read_csv(report_path, dtype={"symbol": str}),
        _read_csv(detail_path, dtype={"symbol": str}),
        etf_metrics=_read_csv(etf_metrics_path, dtype={"symbol": str, "tracking_index_code": str}),
        etf_metadata=_read_csv(etf_metadata_path, dtype={"symbol": str, "tracking_index_code": str}),
        data_quality=_read_csv(data_quality_path, dtype={"symbol": str}),
    )


def summarize_factor_score_audit(
    *,
    audit_path: str | Path = "output/factor_score_audit.csv",
    report_path: str | Path = "output/factor_score_report.csv",
    detail_path: str | Path = "output/factor_score_detail.csv",
    example_limit: int = 10,
) -> dict[str, Any]:
    audit_file = Path(audit_path)
    report = _read_csv(report_path, dtype={"symbol": str})
    detail = _read_csv(detail_path, dtype={"symbol": str})
    if not audit_file.exists():
        if report.empty or detail.empty:
            return {
                "factor_score_audit_report": str(audit_file),
                "audit_status": "not_run",
                "high_severity_findings": [],
                "warning_findings": [],
                "computable_ratio": 0.0,
                "top_blocking_reasons": [],
                "audit_top_examples": [],
            }
        audit = build_factor_score_audit_from_files(report_path=report_path, detail_path=detail_path)
    else:
        audit = _read_csv(audit_file)
    if audit.empty:
        return {
            "factor_score_audit_report": str(audit_file),
            "audit_status": "not_run",
            "high_severity_findings": [],
            "warning_findings": [],
            "computable_ratio": 0.0,
            "top_blocking_reasons": [],
            "audit_top_examples": [],
        }
    high = audit[audit["severity"].astype(str).eq("high")].copy()
    warning = audit[audit["severity"].astype(str).eq("warning")].copy()
    computable_ratio = 0.0
    score_rows = audit[audit["audit_item"].astype(str).eq("score_computable_count")]
    if not score_rows.empty:
        parsed_ratio = pd.to_numeric(score_rows.iloc[0].get("ratio", 0), errors="coerce")
        computable_ratio = 0.0 if pd.isna(parsed_ratio) else float(parsed_ratio)
    audit_status = "blocked_for_strategy_use" if not high.empty else ("warning" if not warning.empty else "ok")
    top_blocking = high.sort_values(["count", "audit_item"], ascending=[False, True]).head(example_limit)
    examples = pd.concat([high, warning], ignore_index=True).head(example_limit)
    keep = ["audit_item", "severity", "count", "ratio", "finding", "affected_symbols"]
    return {
        "factor_score_audit_report": str(audit_file),
        "audit_status": audit_status,
        "high_severity_findings": high[keep].head(example_limit).to_dict("records"),
        "warning_findings": warning[keep].head(example_limit).to_dict("records"),
        "computable_ratio": round(computable_ratio, 4),
        "top_blocking_reasons": top_blocking["finding"].astype(str).tolist(),
        "audit_top_examples": examples[keep].to_dict("records"),
    }


def _gate_row(
    gate_item: str,
    status: str,
    severity: str,
    threshold: str,
    actual_value: str,
    passed: bool,
    blocking: bool,
    finding: str,
    suggested_action: str,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "gate_item": gate_item,
        "status": status,
        "severity": severity,
        "threshold": threshold,
        "actual_value": actual_value,
        "passed": bool(passed),
        "blocking": bool(blocking),
        "finding": finding,
        "suggested_action": suggested_action,
        "notes": notes,
    }


def _audit_item_row(audit: pd.DataFrame, audit_item: str) -> pd.Series | None:
    if audit.empty or "audit_item" not in audit.columns:
        return None
    rows = audit[audit["audit_item"].astype(str).eq(audit_item)]
    return None if rows.empty else rows.iloc[0]


def _enabled_factor_names(definitions: list[FactorDefinition] | None, detail: pd.DataFrame) -> set[str]:
    if definitions is not None:
        return {factor.name for factor in definitions if factor.enabled}
    if detail.empty or "factor_name" not in detail.columns or "factor_status" not in detail.columns:
        return set()
    disabled = set(detail[detail["factor_status"].astype(str).eq("disabled")]["factor_name"].astype(str).unique().tolist())
    return set(detail["factor_name"].astype(str).unique().tolist()) - disabled


def _fully_source_unavailable_factors(detail: pd.DataFrame, factor_names: set[str] | None = None) -> set[str]:
    if detail.empty or "factor_name" not in detail.columns or "factor_status" not in detail.columns:
        return set()
    result: set[str] = set()
    for factor_name, factor_rows in detail.groupby("factor_name", sort=True):
        name = str(factor_name)
        if factor_names is not None and name not in factor_names:
            continue
        statuses = factor_rows["factor_status"].astype(str)
        if not factor_rows.empty and statuses.eq("source_unavailable").all():
            result.add(name)
    return result


def _factor_used_coverage(detail: pd.DataFrame, total_symbols: int, factor_names: set[str]) -> dict[str, float]:
    if detail.empty or not total_symbols or "factor_name" not in detail.columns or "factor_status" not in detail.columns:
        return {}
    coverage: dict[str, float] = {}
    for factor_name, factor_rows in detail[detail["factor_name"].astype(str).isin(factor_names)].groupby("factor_name", sort=True):
        used_count = int(factor_rows["factor_status"].astype(str).eq("used").sum())
        coverage[str(factor_name)] = _ratio(used_count, total_symbols)
    return coverage


def evaluate_factor_score_gate(
    report: pd.DataFrame,
    detail: pd.DataFrame,
    audit: pd.DataFrame,
    definitions: list[FactorDefinition] | None = None,
    *,
    thresholds: dict[str, float] | None = None,
) -> pd.DataFrame:
    gate_thresholds = DEFAULT_FACTOR_SCORE_GATE_THRESHOLDS | (thresholds or {})
    report = report.copy().fillna("")
    detail = detail.copy().fillna("")
    audit = audit.copy().fillna("")
    for frame in [report, detail]:
        if not frame.empty and "symbol" in frame.columns:
            frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)

    total_symbols = int(len(report))
    score_status = report.get("score_status", pd.Series(dtype=str)).astype(str)
    computable_count = int(score_status.eq("ok").sum())
    unable_count = int(total_symbols - computable_count)
    computable_ratio = _ratio(computable_count, total_symbols)
    unable_ratio = _ratio(unable_count, total_symbols)
    rows: list[dict[str, Any]] = []

    min_computable = float(gate_thresholds["min_computable_ratio"])
    passed = computable_ratio >= min_computable
    rows.append(
        _gate_row(
            "min_computable_ratio",
            "passed" if passed else "blocked",
            "info" if passed else "high",
            f">= {min_computable:.2f}",
            f"{computable_ratio:.4f} ({computable_count}/{total_symbols})",
            passed,
            not passed,
            f"Computable ratio is {computable_ratio:.4f}; minimum required before candidate use is {min_computable:.2f}.",
            "Keep factor score as an observation report until broad enough coverage exists.",
            f"score_computable_count={computable_count}; total_symbols={total_symbols}",
        )
    )

    max_unable = float(gate_thresholds["max_unable_to_score_ratio"])
    passed = unable_ratio <= max_unable
    rows.append(
        _gate_row(
            "max_unable_to_score_ratio",
            "passed" if passed else "blocked",
            "info" if passed else "high",
            f"<= {max_unable:.2f}",
            f"{unable_ratio:.4f} ({unable_count}/{total_symbols})",
            passed,
            not passed,
            f"Unable-to-score ratio is {unable_ratio:.4f}; maximum allowed before candidate use is {max_unable:.2f}.",
            "Treat no_used_factors as unscoreable, not as a low score.",
            _status_counts_text(report[~score_status.eq("ok")], "score_status"),
        )
    )

    short_row = _audit_item_row(audit, "short_history_bias")
    short_count = int(pd.to_numeric(short_row.get("count", 0), errors="coerce") or 0) if short_row is not None else 0
    short_ratio = _ratio(short_count, computable_count)
    short_passed = short_count == 0
    short_blocking = bool(short_count and computable_count and short_count >= computable_count)
    rows.append(
        _gate_row(
            "no_short_history_bias",
            "passed" if short_passed else ("blocked" if short_blocking else "warning"),
            "info" if short_passed else ("high" if short_blocking else "warning"),
            "count = 0; must not cover all scoreable symbols",
            f"{short_count}/{computable_count} ({short_ratio:.4f})",
            short_passed,
            short_blocking,
            f"Short-history bias affects {short_count} of {computable_count} scoreable symbols.",
            "Require enough price history before producing an independent candidate strategy.",
            str(short_row.get("finding", "")) if short_row is not None else "short_history_bias audit row missing",
        )
    )

    missing_required = int(pd.to_numeric(report.get("missing_required_factor_count", pd.Series(dtype=str)), errors="coerce").fillna(0).sum()) if not report.empty else 0
    passed = missing_required == 0
    rows.append(
        _gate_row(
            "no_missing_required_factors",
            "passed" if passed else "blocked",
            "info" if passed else "high",
            "count = 0",
            str(missing_required),
            passed,
            not passed,
            f"Missing required factor count is {missing_required}.",
            "Do not form candidates while any required factor is missing.",
        )
    )

    enabled = _enabled_factor_names(definitions, detail)
    full_unavailable = _fully_source_unavailable_factors(detail, enabled)
    benchmark_enabled = enabled & BENCHMARK_DEPENDENT_FACTORS
    benchmark_blockers = sorted(full_unavailable & BENCHMARK_DEPENDENT_FACTORS)
    benchmark_passed = not benchmark_enabled or not benchmark_blockers
    rows.append(
        _gate_row(
            "benchmark_dependency_available",
            "passed" if benchmark_passed else "blocked",
            "info" if benchmark_passed else "high",
            "enabled benchmark factors must not be fully source_unavailable",
            ", ".join(benchmark_blockers) if benchmark_blockers else ("not_enabled" if not benchmark_enabled else "available"),
            benchmark_passed,
            not benchmark_passed,
            "Benchmark-dependent enabled factors are unavailable." if benchmark_blockers else "Benchmark-dependent gate has no full source outage.",
            "Confirm benchmark mapping and schema-valid index cache before candidate strategy use.",
            f"enabled_benchmark_factors={','.join(sorted(benchmark_enabled)) or 'none'}",
        )
    )

    nav_enabled = enabled & NAV_IOPV_DEPENDENT_FACTORS
    nav_blockers = sorted(full_unavailable & NAV_IOPV_DEPENDENT_FACTORS)
    nav_passed = not nav_enabled or not nav_blockers
    rows.append(
        _gate_row(
            "nav_iopv_dependency_available",
            "passed" if nav_passed else "blocked",
            "info" if nav_passed else "high",
            "enabled NAV/IOPV factors must not be fully source_unavailable",
            ", ".join(nav_blockers) if nav_blockers else ("not_enabled" if not nav_enabled else "available"),
            nav_passed,
            not nav_passed,
            "NAV/IOPV-dependent enabled factors are unavailable." if nav_blockers else "NAV/IOPV dependency gate has no full source outage.",
            "Add trusted NAV or IOPV data before using discount/premium in candidates.",
            f"enabled_nav_iopv_factors={','.join(sorted(nav_enabled)) or 'none'}",
        )
    )

    metadata_enabled = enabled & METADATA_DEPENDENT_FACTORS
    metadata_disabled = sorted(METADATA_DEPENDENT_FACTORS - enabled)
    metadata_coverage = _factor_used_coverage(detail, total_symbols, metadata_enabled)
    metadata_low = sorted(name for name, ratio in metadata_coverage.items() if ratio < float(gate_thresholds["factor_coverage_minimum"]))
    metadata_passed = bool(metadata_enabled) and not metadata_low
    metadata_blocking = bool(metadata_enabled and metadata_low)
    rows.append(
        _gate_row(
            "metadata_dependency_available",
            "passed" if metadata_passed else ("blocked" if metadata_blocking else "warning"),
            "info" if metadata_passed else ("high" if metadata_blocking else "warning"),
            f"enabled metadata factors coverage >= {float(gate_thresholds['factor_coverage_minimum']):.2f}",
            "disabled=" + (",".join(metadata_disabled) or "none") if not metadata_enabled else "; ".join(f"{k}={v:.4f}" for k, v in sorted(metadata_coverage.items())),
            metadata_passed,
            metadata_blocking,
            "Metadata factors are disabled or below gate coverage.",
            "Keep fund size and fee out of candidates until metadata coverage is confirmed.",
            "Disabled metadata factors are not a direct blocker, but they keep score explanatory rather than complete.",
        )
    )

    core_blockers = sorted(full_unavailable)
    passed = not core_blockers
    rows.append(
        _gate_row(
            "no_source_unavailable_core_factors",
            "passed" if passed else "blocked",
            "info" if passed else "high",
            "no enabled factor fully source_unavailable",
            ", ".join(core_blockers) if core_blockers else "none",
            passed,
            not passed,
            f"{len(core_blockers)} enabled factor(s) are fully source_unavailable.",
            "Fix source dependencies before factor score can drive candidate construction.",
            f"enabled_factor_count={len(enabled)}",
        )
    )

    coverage = _factor_used_coverage(detail, total_symbols, enabled)
    min_count = int(gate_thresholds["min_score_computable_count"])
    min_coverage = float(gate_thresholds["factor_coverage_minimum"])
    min_factor = min(coverage, key=coverage.get) if coverage else ""
    min_factor_coverage = coverage.get(min_factor, 0.0)
    passed = computable_count >= min_count and min_factor_coverage >= min_coverage
    rows.append(
        _gate_row(
            "factor_coverage_minimum",
            "passed" if passed else "blocked",
            "info" if passed else "high",
            f"score_computable_count >= {min_count}; min enabled factor coverage >= {min_coverage:.2f}",
            f"score_computable_count={computable_count}; min_factor={min_factor or 'none'}:{min_factor_coverage:.4f}",
            passed,
            not passed,
            "Factor coverage is not broad enough for candidate strategy use.",
            "Raise source coverage and sample size before entering ETF-GAP-008B.",
            "; ".join(f"{k}={v:.4f}" for k, v in sorted(coverage.items())),
        )
    )

    return pd.DataFrame(rows, columns=FACTOR_SCORE_GATE_COLUMNS)


def write_factor_score_gate_report(gate: pd.DataFrame, *, gate_path: str | Path = "output/factor_score_gate.csv") -> Path:
    out_gate = Path(gate_path)
    out_gate.parent.mkdir(parents=True, exist_ok=True)
    gate[FACTOR_SCORE_GATE_COLUMNS].to_csv(out_gate, index=False, encoding="utf-8-sig")
    return out_gate


def evaluate_factor_score_gate_from_files(
    *,
    config_path: str | Path = "config/factor_score.yaml",
    report_path: str | Path = "output/factor_score_report.csv",
    detail_path: str | Path = "output/factor_score_detail.csv",
    audit_path: str | Path = "output/factor_score_audit.csv",
) -> pd.DataFrame:
    definitions = load_factor_config(config_path)
    return evaluate_factor_score_gate(
        _read_csv(report_path, dtype={"symbol": str}),
        _read_csv(detail_path, dtype={"symbol": str}),
        _read_csv(audit_path),
        definitions,
    )


def summarize_factor_score_gate(
    *,
    gate_path: str | Path = "output/factor_score_gate.csv",
    config_path: str | Path = "config/factor_score.yaml",
    report_path: str | Path = "output/factor_score_report.csv",
    detail_path: str | Path = "output/factor_score_detail.csv",
    audit_path: str | Path = "output/factor_score_audit.csv",
    example_limit: int = 10,
) -> dict[str, Any]:
    gate_file = Path(gate_path)
    if gate_file.exists():
        gate = _read_csv(gate_file)
    else:
        report_file = Path(report_path)
        detail_file = Path(detail_path)
        audit_file = Path(audit_path)
        if not report_file.exists() or not detail_file.exists() or not audit_file.exists():
            return {
                "factor_score_gate_report": str(gate_file),
                "gate_status": "not_run",
                "blocking_findings": [],
                "warning_findings": [],
                "passed_gate_count": 0,
                "failed_gate_count": 0,
            }
        gate = evaluate_factor_score_gate_from_files(config_path=config_path, report_path=report_path, detail_path=detail_path, audit_path=audit_path)
    if gate.empty:
        return {
            "factor_score_gate_report": str(gate_file),
            "gate_status": "not_run",
            "blocking_findings": [],
            "warning_findings": [],
            "passed_gate_count": 0,
            "failed_gate_count": 0,
        }
    passed_mask = gate["passed"].astype(str).str.lower().isin({"true", "1", "yes"})
    blocking_mask = gate["blocking"].astype(str).str.lower().isin({"true", "1", "yes"})
    blocking = gate[~passed_mask & blocking_mask].copy()
    warnings = gate[~passed_mask & ~blocking_mask].copy()
    gate_status = "blocked_for_strategy_use" if not blocking.empty else ("warning_observation_only" if not warnings.empty else "passed_for_candidate_research")
    keep = ["gate_item", "severity", "threshold", "actual_value", "finding", "suggested_action"]
    return {
        "factor_score_gate_report": str(gate_file),
        "gate_status": gate_status,
        "blocking_findings": blocking[keep].head(example_limit).to_dict("records"),
        "warning_findings": warnings[keep].head(example_limit).to_dict("records"),
        "passed_gate_count": int(passed_mask.sum()),
        "failed_gate_count": int((~passed_mask).sum()),
    }


def summarize_factor_score(
    *,
    report_path: str | Path = "output/factor_score_report.csv",
    detail_path: str | Path = "output/factor_score_detail.csv",
    audit_path: str | Path = "output/factor_score_audit.csv",
    gate_path: str | Path = "output/factor_score_gate.csv",
    config_path: str | Path = "config/factor_score.yaml",
    example_limit: int = 10,
) -> dict[str, Any]:
    report_file = Path(report_path)
    detail_file = Path(detail_path)
    empty = {
        "status": "not_run",
        "factor_score_report": str(report_file),
        "factor_score_detail_report": str(detail_file),
        "total_symbols": 0,
        "score_computable_count": 0,
        "unable_to_score_count": 0,
        "enabled_factor_count": 0,
        "used_factor_counts": {},
        "skipped_factor_counts": {},
        "missing_required_factor_count": 0,
        "top_examples": [],
        "factor_score_audit_report": str(audit_path),
        "audit_status": "not_run",
        "high_severity_findings": [],
        "warning_findings": [],
        "computable_ratio": 0.0,
        "top_blocking_reasons": [],
        "audit_top_examples": [],
        "factor_score_gate_report": str(gate_path),
        "gate_status": "not_run",
        "blocking_findings": [],
        "passed_gate_count": 0,
        "failed_gate_count": 0,
    }
    if not report_file.exists() or not detail_file.exists():
        return empty
    report = _read_csv(report_file, dtype={"symbol": str})
    detail = _read_csv(detail_file, dtype={"symbol": str})
    if report.empty:
        return empty | {"status": "ok"}
    used_counts = detail[detail["factor_status"].eq("used")]["factor_name"].value_counts().to_dict() if not detail.empty else {}
    skipped_counts = detail[~detail["factor_status"].eq("used")]["factor_name"].value_counts().to_dict() if not detail.empty else {}
    enabled = pd.to_numeric(report.get("enabled_factor_count", pd.Series([0])), errors="coerce").fillna(0)
    examples = report.sort_values(["score_status", "rank"]).head(example_limit)[
        ["symbol", "name", "total_score", "score_status", "used_factor_count", "skipped_factor_count", "notes"]
    ].to_dict("records")
    audit_summary = summarize_factor_score_audit(
        audit_path=audit_path,
        report_path=report_file,
        detail_path=detail_file,
        example_limit=example_limit,
    )
    gate_summary = summarize_factor_score_gate(
        gate_path=gate_path,
        config_path=config_path,
        report_path=report_file,
        detail_path=detail_file,
        audit_path=audit_path,
        example_limit=example_limit,
    )
    return {
        "status": "ok",
        "factor_score_report": str(report_file),
        "factor_score_detail_report": str(detail_file),
        "total_symbols": int(len(report)),
        "score_computable_count": int(report["score_status"].astype(str).eq("ok").sum()),
        "unable_to_score_count": int((~report["score_status"].astype(str).eq("ok")).sum()),
        "enabled_factor_count": int(enabled.max()) if not enabled.empty else 0,
        "used_factor_counts": {str(key): int(value) for key, value in used_counts.items()},
        "skipped_factor_counts": {str(key): int(value) for key, value in skipped_counts.items()},
        "missing_required_factor_count": int(pd.to_numeric(report["missing_required_factor_count"], errors="coerce").fillna(0).sum()),
        "top_examples": examples,
    } | audit_summary | gate_summary
