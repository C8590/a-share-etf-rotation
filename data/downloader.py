from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import threading
import time
from typing import Any, Callable

import pandas as pd
import yaml

from data.quality import analyze_single_etf
from data.storage import build_cache_metadata, get_csv_path, load_etf_data, save_etf_data, write_cache_metadata
from data.trading_calendar import latest_trading_day_on_or_before
from data.universe import load_market_etf_universe, universe_records, write_universe_snapshot


COVERAGE_REPORT_PATH = Path("output") / "data_coverage_report.csv"
FAILED_LOG_PATH = Path("logs") / "update_failed.csv"
AKSHARE_DOWNLOAD_LOCK = threading.Lock()
DEFAULT_UNIVERSE_PATH = Path("config") / "etf_universe.yaml"
LEGACY_POOL_PATH = Path("config") / "etf_pool.yaml"

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
    exchange: str = ""
    asset_class: str = ""
    category: str = ""
    tracking_index: str = ""
    listing_date: str = ""
    latest_date: str = ""
    avg_amount_20: float | None = None
    data_rows: int = 0
    is_active: bool = False
    filter_reason: str = ""
    theme: str = ""
    sector: str = ""
    source: str = ""
    start_date: str = ""
    end_date: str = ""
    rows: int = 0
    missing_count: int = 0
    duplicate_count: int = 0
    status: str = "failed"
    failure_reason: str = ""
    cached: bool = False
    local_latest_date: str = ""
    target_update_date: str = ""

    @property
    def error(self) -> str:
        return self.failure_reason

    def to_row(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "exchange": self.exchange,
            "asset_class": self.asset_class,
            "category": self.category,
            "tracking_index": self.tracking_index,
            "listing_date": self.listing_date,
            "latest_date": self.latest_date,
            "avg_amount_20": self.avg_amount_20,
            "data_rows": self.data_rows,
            "is_active": self.is_active,
            "filter_reason": self.filter_reason,
            "theme": self.theme,
            "sector": self.sector,
            "success": self.success,
            "source": self.source,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "rows": self.rows,
            "missing_count": self.missing_count,
            "duplicate_count": self.duplicate_count,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "local_latest_date": self.local_latest_date,
            "target_update_date": self.target_update_date,
        }


def _normalize_etf_item(item: dict[str, Any]) -> dict[str, str]:
    symbol = str(item.get("symbol", "")).zfill(6)
    name = str(item.get("name", "")).strip()
    if not symbol or not name:
        raise ValueError(f"ETF pool item missing symbol or name: {item}")
    category = str(item.get("category", "")).strip()
    asset_class = str(item.get("asset_class", "")).strip()
    theme = str(item.get("theme", "")).strip()
    sector = str(item.get("sector", "")).strip()
    return {
        "symbol": symbol,
        "name": name,
        "exchange": str(item.get("exchange", "")).strip(),
        "category": category,
        "asset_class": asset_class or category,
        "theme": theme or category,
        "sector": sector or category,
        "tracking_index": str(item.get("tracking_index", sector or category)).strip(),
    }


