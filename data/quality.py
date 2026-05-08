from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from data.storage import get_csv_path, load_etf_data


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
        }


@dataclass
class DataGateResult:
    allow_formal: bool
    test_only: bool
    effective_etf_count: int
    latest_date: str
    reasons: list[str]
    quality_results: list[QualityResult]


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
        return QualityResult(symbol, name, "failed", len(frame), "", "", 0, 0, errors, warnings)

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    duplicate_count = int(frame["date"].duplicated().sum())
    missing_count = int(frame[["date", "close"]].isna().any(axis=1).sum()) if "close" in frame.columns else len(frame)
    rows = int(len(frame))

    if rows < min_rows:
        errors.append(f"too few rows: {rows} < {min_rows}")
    if duplicate_count > 0:
        errors.append(f"duplicate dates: {duplicate_count}")
    if frame["date"].isna().any():
        errors.append("date contains null or invalid values")
    if not frame["date"].dropna().is_monotonic_increasing:
        errors.append("date is not ascending")
    if "close" not in frame.columns or frame["close"].isna().any():
        errors.append("close contains null values")

    price_cols = ["open", "high", "low", "close"]
    for col in price_cols:
        if col not in frame.columns:
            errors.append(f"missing {col} column")
        else:
            values = pd.to_numeric(frame[col], errors="coerce")
            if (values <= 0).any():
                errors.append(f"{col} contains non-positive values")

    if {"high", "low"}.issubset(frame.columns):
        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        if (high < low).any():
            errors.append("high is lower than low")

    valid_dates = frame["date"].dropna()
    start_date = str(valid_dates.min().date()) if not valid_dates.empty else ""
    end_date = str(valid_dates.max().date()) if not valid_dates.empty else ""
    status = "failed" if errors else ("warning" if warnings else "passed")
    return QualityResult(symbol, name, status, rows, start_date, end_date, missing_count, duplicate_count, errors, warnings)


def run_data_quality_checks(
    etf_pool: list[dict[str, str]],
    min_rows: int = 250,
    max_latest_lag_days: int = 10,
    max_coverage_gap_days: int = 10,
    min_effective_etf_count: int = 5,
    output_dir: str | Path = "output",
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
            result = QualityResult(symbol, name, "failed", 0, "", "", 0, 0, [str(exc)], [])
        results.append(result)

    passed = [item for item in results if item.status in {"passed", "warning"}]
    effective_count = len(passed)
    latest_dates = [pd.Timestamp(item.end_date) for item in passed if item.end_date]
    latest_date = max(latest_dates) if latest_dates else pd.NaT
    today = pd.Timestamp.today().normalize()

    if effective_count < min_effective_etf_count:
        reasons.append(f"effective ETF count {effective_count} is below gate {min_effective_etf_count}")

    failed_quality = [item for item in results if item.status == "failed"]
    if failed_quality:
        reasons.append(f"data quality failed for {len(failed_quality)} ETF(s)")

    if pd.isna(latest_date):
        reasons.append("no usable latest date")
    else:
        lag_days = int((today - latest_date.normalize()).days)
        if lag_days > max_latest_lag_days:
            reasons.append(f"latest data date {latest_date.date()} is stale by {lag_days} days")

    end_dates = [pd.Timestamp(item.end_date) for item in passed if item.end_date]
    if len(end_dates) >= 2:
        coverage_gap = int((max(end_dates) - min(end_dates)).days)
        if coverage_gap > max_coverage_gap_days:
            reasons.append(f"ETF end-date coverage gap is {coverage_gap} days")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([item.to_row() for item in results]).to_csv(output_path / "data_quality_report.csv", index=False, encoding="utf-8-sig")

    allow_formal = not reasons
    return DataGateResult(
        allow_formal=allow_formal,
        test_only=not allow_formal,
        effective_etf_count=effective_count,
        latest_date=str(latest_date.date()) if not pd.isna(latest_date) else "",
        reasons=reasons,
        quality_results=results,
    )


def cached_symbols(etf_pool: list[dict[str, str]]) -> list[str]:
    return [etf["symbol"] for etf in etf_pool if get_csv_path(etf["symbol"]).exists()]
