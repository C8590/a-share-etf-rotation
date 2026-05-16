from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from data.storage import get_csv_path, load_etf_data
from data.trading_calendar import latest_trading_day_on_or_before


OHLC_COLUMNS = ["open", "high", "low", "close"]
VOLUME_COLUMNS = ["volume", "amount"]
REQUIRED_COLUMNS = ["date", *OHLC_COLUMNS, *VOLUME_COLUMNS, "symbol", "name", "source"]
SAME_PRICE_WARNING_RATIO = 0.95
RETURN_WARNING_THRESHOLD = 0.20
DEFAULT_MIN_AVG_AMOUNT = 20_000_000.0

FAILURE_TYPE_ORDER = [
    "download_failed",
    "missing_required_columns",
    "insufficient_rows",
    "stale_end_date",
    "invalid_ohlc",
    "missing_values",
    "duplicate_dates",
    "abnormal_return",
    "zero_or_low_liquidity",
    "filtered_by_universe_rule",
    "unknown",
]

SUGGESTED_ACTIONS = {
    "download_failed": "retry refresh/rebuild download and inspect source connectivity or local cache",
    "missing_required_columns": "repair cached CSV schema or refresh this ETF from source",
    "insufficient_rows": "keep excluded until enough trading history is accumulated",
    "stale_end_date": "refresh local cache and verify source end-date coverage",
    "invalid_ohlc": "inspect raw OHLC rows and source adjustment path",
    "missing_values": "refresh from source or repair null required fields before use",
    "duplicate_dates": "deduplicate by date and verify merge/update logic",
    "abnormal_return": "manually review price jump, dividend/split adjustment, and source path",
    "zero_or_low_liquidity": "exclude or observe only after liquidity improves",
    "filtered_by_universe_rule": "check universe filter settings and ETF eligibility metadata",
    "unknown": "inspect original failure_reason and add a classifier if recurring",
}


@dataclass
class QualityResult:
    symbol: str
    name: str
    status: str
    rows: int
    start_date: str
    end_date: str
    missing_count: int
    duplicate_count: int
    errors: list[str]
    warnings: list[str]
    failure_types: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "status": self.status,
            "rows": self.rows,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "missing_count": self.missing_count,
            "duplicate_count": self.duplicate_count,
            "errors": "; ".join(self.errors),
            "warnings": "; ".join(self.warnings),
            "failure_types": "; ".join(self.failure_types),
            "primary_failure_type": self.failure_types[0] if self.failure_types else "",
        }


@dataclass
class DataGateResult:
    allow_formal: bool
    test_only: bool
    effective_etf_count: int
    latest_date: str
    reasons: list[str]
    quality_results: list[QualityResult]
    failure_summary: list[dict[str, Any]]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _split_reasons(text: str) -> list[str]:
    return [item.strip() for item in str(text or "").replace("|", ";").split(";") if item.strip()]


def classify_failure_reason(reason: str) -> str:
    text = str(reason or "").lower()
    if not text:
        return "unknown"
    if any(
        marker in text
        for marker in [
            "local data",
            "not found",
            "source returned empty",
            "all data sources failed",
            "previous failure",
            "download",
            "akshare.",
            "connectionpool",
            "proxyerror",
            "max retries exceeded",
        ]
    ):
        return "download_failed"
    if "missing required" in text or "missing " in text and " column" in text or "missing field" in text or "incompatible source fields" in text:
        return "missing_required_columns"
    if "too few rows" in text or "listed_days<" in text:
        return "insufficient_rows"
    if "stale" in text or "end-date coverage gap" in text or "latest data date" in text:
        return "stale_end_date"
    if any(marker in text for marker in ["high is lower", "low is higher", "non-positive", "higher than", "lower than"]):
        return "invalid_ohlc"
    if any(marker in text for marker in ["null", "invalid values", "missing values", "contains na"]):
        return "missing_values"
    if "duplicate" in text:
        return "duplicate_dates"
    if "daily close return exceeds" in text or "abnormal return" in text:
        return "abnormal_return"
    if any(marker in text for marker in ["zero_amount", "zero amount", "zero or negative", "volume", "amount<", "amount contains", "avg_amount<", "liquidity"]):
        return "zero_or_low_liquidity"
    if any(marker in text for marker in ["filter_passed=false", "filtered by universe", "data_completeness<", "market filter"]):
        return "filtered_by_universe_rule"
    return "unknown"


