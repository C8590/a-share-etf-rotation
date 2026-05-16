from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from data.trading_calendar import latest_trading_day_on_or_before


DEFAULT_INDEX_SOURCE_DIAGNOSTIC_LIMIT = 10
DEFAULT_INDEX_SOURCE_DIAGNOSTICS_OUTPUT = Path("output") / "index_source_diagnostics.csv"
DEFAULT_INDEX_MAP_PATH = Path("output") / "index_map.csv"
ABNORMAL_RETURN_THRESHOLD = 0.20

DEFAULT_INDEX_TARGETS = [
    {"index_code": "000015", "index_name": "上证红利"},
    {"index_code": "000300", "index_name": "沪深300"},
    {"index_code": "000688", "index_name": "科创50"},
    {"index_code": "000852", "index_name": "中证1000"},
    {"index_code": "000905", "index_name": "中证500"},
    {"index_code": "000932", "index_name": "中证主要消费"},
    {"index_code": "399006", "index_name": "创业板指"},
    {"index_code": "399975", "index_name": "中证全指证券公司"},
    {"index_code": "931865", "index_name": "中证全指半导体产品与设备"},
]

INDEX_SOURCE_DIAGNOSTICS_COLUMNS = [
    "run_id",
    "checked_at",
    "index_code",
    "index_name",
    "api_name",
    "source_family",
    "call_success",
    "status_code",
    "row_count",
    "start_date",
    "end_date",
    "latest_expected_date",
    "end_date_gap_days",
    "schema_valid",
    "missing_required_columns",
    "missing_values_count",
    "duplicate_dates_count",
    "abnormal_return_count",
    "failure_type",
    "failure_reason",
    "elapsed_ms",
    "usable_as_index_source",
    "requires_manual_review",
    "suggested_action",
    "notes",
]

INDEX_DIAGNOSTIC_REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]

IndexFetcher = Callable[[str, str, str | None, Any], pd.DataFrame]


@dataclass(frozen=True)
class IndexApiCandidate:
    api_name: str
    source_family: str
    fetcher: IndexFetcher
    notes: str = ""


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _latest_expected_date() -> str:
    try:
        return str(latest_trading_day_on_or_before(pd.Timestamp.today().normalize()).date())
    except Exception:
        return str(pd.Timestamp.today().normalize().date())


def _end_date_gap_days(end_date: str, latest_expected_date: str) -> int:
    end = pd.to_datetime(end_date, errors="coerce")
    expected = pd.to_datetime(latest_expected_date, errors="coerce")
    if pd.isna(end) or pd.isna(expected):
        return 0
    return max(0, int((expected.normalize() - end.normalize()).days))


def _pick_column(frame: pd.DataFrame, aliases: list[str]) -> str | None:
    direct = {str(col).strip(): col for col in frame.columns}
    for alias in aliases:
        if alias in direct:
            return direct[alias]
    lower = {str(col).strip().lower(): col for col in frame.columns}
    for alias in aliases:
        found = lower.get(alias.lower())
        if found is not None:
            return found
    return None


def _market_symbol_for_daily(index_code: str) -> str:
    code = str(index_code).strip().zfill(6)
    if code.startswith("399"):
        return f"sz{code}"
    return f"sh{code}"


def _market_symbol_for_em(index_code: str) -> str:
    code = str(index_code).strip().zfill(6)
    if code.startswith("399"):
        return f"sz{code}"
    if code.startswith("9"):
        return f"csi{code}"
    return f"sh{code}"


