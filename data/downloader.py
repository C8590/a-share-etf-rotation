from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time
from typing import Any, Callable

import pandas as pd
import yaml

from data.quality import analyze_single_etf
from data.storage import get_csv_path, load_etf_data, save_etf_data


COVERAGE_REPORT_PATH = Path("output") / "data_coverage_report.csv"

FIELD_ALIASES = {
    "date": ["日期", "date", "Date"],
    "open": ["开盘", "open", "Open"],
    "high": ["最高", "high", "High"],
    "low": ["最低", "low", "Low"],
    "close": ["收盘", "close", "Close"],
    "volume": ["成交量", "volume", "Volume"],
    "amount": ["成交额", "amount", "Amount"],
}


@dataclass
class DataStatus:
    symbol: str
    name: str
    success: bool
    source: str = ""
    start_date: str = ""
    end_date: str = ""
    rows: int = 0
    missing_count: int = 0
    duplicate_count: int = 0
    latest_date: str = ""
    status: str = "failed"
    failure_reason: str = ""
    cached: bool = False

    @property
    def error(self) -> str:
        return self.failure_reason

    def to_row(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "success": self.success,
            "source": self.source,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "rows": self.rows,
            "missing_count": self.missing_count,
            "duplicate_count": self.duplicate_count,
            "latest_date": self.latest_date,
            "status": self.status,
            "failure_reason": self.failure_reason,
        }


def load_etf_pool(config_path: str | Path = "config/etf_pool.yaml") -> list[dict[str, str]]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"ETF pool config not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    etfs = config.get("etfs", [])
    if not isinstance(etfs, list) or not etfs:
        raise ValueError(f"{path} has no etfs list")

    normalized = []
    for item in etfs:
        symbol = str(item.get("symbol", "")).zfill(6)
        name = str(item.get("name", "")).strip()
        category = str(item.get("category", "")).strip()
        if not symbol or not name:
            raise ValueError(f"ETF pool item missing symbol or name: {item}")
        normalized.append({"symbol": symbol, "name": name, "category": category})
    return normalized