def _result_failure_types(errors: list[str], warnings: list[str]) -> list[str]:
    return _unique([classify_failure_reason(reason) for reason in [*errors, *warnings]])


def _status_from_issues(errors: list[str], warnings: list[str]) -> str:
    return "failed" if errors else ("warning" if warnings else "passed")


def _date_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _int_value(value: Any) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0 if pd.isna(parsed) else int(float(parsed))


def _float_value(value: Any) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else float(parsed)


def _load_failure_filters(config_path: str | Path = "config/etf_universe.yaml") -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {"min_avg_amount": DEFAULT_MIN_AVG_AMOUNT}
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return {"min_avg_amount": DEFAULT_MIN_AVG_AMOUNT}
    filters = raw.get("filters", {}) or {}
    return {"min_avg_amount": float(filters.get("min_avg_amount", DEFAULT_MIN_AVG_AMOUNT))}


def analyze_single_etf(
    symbol: str,
    name: str,
    df: pd.DataFrame,
    min_rows: int = 250,
) -> QualityResult:
    errors: list[str] = []
    warnings: list[str] = []
    frame = df.copy()
    if "date" not in frame.columns:
        frame = frame.reset_index()
    if "date" not in frame.columns:
        errors.append("missing date column")
        return QualityResult(symbol, name, "failed", len(frame), "", "", 0, 0, errors, warnings, _result_failure_types(errors, warnings))

    missing_required = [col for col in REQUIRED_COLUMNS if col not in frame.columns]
    if missing_required:
        errors.append(f"missing required columns: {', '.join(missing_required)}")

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    duplicate_count = int(frame["date"].duplicated().sum())
    missing_check_cols = [col for col in REQUIRED_COLUMNS if col in frame.columns]
    missing_count = int(frame[missing_check_cols].isna().any(axis=1).sum()) if missing_check_cols else len(frame)
    rows = int(len(frame))

    blank_required = []
    for col in ["symbol", "name", "source"]:
        if col in frame.columns and (frame[col].astype(str).str.strip() == "").any():
            blank_required.append(col)
    if blank_required:
        errors.append(f"required fields contain missing values: {', '.join(blank_required)}")

    if rows < min_rows:
        errors.append(f"too few rows: {rows} < {min_rows}")
    if duplicate_count > 0:
        errors.append(f"duplicate dates: {duplicate_count}")
    if frame["date"].isna().any():
        errors.append("date contains null or invalid values")
    if not frame["date"].dropna().is_monotonic_increasing:
        errors.append("date is not ascending")
    today = pd.Timestamp.today().normalize()
    if frame["date"].dropna().gt(today).any():
        errors.append("date is later than system date")

    for col in OHLC_COLUMNS:
        if col not in frame.columns:
            errors.append(f"missing {col} column")
        else:
            values = pd.to_numeric(frame[col], errors="coerce")
            if values.isna().any():
                errors.append(f"{col} contains null or invalid values")
            if (values <= 0).any():
                errors.append(f"{col} contains non-positive values")

    for col in VOLUME_COLUMNS:
        if col not in frame.columns:
            errors.append(f"missing {col} column")
        else:
            values = pd.to_numeric(frame[col], errors="coerce")
            if values.isna().any():
                errors.append(f"{col} contains null or invalid values")
            if (values <= 0).any():
                warnings.append(f"{col} contains zero or negative values")

    if set(OHLC_COLUMNS).issubset(frame.columns):
        open_ = pd.to_numeric(frame["open"], errors="coerce")
        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        close = pd.to_numeric(frame["close"], errors="coerce")
        if (high < low).any():
            errors.append("high is lower than low")
        if (high < open_).any():
            errors.append("high is lower than open")
        if (high < close).any():
            errors.append("high is lower than close")
        if (low > open_).any():
            errors.append("low is higher than open")
        if (low > close).any():
            errors.append("low is higher than close")

        valid_close = close.dropna()
        if len(valid_close) > 0:
            if (close == high).mean() >= SAME_PRICE_WARNING_RATIO:
                warnings.append("close equals high at an unusually high ratio")
            if (close == low).mean() >= SAME_PRICE_WARNING_RATIO:
                warnings.append("close equals low at an unusually high ratio")
            if (close == open_).mean() >= SAME_PRICE_WARNING_RATIO:
                warnings.append("close equals open at an unusually high ratio")
            daily_return = frame.assign(_close=close).sort_values("date")["_close"].pct_change()
            abnormal_count = int((daily_return.abs() > RETURN_WARNING_THRESHOLD).sum())
            if abnormal_count:
                warnings.append(f"daily close return exceeds {RETURN_WARNING_THRESHOLD:.0%} on {abnormal_count} day(s)")

    valid_dates = frame["date"].dropna()
    start_date = str(valid_dates.min().date()) if not valid_dates.empty else ""
    end_date = str(valid_dates.max().date()) if not valid_dates.empty else ""
    status = _status_from_issues(errors, warnings)
    return QualityResult(symbol, name, status, rows, start_date, end_date, missing_count, duplicate_count, errors, warnings, _result_failure_types(errors, warnings))


