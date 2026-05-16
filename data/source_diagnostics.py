from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import pandas as pd

from data.downloader import normalize_source_frame
from data.source_preference import build_source_eval_symbols


SOURCE_DIAGNOSTICS_REPORT_COLUMNS = [
    "run_id",
    "checked_at",
    "symbol",
    "check_type",
    "endpoint",
    "proxy_env_detected",
    "http_proxy",
    "https_proxy",
    "akshare_call",
    "adjust",
    "success",
    "status_code",
    "row_count",
    "error_type",
    "error_message",
    "elapsed_ms",
    "retry_count",
    "diagnosis",
    "suggested_action",
]

DEFAULT_DIAGNOSTIC_SYMBOL_LIMIT = 5
EASTMONEY_KLINE_ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


AkshareCaller = Callable[[str, str, str | None], pd.DataFrame]
EndpointGetter = Callable[..., Any]


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sina_symbol(symbol: str) -> str:
    return f"sh{symbol}" if str(symbol).startswith(("5", "6")) else f"sz{symbol}"


def _em_secid(symbol: str) -> str:
    symbol = str(symbol).zfill(6)
    market = "1" if symbol.startswith(("5", "6")) else "0"
    return f"{market}.{symbol}"


def _proxy_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in os.environ.items():
        lower = key.lower()
        if lower in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
            values[lower] = str(value)
    return values


def _proxy_fields() -> dict[str, Any]:
    values = _proxy_env()
    return {
        "proxy_env_detected": bool(values.get("http_proxy") or values.get("https_proxy") or values.get("all_proxy")),
        "http_proxy": values.get("http_proxy", ""),
        "https_proxy": values.get("https_proxy", ""),
    }


def _base_row(run_id: str, check_type: str, symbol: str = "", *, checked_at: str | None = None) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "checked_at": checked_at or _now_text(),
        "symbol": str(symbol).zfill(6) if symbol else "",
        "check_type": check_type,
        "endpoint": "",
        **_proxy_fields(),
        "akshare_call": "",
        "adjust": "",
        "success": False,
        "status_code": "",
        "row_count": 0,
        "error_type": "",
        "error_message": "",
        "elapsed_ms": 0,
        "retry_count": 0,
        "diagnosis": "",
        "suggested_action": "",
    }


def _error_type(exc: BaseException) -> str:
    return exc.__class__.__name__


def _classify_failure(error_type: str, error_message: str, status_code: Any = "") -> tuple[str, str]:
    text = f"{error_type} {error_message}".lower()
    numeric_status = pd.to_numeric(status_code, errors="coerce")
    if not pd.isna(numeric_status) and int(numeric_status) >= 400:
        return "http_error", "check EastMoney endpoint availability and response body before retrying 003F"
    if "proxyerror" in text or "proxy" in text or "remote end closed connection" in text:
        return "proxy_or_network_blocked", "inspect HTTP_PROXY/HTTPS_PROXY/ALL_PROXY and test EastMoney reachability outside AKShare"
    if "timeout" in text or "timed out" in text:
        return "timeout_or_endpoint_slow", "retry with a longer timeout and lower request rate before changing source priority"
    if "unexpected keyword" in text or "positional argument" in text or "got an unexpected" in text:
        return "akshare_parameter_error", "verify installed AKShare fund_etf_hist_em signature and adjust parameter support"
    if "incompatible source fields" in text or "empty data" in text or "no valid rows" in text:
        return "akshare_schema_or_empty_data", "inspect AKShare response fields and symbol availability before trusting this path"
    return "api_or_network_error", "keep Sina as current primary path and preserve the full error for manual diagnosis"


def _row_success(row: dict[str, Any], row_count: int = 0, status_code: Any = "") -> dict[str, Any]:
    row.update(
        {
            "success": True,
            "status_code": status_code,
            "row_count": int(row_count),
            "error_type": "",
            "error_message": "",
            "diagnosis": "ok",
            "suggested_action": "source path is reachable in this diagnostic run",
        }
    )
    return row


def _row_failure(row: dict[str, Any], exc: BaseException, *, status_code: Any = "") -> dict[str, Any]:
    error_type = _error_type(exc)
    error_message = str(exc)
    diagnosis, action = _classify_failure(error_type, error_message, status_code)
    row.update(
        {
            "success": False,
            "status_code": status_code,
            "row_count": 0,
            "error_type": error_type,
            "error_message": error_message,
            "diagnosis": diagnosis,
            "suggested_action": action,
        }
    )
    return row