def _dedupe_etfs(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in items:
        symbol = item["symbol"]
        if symbol in seen:
            continue
        seen.add(symbol)
        result.append(item)
    return result


def load_etf_pool(config_path: str | Path | None = None, preset: str | None = None) -> list[dict[str, str]]:
    if config_path is None:
        return universe_records(preset=preset or "a_share_equity", refresh=False)

    path = Path(config_path) if config_path is not None else (DEFAULT_UNIVERSE_PATH if DEFAULT_UNIVERSE_PATH.exists() else LEGACY_POOL_PATH)
    if not path.exists():
        raise FileNotFoundError(f"ETF pool config not found: {path}")

    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, dtype={"symbol": str}).fillna("")
        etfs = [_normalize_etf_item(row) for row in frame.to_dict("records")]
        return _dedupe_etfs(etfs)

    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    etfs = config.get("etfs", [])
    if not isinstance(etfs, list) or not etfs:
        raise ValueError(f"{path} has no etfs list")

    normalized = _dedupe_etfs([_normalize_etf_item(item) for item in etfs])
    selected_preset = preset or str(config.get("default_preset") or "full_universe")
    presets = config.get("presets", {}) or {}
    preset_cfg = presets.get(selected_preset, {})
    preset_symbols = preset_cfg.get("symbols") if isinstance(preset_cfg, dict) else None
    if selected_preset == "full_universe" or preset_symbols == "*":
        return normalized
    if preset_symbols:
        allowed = {str(symbol).zfill(6) for symbol in preset_symbols}
        return [item for item in normalized if item["symbol"] in allowed]
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

    required_non_null = ["date", "open", "high", "low", "close", "volume", "amount"]
    invalid = [col for col in required_non_null if df[col].isna().any()]
    if invalid:
        raise ValueError(f"{symbol} source contains null or invalid required fields: {', '.join(invalid)}")

    df = df.sort_values("date")
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
) -> tuple[pd.DataFrame, str, dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise ImportError("AKShare is not installed; run pip install -r requirements.txt") from exc

    end = end_date or datetime.now().strftime("%Y%m%d")
    errors: list[str] = []
    for attempt in range(1, retries + 1):
        print(f"    ETF download attempt={attempt}/{retries}")
        sources: list[tuple[str, Callable[[], pd.DataFrame]]] = [
            ("akshare.fund_etf_hist_sina", lambda: _download_sina(ak, symbol, start_date, end, 1, retry_delay)),
            ("akshare.fund_etf_hist_em.qfq", lambda: _download_em_chunked(ak, symbol, start_date, end, "qfq", 1, retry_delay)),
            ("akshare.fund_etf_hist_em.none", lambda: _download_em_chunked(ak, symbol, start_date, end, "", 1, retry_delay)),
        ]

        failed_chain: list[str] = []
        for source_name, fetcher in sources:
            try:
                df = fetcher()
                if df.empty:
                    raise ValueError("source returned empty data")
                fallback_chain = [*failed_chain, source_name]
                return df, source_name, {
                    "fallback_used": bool(failed_chain),
                    "fallback_chain": fallback_chain,
                }
            except Exception as exc:  # noqa: BLE001
                msg = f"{source_name}: {exc}"
                errors.append(msg)
                failed_chain.append(source_name)
                print(f"    fallback after {source_name} failed: {exc}")

        if attempt < retries:
            time.sleep(retry_delay * attempt)

    raise RuntimeError(f"all data sources failed after {retries} attempts: {' | '.join(errors)}")


def _status_from_df(symbol: str, name: str, df: pd.DataFrame, source: str, cached: bool, meta: dict[str, str] | None = None) -> DataStatus:
    frame = df.reset_index() if "date" not in df.columns else df.copy()
    if "symbol" not in frame.columns:
        frame["symbol"] = str(symbol).zfill(6)
    if "name" not in frame.columns:
        frame["name"] = name
    if "source" not in frame.columns:
        frame["source"] = source
    quality = analyze_single_etf(symbol, name, frame)
    dates = pd.to_datetime(frame["date"] if "date" in frame.columns else frame.index)
    meta = meta or {}
    amount = pd.to_numeric(frame.get("amount"), errors="coerce") if "amount" in frame.columns else pd.Series(dtype=float)
    avg_amount_20 = float(amount.tail(20).mean()) if len(amount.dropna()) else None
    latest_date = str(dates.max().date()) if len(dates) else ""
    listing_date = str(dates.min().date()) if len(dates) else ""
    return DataStatus(
        symbol=symbol,
        name=name,
        success=True,
        exchange=meta.get("exchange", ""),
        asset_class=meta.get("asset_class", ""),
        category=meta.get("category", ""),
        tracking_index=meta.get("tracking_index", meta.get("sector", "")),
        listing_date=listing_date,
        latest_date=latest_date,
        avg_amount_20=avg_amount_20,
        data_rows=int(len(frame)),
        is_active=bool(avg_amount_20 is not None and avg_amount_20 > 0),
        filter_reason="; ".join(quality.errors),
        theme=meta.get("theme", ""),
        sector=meta.get("sector", ""),
        source=source,
        start_date=listing_date,
        end_date=latest_date,
        rows=int(len(frame)),
        missing_count=quality.missing_count,
        duplicate_count=quality.duplicate_count,
        status=quality.status,
        failure_reason="; ".join(quality.errors),
        cached=cached,
    )


def _failed_status(symbol: str, name: str, reason: str, meta: dict[str, str] | None = None) -> DataStatus:
    meta = meta or {}
    return DataStatus(
        symbol=symbol,
        name=name,
        success=False,
        exchange=meta.get("exchange", ""),
        asset_class=meta.get("asset_class", ""),
        category=meta.get("category", ""),
        tracking_index=meta.get("tracking_index", meta.get("sector", "")),
        theme=meta.get("theme", ""),
        sector=meta.get("sector", ""),
        status="failed",
        failure_reason=reason,
        filter_reason=reason,
        is_active=False,
    )


def _target_complete_date(end_date: str | None = None) -> pd.Timestamp:
    if end_date:
        return pd.Timestamp(end_date).normalize()
    now = pd.Timestamp.now(tz="Asia/Shanghai")
    return latest_trading_day_on_or_before(pd.Timestamp(now).tz_localize(None).normalize())


def _next_calendar_day(date_text: str) -> str:
    return (pd.Timestamp(date_text) + timedelta(days=1)).strftime("%Y%m%d")


def _merge_history(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    old_frame = old.reset_index() if "date" not in old.columns else old.copy()
    merged = pd.concat([old_frame, new], ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce")
    return merged.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def _write_failed_log(statuses: list[DataStatus], path: Path = FAILED_LOG_PATH) -> None:
    failed = [status.to_row() for status in statuses if not status.success]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(failed).to_csv(path, index=False, encoding="utf-8-sig")


def _status_from_coverage_row(row: dict[str, Any], meta: dict[str, str]) -> DataStatus:
    def int_value(value: Any) -> int:
        parsed = pd.to_numeric(value, errors="coerce")
        return 0 if pd.isna(parsed) else int(float(parsed))

    avg_amount = pd.to_numeric(row.get("avg_amount_20", pd.NA), errors="coerce")
    status = DataStatus(
        symbol=str(row.get("symbol", meta["symbol"])).zfill(6),
        name=str(row.get("name", meta["name"])),
        success=str(row.get("success", "True")).lower() not in {"false", "0", "no"},
        exchange=str(row.get("exchange", meta.get("exchange", ""))),
        asset_class=str(row.get("asset_class", meta.get("asset_class", ""))),
        category=str(row.get("category", meta.get("category", ""))),
        tracking_index=str(row.get("tracking_index", meta.get("tracking_index", ""))),
        listing_date=str(row.get("listing_date", row.get("start_date", ""))),
        latest_date=str(row.get("latest_date", row.get("end_date", ""))),
        avg_amount_20=None if pd.isna(avg_amount) else float(avg_amount),
        data_rows=int_value(row.get("data_rows", row.get("rows", 0))),
        is_active=str(row.get("is_active", "False")).lower() in {"true", "1", "yes"},
        filter_reason=str(row.get("filter_reason", "")),
        theme=str(row.get("theme", meta.get("theme", ""))),
        sector=str(row.get("sector", meta.get("sector", ""))),
        source=str(row.get("source", "local_cache") or "local_cache"),
        start_date=str(row.get("start_date", row.get("listing_date", ""))),
        end_date=str(row.get("end_date", row.get("latest_date", ""))),
        rows=int_value(row.get("rows", row.get("data_rows", 0))),
        missing_count=int_value(row.get("missing_count", 0)),
        duplicate_count=int_value(row.get("duplicate_count", 0)),
        status="skipped",
        failure_reason=str(row.get("failure_reason", "")),
        cached=True,
        local_latest_date=str(row.get("latest_date", row.get("end_date", ""))),
        target_update_date="",
    )
    return status


def update_one_etf(
    etf: dict[str, str],
    start_date: str,
    end_date: str | None,
    mode: str = "incremental",
) -> DataStatus:
    symbol = etf["symbol"]
    name = etf["name"]
    path = get_csv_path(symbol)
    target_date = _target_complete_date(end_date)
    local_df: pd.DataFrame | None = None
    local_latest = ""

    if path.exists():
        local_df = load_etf_data(symbol, name=name)
        if not local_df.empty:
            local_latest = str(pd.Timestamp(local_df.index.max()).date())

    if mode in {"incremental", "refresh"} and local_df is not None and local_latest:
        if pd.Timestamp(local_latest) >= target_date:
            status = _status_from_df(symbol, name, local_df.reset_index(), "local_cache", cached=True, meta=etf)
            status.status = "skipped"
            status.local_latest_date = local_latest
            status.target_update_date = str(target_date.date())
            return status
        fetch_start = _next_calendar_day(local_latest)
    else:
        fetch_start = start_date

    with AKSHARE_DOWNLOAD_LOCK:
        df, source, download_meta = download_etf_history(symbol=symbol, start_date=fetch_start, end_date=target_date.strftime("%Y%m%d"), retries=2)
    if mode in {"incremental", "refresh"} and local_df is not None:
        df = _merge_history(local_df, df)
    saved = save_etf_data(symbol, df, name=name, source=source)
    metadata = build_cache_metadata(symbol, df, name=name, source=source, cache_file=saved, **download_meta)
    write_cache_metadata(symbol, metadata)
    status = _status_from_df(symbol, name, df, source, cached=False, meta=etf)
    status.failure_reason = "" if saved else status.failure_reason
    status.local_latest_date = local_latest
    status.target_update_date = str(target_date.date())
    return status


def update_many_etfs(
    etf_pool: list[dict[str, str]],
    start_date: str,
    end_date: str | None = None,
    mode: str = "incremental",
    symbols: set[str] | None = None,
    max_workers: int = 6,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> list[DataStatus]:
    targets = etf_pool
    if symbols:
        targets = [etf for etf in etf_pool if etf["symbol"] in symbols]

    total = len(targets)
    statuses: list[DataStatus] = []
    counts = {"success": 0, "skipped": 0, "failed": 0}
    started = time.perf_counter()
    target_date = str(_target_complete_date(end_date).date())
    coverage_by_symbol: dict[str, dict[str, Any]] = {}
    if mode in {"incremental", "refresh"} and COVERAGE_REPORT_PATH.exists():
        try:
            coverage = pd.read_csv(COVERAGE_REPORT_PATH, dtype={"symbol": str}).fillna("")
            if "symbol" in coverage.columns:
                coverage["symbol"] = coverage["symbol"].astype(str).str.zfill(6)
                coverage_by_symbol = {str(row["symbol"]).zfill(6): row for row in coverage.to_dict("records")}
        except Exception:
            coverage_by_symbol = {}
    previous_failed = _load_previous_failures() if mode == "incremental" else set()
    download_targets: list[dict[str, str]] = []
    for etf in targets:
        symbol = etf["symbol"]
        cached_row = coverage_by_symbol.get(symbol)
        cached_latest = str(cached_row.get("latest_date", cached_row.get("end_date", ""))) if cached_row else ""
        cached_is_current = False
        if cached_row and cached_latest:
            try:
                cached_is_current = pd.Timestamp(cached_latest) >= pd.Timestamp(target_date)
            except Exception:
                cached_is_current = False
        if cached_is_current and get_csv_path(symbol).exists():
            status = _status_from_coverage_row(cached_row, etf)
            status.target_update_date = target_date
            statuses.append(status)
            counts["skipped"] += 1
        elif symbol in previous_failed:
            status = _failed_status(etf["symbol"], etf["name"], "previous failure skipped in incremental mode; use --mode refresh --symbols or --mode rebuild", etf)
            statuses.append(status)
            counts["failed"] += 1
        else:
            download_targets.append(etf)

    def emit(current: int, etf: dict[str, str], status: DataStatus) -> None:
        elapsed = time.perf_counter() - started
        rate = current / elapsed if elapsed > 0 else 0.0
        eta = (total - current) / rate if rate > 0 else 0.0
        if progress_callback:
            progress_callback(
                {
                    "stage": "下载 / 更新行情" if mode in {"incremental", "refresh"} else "全量重建数据",
                    "current": current,
                    "total": total,
                    "symbol": etf["symbol"],
                    "name": etf["name"],
                    "local_latest_date": status.local_latest_date or status.latest_date,
                    "target_date": target_date,
                    "status": status.status,
                    "success_count": counts["success"],
                    "skipped_count": counts["skipped"],
                    "failed_count": counts["failed"],
                    "elapsed_seconds": elapsed,
                    "eta_seconds": eta,
                }
            )

    current_offset = len(statuses)
    for current, status in enumerate(statuses, start=1):
        emit(current, {"symbol": status.symbol, "name": status.name}, status)

    if download_targets:
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            futures = {executor.submit(update_one_etf, etf, start_date, end_date, mode): etf for etf in download_targets}
            completed = as_completed(futures)
            try:
                from tqdm import tqdm

                completed = tqdm(completed, total=len(futures), desc="update ETF data", unit="etf")
            except Exception:
                pass
            for current, future in enumerate(completed, start=current_offset + 1):
                etf = futures[future]
                try:
                    status = future.result()
                except Exception as exc:  # noqa: BLE001
                    status = _failed_status(etf["symbol"], etf["name"], str(exc), etf)
                statuses.append(status)
                if not status.success:
                    counts["failed"] += 1
                elif status.status == "skipped" or status.cached:
                    counts["skipped"] += 1
                else:
                    counts["success"] += 1
                emit(current, etf, status)

    status_by_symbol = {status.symbol: status for status in statuses}
    for etf in etf_pool:
        symbol = etf["symbol"]
        if symbol in status_by_symbol:
            continue
        try:
            df = load_etf_data(symbol, name=etf["name"]).reset_index()
            status_by_symbol[symbol] = _status_from_df(symbol, etf["name"], df, "local_cache", cached=True, meta=etf)
        except Exception as exc:  # noqa: BLE001
            status_by_symbol[symbol] = _failed_status(symbol, etf["name"], str(exc), etf)

    ordered = [status_by_symbol[etf["symbol"]] for etf in etf_pool]
    write_coverage_report(ordered)
    _write_failed_log(ordered)
    return ordered


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
    coverage = pd.DataFrame([status.to_row() for status in statuses])
    coverage.to_csv(path, index=False, encoding="utf-8-sig")
    try:
        raw = load_market_etf_universe(refresh=False)
        write_universe_snapshot(raw, coverage)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: failed to write ETF universe snapshot: {exc}")
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
            statuses.append(_status_from_df(symbol, name, df, "local_cache", cached=True, meta=etf))
        except Exception as exc:  # noqa: BLE001
            error = previous_errors.get(symbol) or str(exc)
            statuses.append(_failed_status(symbol, name, error, etf))

    write_coverage_report(statuses, path)
    return statuses


def update_all_data(
    etf_pool: list[dict[str, str]],
    start_date: str,
    end_date: str | None = None,
    refresh: bool = False,
    retry_failed_only: bool = False,
    mode: str | None = None,
    symbols: set[str] | None = None,
    max_workers: int = 6,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[str], list[str], list[DataStatus]]:
    if mode in {"incremental", "refresh", "rebuild"} and not retry_failed_only:
        statuses = update_many_etfs(
            etf_pool=etf_pool,
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            symbols=symbols,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
        successes = [
            f"{item.symbol} {item.name}: {item.status} rows={item.rows} range={item.start_date}->{item.end_date}"
            for item in statuses
            if item.success
        ]
        errors = [f"{item.symbol} {item.name}: {item.failure_reason}" for item in statuses if not item.success]
        return successes, errors, statuses

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
                status = _status_from_df(symbol, name, df, "local_cache", cached=True, meta=etf)
                statuses.append(status)
                msg = f"{symbol} {name}: cache rows={status.rows} range={status.start_date}->{status.end_date} status={status.status}"
                successes.append(msg)
                print(f"    OK source=local_cache rows={status.rows}")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"    cache unusable; refreshing. reason={exc}")

        try:
            df, source, download_meta = download_etf_history(symbol=symbol, start_date=start_date, end_date=end_date)
            path = save_etf_data(symbol, df, name=name, source=source)
            metadata = build_cache_metadata(symbol, df, name=name, source=source, cache_file=path, **download_meta)
            write_cache_metadata(symbol, metadata)
            status = _status_from_df(symbol, name, df, source, cached=False, meta=etf)
            statuses.append(status)
            msg = f"{symbol} {name}: downloaded source={source} rows={status.rows} range={status.start_date}->{status.end_date} path={path}"
            successes.append(msg)
            print(f"    OK source={source} rows={status.rows}")
        except Exception as exc:  # noqa: BLE001
            reason = str(exc)
            statuses.append(_failed_status(symbol, name, reason, etf))
            errors.append(f"{symbol} {name}: {reason}")
            print(f"    ERR {symbol} {name}: {reason}")
        time.sleep(0.05)

    status_by_symbol = {status.symbol: status for status in statuses}
    for etf in etf_pool:
        symbol = etf["symbol"]
        if symbol in status_by_symbol:
            continue
        try:
            df = load_etf_data(symbol, name=etf["name"]).reset_index()
            save_etf_data(symbol, df, name=etf["name"], source="local_cache")
            status_by_symbol[symbol] = _status_from_df(symbol, etf["name"], df, "local_cache", cached=True, meta=etf)
        except Exception as exc:  # noqa: BLE001
            status_by_symbol[symbol] = _failed_status(symbol, etf["name"], str(exc), etf)

    ordered_statuses = [status_by_symbol[etf["symbol"]] for etf in etf_pool]
    write_coverage_report(ordered_statuses)
    return successes, errors, ordered_statuses