def _coverage_map(coverage_rows: list[dict[str, Any]] | None, output_dir: str | Path) -> dict[str, dict[str, Any]]:
    if coverage_rows is not None:
        rows = coverage_rows
    else:
        path = Path(output_dir) / "data_coverage_report.csv"
        if not path.exists():
            return {}
        try:
            rows = pd.read_csv(path, dtype={"symbol": str}).fillna("").to_dict("records")
        except Exception:
            return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).zfill(6)
        if symbol:
            result[symbol] = row
    return result


def _latest_expected_date(coverage_by_symbol: dict[str, dict[str, Any]], results: list[QualityResult]) -> str:
    try:
        return str(latest_trading_day_on_or_before(pd.Timestamp.today()).date())
    except Exception:
        pass
    candidates: list[pd.Timestamp] = []
    for row in coverage_by_symbol.values():
        for col in ["target_update_date", "latest_date", "end_date", "local_latest_date"]:
            value = row.get(col, "")
            parsed = pd.to_datetime(value, errors="coerce")
            if not pd.isna(parsed):
                candidates.append(parsed.normalize())
                break
    for item in results:
        parsed = pd.to_datetime(item.end_date, errors="coerce")
        if not pd.isna(parsed):
            candidates.append(parsed.normalize())
    return str(max(candidates).date()) if candidates else ""


def _end_date_gap_days(end_date: str, latest_expected_date: str) -> int:
    end = pd.to_datetime(end_date, errors="coerce")
    expected = pd.to_datetime(latest_expected_date, errors="coerce")
    if pd.isna(end) or pd.isna(expected):
        return 0
    return max(0, int((expected.normalize() - end.normalize()).days))


def _failure_row(
    *,
    symbol: str,
    name: str,
    row: dict[str, Any],
    item: QualityResult | None,
    failure_type: str,
    reason: str,
    severity: str,
    latest_expected_date: str,
    end_date_gap_days: int,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name,
        "asset_class": str(row.get("asset_class", "")),
        "category": str(row.get("category", "")),
        "source": str(row.get("source", "")),
        "start_date": _date_text(row.get("start_date", row.get("listing_date", item.start_date if item else ""))),
        "end_date": _date_text(row.get("end_date", row.get("latest_date", item.end_date if item else ""))),
        "row_count": _int_value(row.get("rows", row.get("data_rows", item.rows if item else 0))),
        "latest_expected_date": latest_expected_date,
        "end_date_gap_days": end_date_gap_days,
        "failure_type": failure_type,
        "failure_reason": reason,
        "severity": severity,
        "suggested_action": SUGGESTED_ACTIONS.get(failure_type, SUGGESTED_ACTIONS["unknown"]),
    }