def _call_with_retries(fetcher: Callable[[], Any], retries: int) -> tuple[Any, int]:
    attempts = max(1, int(retries))
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fetcher(), attempt - 1
        except BaseException as exc:  # noqa: BLE001
            last_error = exc
            if attempt < attempts:
                time.sleep(0.2 * attempt)
    assert last_error is not None
    raise last_error


def diagnose_proxy_environment(run_id: str | None = None) -> list[dict[str, Any]]:
    current_run_id = run_id or "source_diag_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    row = _base_row(current_run_id, "proxy_env")
    proxy_detected = bool(row["proxy_env_detected"])
    row.update(
        {
            "success": True,
            "diagnosis": "proxy_env_detected" if proxy_detected else "no_proxy_env_detected",
            "suggested_action": (
                "confirm whether proxy can reach push2his.eastmoney.com"
                if proxy_detected
                else "proxy environment variables are not the immediate cause"
            ),
        }
    )
    return [{column: row.get(column, "") for column in SOURCE_DIAGNOSTICS_REPORT_COLUMNS}]


def _fetch_sina(symbol: str, start_date: str, end_date: str | None, ak_module: Any) -> pd.DataFrame:
    raw = ak_module.fund_etf_hist_sina(symbol=_sina_symbol(symbol))
    frame = normalize_source_frame(symbol, raw)
    start = pd.to_datetime(start_date, errors="coerce")
    end = pd.to_datetime(end_date or datetime.now().strftime("%Y%m%d"), errors="coerce")
    if not pd.isna(start):
        frame = frame[frame["date"] >= start]
    if not pd.isna(end):
        frame = frame[frame["date"] <= end]
    return frame.reset_index(drop=True)


def _fetch_em(symbol: str, start_date: str, end_date: str | None, adjust: str, ak_module: Any) -> pd.DataFrame:
    raw = ak_module.fund_etf_hist_em(
        symbol=str(symbol).zfill(6),
        period="daily",
        start_date=start_date,
        end_date=end_date or datetime.now().strftime("%Y%m%d"),
        adjust=adjust,
    )
    return normalize_source_frame(symbol, raw)


def diagnose_akshare_em_call(
    symbol: str,
    *,
    adjust: str = "qfq",
    run_id: str | None = None,
    start_date: str = "20190101",
    end_date: str | None = None,
    retries: int = 1,
    ak_module: Any | None = None,
) -> list[dict[str, Any]]:
    current_run_id = run_id or "source_diag_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    check_type = "akshare_em_qfq" if adjust == "qfq" else "akshare_em_none"
    row = _base_row(current_run_id, check_type, symbol)
    row.update(
        {
            "endpoint": EASTMONEY_KLINE_ENDPOINT,
            "akshare_call": "fund_etf_hist_em",
            "adjust": adjust or "none",
        }
    )
    started = time.perf_counter()
    try:
        if ak_module is None:
            import akshare as ak_module

        frame, retry_count = _call_with_retries(lambda: _fetch_em(symbol, start_date, end_date, adjust, ak_module), retries)
        row["retry_count"] = retry_count
        row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return [{column: _row_success(row, len(frame)).get(column, "") for column in SOURCE_DIAGNOSTICS_REPORT_COLUMNS}]
    except BaseException as exc:  # noqa: BLE001
        row["retry_count"] = max(0, int(retries) - 1)
        row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return [{column: _row_failure(row, exc).get(column, "") for column in SOURCE_DIAGNOSTICS_REPORT_COLUMNS}]


def diagnose_akshare_sina_call(
    symbol: str,
    *,
    run_id: str | None = None,
    start_date: str = "20190101",
    end_date: str | None = None,
    retries: int = 1,
    ak_module: Any | None = None,
) -> list[dict[str, Any]]:
    current_run_id = run_id or "source_diag_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    row = _base_row(current_run_id, "akshare_sina", symbol)
    row.update({"akshare_call": "fund_etf_hist_sina", "adjust": "unknown"})
    started = time.perf_counter()
    try:
        if ak_module is None:
            import akshare as ak_module

        frame, retry_count = _call_with_retries(lambda: _fetch_sina(symbol, start_date, end_date, ak_module), retries)
        row["retry_count"] = retry_count
        row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return [{column: _row_success(row, len(frame)).get(column, "") for column in SOURCE_DIAGNOSTICS_REPORT_COLUMNS}]
    except BaseException as exc:  # noqa: BLE001
        row["retry_count"] = max(0, int(retries) - 1)
        row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return [{column: _row_failure(row, exc).get(column, "") for column in SOURCE_DIAGNOSTICS_REPORT_COLUMNS}]