def _pick_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    normalized = {str(col).strip(): col for col in df.columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def normalize_source_frame(symbol: str, raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise ValueError(f"{symbol} source returned empty data")

    data: dict[str, Any] = {}
    missing: list[str] = []
    for target_col, aliases in FIELD_ALIASES.items():
        source_col = _pick_column(raw, aliases)
        if source_col is None:
            missing.append(f"{target_col}({','.join(aliases)})")
            continue
        data[target_col] = raw[source_col]

    if missing:
        available = ", ".join(map(str, raw.columns))
        raise ValueError(f"{symbol} incompatible source fields; missing {', '.join(missing)}; available: {available}")

    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date", "open", "close"]).sort_values("date")
    if df.empty:
        raise ValueError(f"{symbol} has no valid rows after cleaning")
    return df


def _sina_symbol(symbol: str) -> str:
    return f"sh{symbol}" if symbol.startswith(("5", "6")) else f"sz{symbol}"


def _date_chunks(start_date: str, end_date: str) -> list[tuple[str, str]]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    chunks: list[tuple[str, str]] = []
    year = start.year
    while year <= end.year:
        chunk_start = max(start, datetime(year, 1, 1).date())
        chunk_end = min(end, datetime(year, 12, 31).date())
        chunks.append((chunk_start.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
        year += 1
    return chunks


def _retry_call(label: str, fetcher: Callable[[], pd.DataFrame], retries: int, retry_delay: float) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            print(f"    source={label} attempt={attempt}/{retries}")
            return fetcher()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"    source={label} failed: {exc}")
            if attempt < retries:
                time.sleep(retry_delay * attempt)
    raise RuntimeError(f"{label} failed after {retries} attempts: {last_error}") from last_error


def _download_em_chunked(
    ak: Any,
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str,
    retries: int,
    retry_delay: float,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for chunk_start, chunk_end in _date_chunks(start_date, end_date):
        label = f"fund_etf_hist_em(adjust={adjust or 'none'}, {chunk_start}-{chunk_end})"
        raw = _retry_call(
            label,
            lambda cs=chunk_start, ce=chunk_end: ak.fund_etf_hist_em(
                symbol=symbol,
                period="daily",
                start_date=cs,
                end_date=ce,
                adjust=adjust,
            ),
            retries=retries,
            retry_delay=retry_delay,
        )
        frames.append(normalize_source_frame(symbol, raw))
        time.sleep(0.2)

    merged = pd.concat(frames, ignore_index=True)
    return merged.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def _download_sina(
    ak: Any,
    symbol: str,
    start_date: str,
    end_date: str,
    retries: int,
    retry_delay: float,
) -> pd.DataFrame:
    raw = _retry_call(
        "fund_etf_hist_sina",
        lambda: ak.fund_etf_hist_sina(symbol=_sina_symbol(symbol)),
        retries=retries,
        retry_delay=retry_delay,
    )
    df = normalize_source_frame(symbol, raw)
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


def download_etf_history(
    symbol: str,
    start_date: str = "20190101",
    end_date: str | None = None,
    retries: int = 3,
    retry_delay: float = 2.0,
) -> tuple[pd.DataFrame, str]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise ImportError("AKShare is not installed; run pip install -r requirements.txt") from exc

    end = end_date or datetime.now().strftime("%Y%m%d")
    errors: list[str] = []
    for attempt in range(1, retries + 1):
        print(f"    ETF download attempt={attempt}/{retries}")
        sources: list[tuple[str, Callable[[], pd.DataFrame]]] = [
            ("akshare.fund_etf_hist_em.qfq", lambda: _download_em_chunked(ak, symbol, start_date, end, "qfq", 1, retry_delay)),
            ("akshare.fund_etf_hist_em.none", lambda: _download_em_chunked(ak, symbol, start_date, end, "", 1, retry_delay)),
            ("akshare.fund_etf_hist_sina", lambda: _download_sina(ak, symbol, start_date, end, 1, retry_delay)),
        ]

        for source_name, fetcher in sources:
            try:
                df = fetcher()
                if df.empty:
                    raise ValueError("source returned empty data")
                return df, source_name
            except Exception as exc:  # noqa: BLE001
                msg = f"{source_name}: {exc}"
                errors.append(msg)
                print(f"    fallback after {source_name} failed: {exc}")

        if attempt < retries:
            time.sleep(retry_delay * attempt)

    raise RuntimeError(f"all data sources failed after {retries} attempts: {' | '.join(errors)}")


def _status_from_df(symbol: str, name: str, df: pd.DataFrame, source: str, cached: bool) -> DataStatus:
    frame = df.reset_index() if "date" not in df.columns else df.copy()
    quality = analyze_single_etf(symbol, name, frame)
    dates = pd.to_datetime(frame["date"] if "date" in frame.columns else frame.index)
    return DataStatus(
        symbol=symbol,
        name=name,
        success=quality.status != "failed",
        source=source,
        start_date=str(dates.min().date()) if len(dates) else "",
        end_date=str(dates.max().date()) if len(dates) else "",
        rows=int(len(frame)),
        missing_count=quality.missing_count,
        duplicate_count=quality.duplicate_count,
        latest_date=str(dates.max().date()) if len(dates) else "",
        status=quality.status,
        failure_reason="; ".join(quality.errors),
        cached=cached,
    )


def _load_previous_failures(path: Path = COVERAGE_REPORT_PATH) -> set[str]:
    if not path.exists():
        return set()
    try:
        report = pd.read_csv(path, dtype={"symbol": str, "ETF代码": str}).fillna("")
    except Exception:
        return set()
    if "symbol" in report.columns:
        failed = report[report.get("success", False).astype(str).str.lower().isin(["false", "0", "no", "否"])]
        return set(failed["symbol"].astype(str).str.zfill(6).tolist())
    if "ETF代码" in report.columns:
        ok_col = "是否下载成功"
        failed = report[report.get(ok_col, "") != "是"]
        return set(failed["ETF代码"].astype(str).str.zfill(6).tolist())
    return set()


def write_coverage_report(statuses: list[DataStatus], path: Path = COVERAGE_REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([status.to_row() for status in statuses]).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def build_data_coverage_report(
    etf_pool: list[dict[str, str]],
    path: Path = COVERAGE_REPORT_PATH,
) -> list[DataStatus]:
    statuses: list[DataStatus] = []
    previous_errors: dict[str, str] = {}
    if path.exists():
        try:
            old = pd.read_csv(path, dtype={"symbol": str, "ETF代码": str}).fillna("")
            if "symbol" in old.columns:
                previous_errors = {str(row["symbol"]).zfill(6): str(row.get("failure_reason", "")) for _, row in old.iterrows()}
            elif "ETF代码" in old.columns:
                previous_errors = {str(row["ETF代码"]).zfill(6): str(row.get("失败原因", "")) for _, row in old.iterrows()}
        except Exception:
            previous_errors = {}

    for etf in etf_pool:
        symbol = etf["symbol"]
        name = etf["name"]
        try:
            df = load_etf_data(symbol, name=name).reset_index()
            save_etf_data(symbol, df, name=name, source="local_cache")
            statuses.append(_status_from_df(symbol, name, df, "local_cache", cached=True))
        except Exception as exc:  # noqa: BLE001
            error = previous_errors.get(symbol) or str(exc)
            statuses.append(DataStatus(symbol=symbol, name=name, success=False, status="failed", failure_reason=error))

    write_coverage_report(statuses, path)
    return statuses


def update_all_data(
    etf_pool: list[dict[str, str]],
    start_date: str,
    end_date: str | None = None,
    refresh: bool = False,
    retry_failed_only: bool = False,
) -> tuple[list[str], list[str], list[DataStatus]]:
    successes: list[str] = []
    errors: list[str] = []
    statuses: list[DataStatus] = []
    failed_symbols = _load_previous_failures() if retry_failed_only else set()

    targets = etf_pool
    if retry_failed_only:
        targets = [etf for etf in etf_pool if etf["symbol"] in failed_symbols or not get_csv_path(etf["symbol"]).exists()]

    if retry_failed_only and not targets:
        print("No failed or missing ETFs found for retry.")
        statuses = build_data_coverage_report(etf_pool)
        return successes, errors, statuses

    total = len(targets)
    for idx, etf in enumerate(targets, start=1):
        symbol = etf["symbol"]
        name = etf["name"]
        print(f"[{idx}/{total}] ETF {symbol} {name}")

        if not refresh and get_csv_path(symbol).exists() and not retry_failed_only:
            try:
                df = load_etf_data(symbol, name=name).reset_index()
                save_etf_data(symbol, df, name=name, source="local_cache")
                status = _status_from_df(symbol, name, df, "local_cache", cached=True)
                statuses.append(status)
                msg = f"{symbol} {name}: cache rows={status.rows} range={status.start_date}->{status.end_date} status={status.status}"
                successes.append(msg)
                print(f"    OK source=local_cache rows={status.rows}")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"    cache unusable; refreshing. reason={exc}")

        try:
            df, source = download_etf_history(symbol=symbol, start_date=start_date, end_date=end_date)
            path = save_etf_data(symbol, df, name=name, source=source)
            status = _status_from_df(symbol, name, df, source, cached=False)
            statuses.append(status)
            msg = f"{symbol} {name}: downloaded source={source} rows={status.rows} range={status.start_date}->{status.end_date} path={path}"
            successes.append(msg)
            print(f"    OK source={source} rows={status.rows}")
        except Exception as exc:  # noqa: BLE001
            reason = str(exc)
            statuses.append(DataStatus(symbol=symbol, name=name, success=False, status="failed", failure_reason=reason))
            errors.append(f"{symbol} {name}: {reason}")
            print(f"    ERR {symbol} {name}: {reason}")
        time.sleep(0.8)

    status_by_symbol = {status.symbol: status for status in statuses}
    for etf in etf_pool:
        symbol = etf["symbol"]
        if symbol in status_by_symbol:
            continue
        try:
            df = load_etf_data(symbol, name=etf["name"]).reset_index()
            save_etf_data(symbol, df, name=etf["name"], source="local_cache")
            status_by_symbol[symbol] = _status_from_df(symbol, etf["name"], df, "local_cache", cached=True)
        except Exception as exc:  # noqa: BLE001
            status_by_symbol[symbol] = DataStatus(symbol=symbol, name=etf["name"], success=False, status="failed", failure_reason=str(exc))

    ordered_statuses = [status_by_symbol[etf["symbol"]] for etf in etf_pool]
    write_coverage_report(ordered_statuses)
    return successes, errors, ordered_statuses