def _filter_dates(frame: pd.DataFrame, start_date: str, end_date: str | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame() if frame is None else frame
    date_col = _pick_column(frame, ["date", "日期", "trade_date", "时间", "day"])
    if date_col is None:
        return frame
    work = frame.copy()
    dates = pd.to_datetime(work[date_col], errors="coerce")
    start = pd.to_datetime(start_date, errors="coerce")
    end = pd.to_datetime(end_date or datetime.now().strftime("%Y%m%d"), errors="coerce")
    if not pd.isna(start):
        work = work[dates >= start]
        dates = dates[dates >= start]
    if not pd.isna(end):
        work = work[dates <= end]
    return work.reset_index(drop=True)


def _fetch_index_zh_a_hist(index_code: str, start_date: str, end_date: str | None, ak_module: Any) -> pd.DataFrame:
    return ak_module.index_zh_a_hist(symbol=str(index_code).strip().zfill(6), period="daily", start_date=start_date, end_date=end_date or datetime.now().strftime("%Y%m%d"))


def _fetch_stock_zh_index_daily_em(index_code: str, start_date: str, end_date: str | None, ak_module: Any) -> pd.DataFrame:
    return ak_module.stock_zh_index_daily_em(symbol=_market_symbol_for_em(index_code), start_date=start_date, end_date=end_date or datetime.now().strftime("%Y%m%d"))


def _fetch_stock_zh_index_daily(index_code: str, start_date: str, end_date: str | None, ak_module: Any) -> pd.DataFrame:
    raw = ak_module.stock_zh_index_daily(symbol=_market_symbol_for_daily(index_code))
    return _filter_dates(raw, start_date, end_date)


def _fetch_stock_zh_index_daily_tx(index_code: str, start_date: str, end_date: str | None, ak_module: Any) -> pd.DataFrame:
    return ak_module.stock_zh_index_daily_tx(symbol=_market_symbol_for_daily(index_code), start_date=start_date, end_date=end_date or datetime.now().strftime("%Y%m%d"))


def _fetch_stock_zh_index_hist_csindex(index_code: str, start_date: str, end_date: str | None, ak_module: Any) -> pd.DataFrame:
    return ak_module.stock_zh_index_hist_csindex(symbol=str(index_code).strip().zfill(6), start_date=start_date, end_date=end_date or datetime.now().strftime("%Y%m%d"))


INDEX_API_CANDIDATES = [
    IndexApiCandidate("akshare.index_zh_a_hist", "eastmoney", _fetch_index_zh_a_hist, "AKShare A-share index history; observed EM dependency"),
    IndexApiCandidate("akshare.stock_zh_index_daily_em", "eastmoney", _fetch_stock_zh_index_daily_em, "AKShare EastMoney daily index kline"),
    IndexApiCandidate("akshare.stock_zh_index_daily", "sina", _fetch_stock_zh_index_daily, "AKShare Sina daily index history"),
    IndexApiCandidate("akshare.stock_zh_index_daily_tx", "tencent", _fetch_stock_zh_index_daily_tx, "AKShare Tencent daily index history"),
    IndexApiCandidate("akshare.stock_zh_index_hist_csindex", "csindex", _fetch_stock_zh_index_hist_csindex, "AKShare CSI index history candidate"),
]


def build_index_diagnostic_targets(
    *,
    index_codes: str | list[str] | None = None,
    index_map_path: str | Path = DEFAULT_INDEX_MAP_PATH,
    max_count: int = DEFAULT_INDEX_SOURCE_DIAGNOSTIC_LIMIT,
) -> list[dict[str, str]]:
    if int(max_count) > DEFAULT_INDEX_SOURCE_DIAGNOSTIC_LIMIT:
        raise ValueError(f"index source diagnostics max_count must be <= {DEFAULT_INDEX_SOURCE_DIAGNOSTIC_LIMIT}")
    requested = [item.strip().zfill(6) for item in index_codes.split(",") if item.strip()] if isinstance(index_codes, str) else None
    if isinstance(index_codes, list):
        requested = [str(item).strip().zfill(6) for item in index_codes if str(item).strip()]

    default_names = {item["index_code"]: item["index_name"] for item in DEFAULT_INDEX_TARGETS}
    targets: list[dict[str, str]] = []
    if requested:
        for code in requested[: int(max_count)]:
            targets.append({"index_code": code, "index_name": default_names.get(code, "")})
        return targets

    path = Path(index_map_path)
    if path.exists():
        try:
            frame = pd.read_csv(path, dtype={"tracking_index_code": str}, encoding="utf-8-sig").fillna("")
            if "usable_as_benchmark" in frame.columns:
                frame = frame[frame["usable_as_benchmark"].apply(_bool_value)].copy()
            seen: set[str] = set()
            for row in frame.to_dict("records"):
                code = str(row.get("tracking_index_code", "")).strip().zfill(6)
                if not code or code.lower() == "unable_to_confirm" or code in seen:
                    continue
                seen.add(code)
                targets.append(
                    {
                        "index_code": code,
                        "index_name": str(row.get("tracking_index_name", "") or default_names.get(code, "")),
                    }
                )
                if len(targets) >= int(max_count):
                    break
        except Exception:
            targets = []

    if not targets:
        targets = DEFAULT_INDEX_TARGETS[: int(max_count)]
    return targets[: int(max_count)]


def _base_row(
    *,
    run_id: str,
    checked_at: str,
    index_code: str,
    index_name: str,
    api_name: str,
    source_family: str,
    latest_expected_date: str,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "checked_at": checked_at,
        "index_code": str(index_code).strip().zfill(6),
        "index_name": str(index_name or ""),
        "api_name": api_name,
        "source_family": source_family,
        "call_success": False,
        "status_code": "",
        "row_count": 0,
        "start_date": "",
        "end_date": "",
        "latest_expected_date": latest_expected_date,
        "end_date_gap_days": 0,
        "schema_valid": False,
        "missing_required_columns": "",
        "missing_values_count": 0,
        "duplicate_dates_count": 0,
        "abnormal_return_count": 0,
        "failure_type": "",
        "failure_reason": "",
        "elapsed_ms": 0,
        "usable_as_index_source": False,
        "requires_manual_review": True,
        "suggested_action": "",
        "notes": notes,
    }


def _status_code_from_exception(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", "")
    if status:
        return str(status)
    match = re.search(r"\bHTTP\s+(\d{3})\b", str(exc), flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _classify_failure(exc: BaseException | None = None, *, schema_reason: str = "", status_code: str = "") -> str:
    if schema_reason:
        return "schema_error"
    if status_code:
        parsed = pd.to_numeric(status_code, errors="coerce")
        if not pd.isna(parsed) and int(parsed) >= 400:
            return "http_error"
    if exc is None:
        return "unknown"
    text = f"{exc.__class__.__name__} {exc}".lower()
    if "proxyerror" in text or "proxy" in text or "remote end closed connection" in text:
        return "proxy_error"
    if "timeout" in text or "timed out" in text or "read timed out" in text:
        return "timeout"
    if "http" in text and re.search(r"\b[45]\d\d\b", text):
        return "http_error"
    return "unknown"


def _suggested_action(row: dict[str, Any]) -> str:
    if row["usable_as_index_source"]:
        return "candidate can be considered for update-index-data after manual review of source stability"
    failure_type = str(row.get("failure_type", ""))
    if failure_type == "proxy_error":
        return "fix proxy or network path to this source before rerunning update-index-data"
    if failure_type == "timeout":
        return "retry diagnostics later or with a lower request rate before trusting this source"
    if failure_type == "http_error":
        return "inspect endpoint availability or symbol support for this API"
    if failure_type == "schema_error":
        return "do not use for benchmark cache until required OHLCV fields are present and normalized"
    if failure_type == "empty_data":
        return "treat as unsupported for this index code unless a source-specific symbol mapping is confirmed"
    return "preserve diagnostic evidence and review source support before changing benchmark workflow"


def _evaluate_schema(raw: pd.DataFrame, *, latest_expected_date: str) -> dict[str, Any]:
    if raw is None or raw.empty:
        return {
            "row_count": 0,
            "failure_type": "empty_data",
            "failure_reason": "candidate returned empty data",
        }
    aliases = {
        "date": ["date", "日期", "trade_date", "时间", "day"],
        "open": ["open", "开盘", "开盘价"],
        "high": ["high", "最高", "最高价"],
        "low": ["low", "最低", "最低价"],
        "close": ["close", "收盘", "收盘价"],
        "volume": ["volume", "成交量", "vol"],
        "amount": ["amount", "成交额", "成交金额"],
    }
    picked = {name: _pick_column(raw, candidates) for name, candidates in aliases.items()}
    missing = [name for name, column in picked.items() if column is None]
    result: dict[str, Any] = {
        "row_count": int(len(raw)),
        "missing_required_columns": ";".join(missing),
        "schema_valid": not missing,
        "failure_type": "schema_error" if missing else "",
        "failure_reason": f"missing required columns: {', '.join(missing)}" if missing else "",
    }
    date_col = picked.get("date")
    dates = pd.to_datetime(raw[date_col], errors="coerce") if date_col is not None else pd.Series(dtype="datetime64[ns]")
    result["start_date"] = _date_text(dates.min()) if not dates.empty else ""
    result["end_date"] = _date_text(dates.max()) if not dates.empty else ""
    result["end_date_gap_days"] = _end_date_gap_days(result["end_date"], latest_expected_date)
    result["duplicate_dates_count"] = int(dates.duplicated().sum()) if not dates.empty else 0
    if missing:
        return result

    normalized = pd.DataFrame({name: raw[column] for name, column in picked.items() if column is not None})
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    result["missing_values_count"] = int(normalized[INDEX_DIAGNOSTIC_REQUIRED_COLUMNS].isna().any(axis=1).sum())
    close = pd.to_numeric(normalized["close"], errors="coerce")
    result["abnormal_return_count"] = int((close.pct_change().abs() > ABNORMAL_RETURN_THRESHOLD).sum())
    if normalized["date"].isna().any():
        result["schema_valid"] = False
        result["failure_type"] = "schema_error"
        result["failure_reason"] = "unparseable dates"
    return result


def diagnose_index_api_candidate(
    index_code: str,
    index_name: str = "",
    *,
    api_name: str = "akshare.index_zh_a_hist",
    source_family: str | None = None,
    fetcher: IndexFetcher | None = None,
    start_date: str = "20190101",
    end_date: str | None = None,
    run_id: str | None = None,
    checked_at: str | None = None,
    latest_expected_date: str | None = None,
    ak_module: Any | None = None,
    notes: str = "",
) -> dict[str, Any]:
    candidate = next((item for item in INDEX_API_CANDIDATES if item.api_name == api_name), None)
    if candidate is None and fetcher is None:
        raise ValueError(f"unknown index API candidate: {api_name}")
    current_run_id = run_id or "index_source_diag_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    current_checked_at = checked_at or _now_text()
    expected = latest_expected_date or _latest_expected_date()
    family = source_family or (candidate.source_family if candidate else "unknown")
    fetch = fetcher or candidate.fetcher  # type: ignore[union-attr]
    row = _base_row(
        run_id=current_run_id,
        checked_at=current_checked_at,
        index_code=index_code,
        index_name=index_name,
        api_name=api_name,
        source_family=family,
        latest_expected_date=expected,
        notes="; ".join(item for item in [notes, candidate.notes if candidate else ""] if item),
    )
    started = time.perf_counter()
    try:
        if ak_module is None:
            import akshare as ak_module
        raw = fetch(str(index_code).strip().zfill(6), start_date, end_date, ak_module)
        row["call_success"] = True
        row.update(_evaluate_schema(raw, latest_expected_date=expected))
    except BaseException as exc:  # noqa: BLE001
        status_code = _status_code_from_exception(exc)
        row.update(
            {
                "call_success": False,
                "status_code": status_code,
                "failure_type": _classify_failure(exc, status_code=status_code),
                "failure_reason": str(exc),
            }
        )
    row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    row["usable_as_index_source"] = bool(
        row["call_success"]
        and row["schema_valid"]
        and int(row.get("row_count") or 0) > 0
        and int(row.get("missing_values_count") or 0) == 0
        and int(row.get("end_date_gap_days") or 0) <= 7
    )
    if row["call_success"] and not row["usable_as_index_source"] and not row.get("failure_type"):
        if int(row.get("missing_values_count") or 0) > 0:
            row["failure_type"] = "schema_error"
            row["failure_reason"] = "missing values in required OHLCV/date fields"
        elif int(row.get("duplicate_dates_count") or 0) > 0:
            row["failure_type"] = "schema_error"
            row["failure_reason"] = "duplicate dates in returned history"
        elif int(row.get("end_date_gap_days") or 0) > 7:
            row["failure_type"] = "unknown"
            row["failure_reason"] = "candidate end date is stale versus latest expected trading day"
        else:
            row["failure_type"] = "unknown"
            row["failure_reason"] = "candidate returned data but did not meet usability rules"
    row["requires_manual_review"] = not bool(row["usable_as_index_source"])
    row["suggested_action"] = _suggested_action(row)
    return {column: row.get(column, "") for column in INDEX_SOURCE_DIAGNOSTICS_COLUMNS}


def diagnose_index_source_candidates(
    *,
    index_codes: str | list[str] | None = None,
    max_count: int = DEFAULT_INDEX_SOURCE_DIAGNOSTIC_LIMIT,
    start_date: str = "20190101",
    end_date: str | None = None,
    index_map_path: str | Path = DEFAULT_INDEX_MAP_PATH,
    ak_module: Any | None = None,
    candidates: list[IndexApiCandidate] | None = None,
) -> list[dict[str, Any]]:
    targets = build_index_diagnostic_targets(index_codes=index_codes, index_map_path=index_map_path, max_count=max_count)
    selected_candidates = candidates or INDEX_API_CANDIDATES
    run_id = "index_source_diag_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    checked_at = _now_text()
    expected = _latest_expected_date()
    rows: list[dict[str, Any]] = []
    for target in targets:
        for candidate in selected_candidates:
            rows.append(
                diagnose_index_api_candidate(
                    target["index_code"],
                    target.get("index_name", ""),
                    api_name=candidate.api_name,
                    source_family=candidate.source_family,
                    fetcher=candidate.fetcher,
                    start_date=start_date,
                    end_date=end_date,
                    run_id=run_id,
                    checked_at=checked_at,
                    latest_expected_date=expected,
                    ak_module=ak_module,
                    notes=candidate.notes,
                )
            )
    return rows


def write_index_source_diagnostics_report(
    rows: list[dict[str, Any]],
    path: str | Path = DEFAULT_INDEX_SOURCE_DIAGNOSTICS_OUTPUT,
) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=INDEX_SOURCE_DIAGNOSTICS_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def _bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "yes", "y"])


def summarize_index_source_diagnostics(
    rows: list[dict[str, Any]] | None = None,
    report_path: str | Path = DEFAULT_INDEX_SOURCE_DIAGNOSTICS_OUTPUT,
    example_limit: int = 10,
) -> dict[str, Any]:
    path = Path(report_path)
    if rows is None:
        if not path.exists():
            return {
                "status": "not_run",
                "index_source_diagnostics_report": str(path),
                "total_indexes_checked": 0,
                "total_api_candidates": 0,
                "success_count": 0,
                "usable_source_count": 0,
                "eastmoney_failure_count": 0,
                "proxy_error_count": 0,
                "timeout_count": 0,
                "preferred_api_candidates": [],
                "top_examples": [],
                "suggested_action": "run diagnose-index-source before rerunning update-index-data or starting ETF-GAP-007",
            }
        frame = pd.read_csv(path, dtype={"index_code": str}, encoding="utf-8-sig").fillna("")
    else:
        frame = pd.DataFrame(rows).fillna("")
    if frame.empty:
        return {
            "status": "not_run",
            "index_source_diagnostics_report": str(path),
            "total_indexes_checked": 0,
            "total_api_candidates": 0,
            "success_count": 0,
            "usable_source_count": 0,
            "eastmoney_failure_count": 0,
            "proxy_error_count": 0,
            "timeout_count": 0,
            "preferred_api_candidates": [],
            "top_examples": [],
            "suggested_action": "run diagnose-index-source before rerunning update-index-data or starting ETF-GAP-007",
        }

    success = _bool_series(frame["call_success"]) if "call_success" in frame.columns else pd.Series(False, index=frame.index)
    usable = _bool_series(frame["usable_as_index_source"]) if "usable_as_index_source" in frame.columns else pd.Series(False, index=frame.index)
    source_family = frame.get("source_family", pd.Series("", index=frame.index)).astype(str)
    failure_type = frame.get("failure_type", pd.Series("", index=frame.index)).astype(str)
    eastmoney_failure = (~success) & source_family.eq("eastmoney")
    proxy_errors = (~success) & failure_type.eq("proxy_error")
    timeouts = (~success) & failure_type.eq("timeout")
    preferred = (
        frame[usable]
        .sort_values(["index_code", "end_date_gap_days", "row_count"], ascending=[True, True, False])
        .drop_duplicates("index_code")[["index_code", "index_name", "api_name", "source_family", "row_count", "end_date"]]
        .to_dict("records")
        if usable.any()
        else []
    )
    if preferred:
        action = "review preferred candidates, then rerun update-index-data with an explicitly chosen source path"
    elif int(proxy_errors.sum()) > 0:
        action = "fix proxy/network reachability first; do not compute tracking error from missing benchmarks"
    elif int(timeouts.sum()) > 0:
        action = "retry diagnostics later or with lower request rate; do not start full ETF-GAP-007 yet"
    else:
        action = "no usable index source found; keep only interface/report skeletons until a real source succeeds"
    examples = frame[~usable].head(example_limit)[
        ["index_code", "index_name", "api_name", "source_family", "call_success", "failure_type", "failure_reason", "suggested_action"]
    ].to_dict("records")
    return {
        "status": "ok",
        "index_source_diagnostics_report": str(path),
        "total_indexes_checked": int(frame["index_code"].astype(str).nunique()) if "index_code" in frame.columns else 0,
        "total_api_candidates": int(len(frame)),
        "success_count": int(success.sum()),
        "usable_source_count": int(usable.sum()),
        "eastmoney_failure_count": int(eastmoney_failure.sum()),
        "proxy_error_count": int(proxy_errors.sum()),
        "timeout_count": int(timeouts.sum()),
        "preferred_api_candidates": preferred,
        "top_examples": examples,
        "suggested_action": action,
    }