def _raw_em_url(symbol: str, start_date: str, end_date: str | None, adjust: str) -> str:
    fqt = "1" if adjust == "qfq" else "0"
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": "101",
        "fqt": fqt,
        "beg": start_date,
        "end": end_date or datetime.now().strftime("%Y%m%d"),
        "secid": _em_secid(symbol),
    }
    return EASTMONEY_KLINE_ENDPOINT + "?" + urlencode(params)


def diagnose_em_endpoint(
    symbol: str,
    *,
    adjust: str = "qfq",
    run_id: str | None = None,
    start_date: str = "20190101",
    end_date: str | None = None,
    timeout: float = 8.0,
    retries: int = 1,
    endpoint_getter: EndpointGetter | None = None,
) -> list[dict[str, Any]]:
    current_run_id = run_id or "source_diag_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    url = _raw_em_url(symbol, start_date, end_date, adjust)
    row = _base_row(current_run_id, "raw_endpoint_probe", symbol)
    row.update({"endpoint": url, "adjust": adjust or "none"})
    started = time.perf_counter()
    try:
        if endpoint_getter is None:
            import requests

            endpoint_getter = requests.get
        response, retry_count = _call_with_retries(lambda: endpoint_getter(url, timeout=timeout), retries)
        row["retry_count"] = retry_count
        status_code = getattr(response, "status_code", "")
        if int(status_code or 0) >= 400:
            raise RuntimeError(f"HTTP {status_code}")
        row_count = 0
        try:
            payload = response.json()
            klines = (((payload or {}).get("data") or {}).get("klines") or [])
            row_count = len(klines)
        except Exception:
            row_count = 0
        row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return [{column: _row_success(row, row_count, status_code).get(column, "") for column in SOURCE_DIAGNOSTICS_REPORT_COLUMNS}]
    except BaseException as exc:  # noqa: BLE001
        row["retry_count"] = max(0, int(retries) - 1)
        status_code = getattr(getattr(exc, "response", None), "status_code", "")
        if not status_code and str(exc).startswith("HTTP "):
            status_code = str(exc).split(" ", 1)[1]
        row["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return [{column: _row_failure(row, exc, status_code=status_code).get(column, "") for column in SOURCE_DIAGNOSTICS_REPORT_COLUMNS}]


def write_source_diagnostics_report(rows: list[dict[str, Any]], path: str | Path = "output/source_diagnostics_report.csv") -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=SOURCE_DIAGNOSTICS_REPORT_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def _bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "yes", "y"])