def build_data_failure_summary(
    etf_pool: list[dict[str, str]],
    quality_results: list[QualityResult],
    coverage_rows: list[dict[str, Any]] | None = None,
    latest_expected_date: str | None = None,
    max_end_date_gap_days: int = 10,
    min_avg_amount: float = DEFAULT_MIN_AVG_AMOUNT,
    output_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    coverage_by_symbol = _coverage_map(coverage_rows, output_dir or "output")
    quality_by_symbol = {item.symbol: item for item in quality_results}
    if latest_expected_date is None:
        latest_expected_date = _latest_expected_date(coverage_by_symbol, quality_results)

    summary: list[dict[str, Any]] = []
    emitted: set[tuple[str, str, str]] = set()

    def append(symbol: str, failure_type: str, reason: str, severity: str, row: dict[str, Any], item: QualityResult | None) -> None:
        key = (symbol, failure_type, reason)
        if key in emitted:
            return
        emitted.add(key)
        end_date = str(row.get("end_date", row.get("latest_date", item.end_date if item else "")))
        gap_days = _end_date_gap_days(end_date, latest_expected_date or "")
        summary.append(
            _failure_row(
                symbol=symbol,
                name=str(row.get("name", item.name if item else "")),
                row=row,
                item=item,
                failure_type=failure_type,
                reason=reason,
                severity=severity,
                latest_expected_date=latest_expected_date or "",
                end_date_gap_days=gap_days,
            )
        )

    for etf in etf_pool:
        symbol = str(etf["symbol"]).zfill(6)
        item = quality_by_symbol.get(symbol)
        row = {**etf, **coverage_by_symbol.get(symbol, {})}
        end_date = str(row.get("end_date", row.get("latest_date", item.end_date if item else "")))
        gap_days = _end_date_gap_days(end_date, latest_expected_date or "")

        success_text = str(row.get("success", "")).lower()
        if success_text in {"false", "0", "no"}:
            reason = str(row.get("failure_reason") or row.get("filter_reason") or "download or local cache failed")
            append(symbol, "download_failed", reason, "severe", row, item)

        if item is not None:
            for reason in item.errors:
                append(symbol, classify_failure_reason(reason), reason, "severe", row, item)
            for reason in item.warnings:
                append(symbol, classify_failure_reason(reason), reason, "warning", row, item)
            if item.status == "failed" and not item.errors:
                append(symbol, "unknown", "quality status failed without explicit reason", "severe", row, item)

        if gap_days > max_end_date_gap_days:
            append(symbol, "stale_end_date", f"end_date is {gap_days} day(s) behind latest_expected_date", "severe", row, item)

        avg_amount = _float_value(row.get("avg_amount_20", None))
        if avg_amount is not None and avg_amount < min_avg_amount:
            severity = "severe" if avg_amount <= 0 else "warning"
            append(symbol, "zero_or_low_liquidity", f"avg_amount_20 {avg_amount:.2f} < {min_avg_amount:.2f}", severity, row, item)

        filter_reason = str(row.get("filter_reason", "")).strip()
        if filter_reason:
            for reason in _split_reasons(filter_reason):
                failure_type = classify_failure_reason(reason)
                if failure_type in {"insufficient_rows", "zero_or_low_liquidity", "unknown"}:
                    append(symbol, failure_type, reason, "warning", row, item)
                elif failure_type == "filtered_by_universe_rule":
                    append(symbol, failure_type, reason, "warning", row, item)

    summary.sort(key=lambda row: (FAILURE_TYPE_ORDER.index(row["failure_type"]) if row["failure_type"] in FAILURE_TYPE_ORDER else 999, row["symbol"]))

    if output_dir is not None:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(summary, columns=[
            "symbol",
            "name",
            "asset_class",
            "category",
            "source",
            "start_date",
            "end_date",
            "row_count",
            "latest_expected_date",
            "end_date_gap_days",
            "failure_type",
            "failure_reason",
            "severity",
            "suggested_action",
        ]).to_csv(output_path / "data_failure_summary.csv", index=False, encoding="utf-8-sig")
    return summary


def summarize_failure_summary(summary: list[dict[str, Any]], example_limit: int = 10) -> dict[str, Any]:
    frame = pd.DataFrame(summary)
    if frame.empty:
        return {
            "total_failed": 0,
            "failure_type_counts": {},
            "severe_failed": 0,
            "warning_failed": 0,
            "top_examples": [],
        }
    severe_symbols = frame[frame["severity"].eq("severe")]["symbol"].astype(str).nunique()
    warning_symbols = frame[frame["severity"].eq("warning")]["symbol"].astype(str).nunique()
    type_counts = frame.groupby("failure_type")["symbol"].nunique().sort_index()
    examples = (
        frame.drop_duplicates(subset=["symbol", "failure_type"])
        .head(example_limit)[["symbol", "name", "failure_type", "failure_reason", "severity"]]
        .to_dict("records")
    )
    return {
        "total_failed": int(frame["symbol"].astype(str).nunique()),
        "failure_type_counts": {str(k): int(v) for k, v in type_counts.items()},
        "severe_failed": int(severe_symbols),
        "warning_failed": int(warning_symbols),
        "top_examples": examples,
    }


def run_data_quality_checks(
    etf_pool: list[dict[str, str]],
    min_rows: int = 250,
    max_latest_lag_days: int = 10,
    max_coverage_gap_days: int = 10,
    min_effective_etf_count: int = 5,
    output_dir: str | Path = "output",
    coverage_rows: list[dict[str, Any]] | None = None,
) -> DataGateResult:
    results: list[QualityResult] = []
    reasons: list[str] = []

    for etf in etf_pool:
        symbol = etf["symbol"]
        name = etf["name"]
        try:
            df = load_etf_data(symbol, name=name).reset_index()
            result = analyze_single_etf(symbol, name, df, min_rows=min_rows)
        except Exception as exc:  # noqa: BLE001
            errors = [str(exc)]
            result = QualityResult(symbol, name, "failed", 0, "", "", 0, 0, errors, [], _result_failure_types(errors, []))
        results.append(result)

    passed = [item for item in results if item.status in {"passed", "warning"}]
    effective_count = len(passed)
    latest_dates = [pd.Timestamp(item.end_date) for item in passed if item.end_date]
    latest_date = max(latest_dates) if latest_dates else pd.NaT
    today = pd.Timestamp.today().normalize()
    latest_expected_date = ""
    calendar_error = ""
    try:
        latest_expected_date = str(latest_trading_day_on_or_before(today).date())
    except Exception as exc:  # noqa: BLE001
        calendar_error = str(exc)
        reasons.append(f"trading calendar unavailable: {calendar_error}")

    if effective_count < min_effective_etf_count:
        reasons.append(f"effective ETF count {effective_count} is below gate {min_effective_etf_count}")

    failed_quality = [item for item in results if item.status == "failed"]
    if failed_quality:
        reasons.append(f"data quality failed for {len(failed_quality)} ETF(s)")

    if pd.isna(latest_date):
        reasons.append("no usable latest date")
    else:
        expected = pd.Timestamp(latest_expected_date) if latest_expected_date else today
        lag_days = int((expected.normalize() - latest_date.normalize()).days)
        if lag_days > max_latest_lag_days:
            reasons.append(f"latest data date {latest_date.date()} is stale by {lag_days} trading-calendar day(s)")

    end_dates = [pd.Timestamp(item.end_date) for item in passed if item.end_date]
    if len(end_dates) >= 2:
        expected = pd.Timestamp(latest_expected_date) if latest_expected_date else max(end_dates)
        coverage_gap = max(0, int((expected.normalize() - min(end_dates).normalize()).days))
        if coverage_gap > max_coverage_gap_days:
            reasons.append(f"ETF end-date coverage gap is {coverage_gap} days")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([item.to_row() for item in results]).to_csv(output_path / "data_quality_report.csv", index=False, encoding="utf-8-sig")
    filters = _load_failure_filters()
    summary = build_data_failure_summary(
        etf_pool=etf_pool,
        quality_results=results,
        coverage_rows=coverage_rows,
        latest_expected_date=latest_expected_date or None,
        max_end_date_gap_days=max_coverage_gap_days,
        min_avg_amount=float(filters.get("min_avg_amount", DEFAULT_MIN_AVG_AMOUNT)),
        output_dir=output_path,
    )

    allow_formal = not reasons
    return DataGateResult(
        allow_formal=allow_formal,
        test_only=not allow_formal,
        effective_etf_count=effective_count,
        latest_date=str(latest_date.date()) if not pd.isna(latest_date) else "",
        reasons=reasons,
        quality_results=results,
        failure_summary=summary,
    )


def cached_symbols(etf_pool: list[dict[str, str]]) -> list[str]:
    return [etf["symbol"] for etf in etf_pool if get_csv_path(etf["symbol"]).exists()]