def summarize_source_diagnostics(
    rows: list[dict[str, Any]] | None = None,
    report_path: str | Path = "output/source_diagnostics_report.csv",
    example_limit: int = 10,
) -> dict[str, Any]:
    path = Path(report_path)
    if rows is None:
        if not path.exists():
            return {
                "status": "not_run",
                "report": str(path),
                "total_symbols": 0,
                "total_checks": 0,
                "em_qfq_success_count": 0,
                "em_none_success_count": 0,
                "sina_success_count": 0,
                "proxy_error_count": 0,
                "timeout_count": 0,
                "suggested_action": "run diagnose-source before reconsidering EM qfq promotion",
                "top_examples": [],
            }
        frame = pd.read_csv(path, dtype={"symbol": str}, encoding="utf-8-sig").fillna("")
    else:
        frame = pd.DataFrame(rows).fillna("")
    if frame.empty:
        return {
            "status": "not_run",
            "report": str(path),
            "total_symbols": 0,
            "total_checks": 0,
            "em_qfq_success_count": 0,
            "em_none_success_count": 0,
            "sina_success_count": 0,
            "proxy_error_count": 0,
            "timeout_count": 0,
            "suggested_action": "run diagnose-source before reconsidering EM qfq promotion",
            "top_examples": [],
        }

    success = _bool_series(frame["success"]) if "success" in frame.columns else pd.Series(False, index=frame.index)
    check_type = frame.get("check_type", pd.Series("", index=frame.index)).astype(str)
    diagnosis = frame.get("diagnosis", pd.Series("", index=frame.index)).astype(str)
    error_type = frame.get("error_type", pd.Series("", index=frame.index)).astype(str)
    proxy_errors = ~success & (diagnosis.str.contains("proxy", case=False, na=False) | error_type.str.contains("ProxyError", case=False, na=False))
    timeouts = ~success & (diagnosis.str.contains("timeout", case=False, na=False) | error_type.str.contains("Timeout", case=False, na=False))
    em_qfq_success = int((check_type.eq("akshare_em_qfq") & success).sum())
    em_none_success = int((check_type.eq("akshare_em_none") & success).sum())
    sina_success = int((check_type.eq("akshare_sina") & success).sum())
    proxy_count = int(proxy_errors.sum())
    timeout_count = int(timeouts.sum())
    if em_qfq_success > 0 and proxy_count == 0 and timeout_count == 0:
        action = "rerun ETF-GAP-003F source preference evaluation before considering ETF-GAP-003G"
    elif proxy_count > 0:
        action = "fix proxy or EastMoney reachability first; keep Sina as current primary path"
    elif timeout_count > 0:
        action = "retry diagnostics with longer timeout and lower request rate; keep Sina primary until stable"
    else:
        action = "keep Sina primary and inspect preserved AKShare/endpoint errors before rerunning 003F"
    examples = frame[~success].head(example_limit)[
        ["symbol", "check_type", "success", "error_type", "error_message", "diagnosis", "suggested_action"]
    ].to_dict("records")
    return {
        "status": "ok",
        "report": str(path),
        "total_symbols": int(frame[frame["symbol"].astype(str).str.strip() != ""]["symbol"].astype(str).nunique()) if "symbol" in frame.columns else 0,
        "total_checks": int(len(frame)),
        "em_qfq_success_count": em_qfq_success,
        "em_none_success_count": em_none_success,
        "sina_success_count": sina_success,
        "proxy_error_count": proxy_count,
        "timeout_count": timeout_count,
        "suggested_action": action,
        "top_examples": examples,
    }


def run_source_diagnostics(
    *,
    symbols: str | list[str] | None = None,
    max_count: int = DEFAULT_DIAGNOSTIC_SYMBOL_LIMIT,
    start_date: str = "20190101",
    end_date: str | None = None,
    output_dir: str | Path = "output",
    config_path: str | Path = "config/etf_universe.yaml",
    retries: int = 1,
    timeout: float = 8.0,
    ak_module: Any | None = None,
    endpoint_getter: EndpointGetter | None = None,
) -> tuple[list[dict[str, Any]], Path]:
    if max_count > DEFAULT_DIAGNOSTIC_SYMBOL_LIMIT and not symbols:
        raise ValueError(f"diagnose-source default selection refuses max_count > {DEFAULT_DIAGNOSTIC_SYMBOL_LIMIT} without explicit --symbols")
    selected = build_source_eval_symbols(symbols=symbols, max_count=min(max_count, 20), config_path=config_path)[:max_count]
    run_id = "source_diag_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    rows: list[dict[str, Any]] = []
    rows.extend(diagnose_proxy_environment(run_id))
    for item in selected:
        symbol = str(item["symbol"]).zfill(6)
        rows.extend(diagnose_akshare_sina_call(symbol, run_id=run_id, start_date=start_date, end_date=end_date, retries=retries, ak_module=ak_module))
        rows.extend(diagnose_akshare_em_call(symbol, adjust="qfq", run_id=run_id, start_date=start_date, end_date=end_date, retries=retries, ak_module=ak_module))
        rows.extend(diagnose_akshare_em_call(symbol, adjust="", run_id=run_id, start_date=start_date, end_date=end_date, retries=retries, ak_module=ak_module))
        rows.extend(
            diagnose_em_endpoint(
                symbol,
                adjust="qfq",
                run_id=run_id,
                start_date=start_date,
                end_date=end_date,
                timeout=timeout,
                retries=retries,
                endpoint_getter=endpoint_getter,
            )
        )
    report_path = write_source_diagnostics_report(rows, Path(output_dir) / "source_diagnostics_report.csv")
    return rows, report_path
