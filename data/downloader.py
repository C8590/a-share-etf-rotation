from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import threading
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from data.quality import analyze_single_etf
from data.storage import (
    cache_metadata_is_fresh,
    get_csv_path,
    load_etf_data,
    metadata_records,
    normalize_symbol,
    save_etf_data,
    scan_cache_metadata,
    update_cache_metadata,
)
from data.trading_calendar import get_current_trading_day, get_market_phase, load_a_share_trading_calendar
from data.universe import load_market_etf_universe, universe_records, write_universe_snapshot


COVERAGE_REPORT_PATH = Path("output") / "data_coverage_report.csv"
UPDATE_FAILURE_REPORT_PATH = Path("output") / "update_failures.csv"
FAILED_LOG_PATH = Path("logs") / "update_failed.csv"
AKSHARE_DOWNLOAD_LOCK = threading.Lock()
SOURCE_RATE_LOCK = threading.Lock()
SOURCE_LAST_CALL: dict[str, float] = {}
SOURCE_MIN_INTERVAL_SECONDS = 0.12
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


class SourceCircuitBreaker:
    def __init__(self, threshold: int = 20, cooldown_seconds: int = 300) -> None:
        self.threshold = int(threshold)
        self.cooldown_seconds = int(cooldown_seconds)
        self._failures: dict[str, int] = {}
        self._disabled_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, source: str) -> bool:
        with self._lock:
            until = self._disabled_until.get(source, 0.0)
            if until and until > time.time():
                return False
            if until:
                self._disabled_until.pop(source, None)
                self._failures[source] = 0
            return True

    def record_success(self, source: str) -> None:
        with self._lock:
            self._failures[source] = 0
            self._disabled_until.pop(source, None)

    def record_failure(self, source: str) -> None:
        with self._lock:
            failures = self._failures.get(source, 0) + 1
            self._failures[source] = failures
            if failures >= self.threshold:
                self._disabled_until[source] = time.time() + self.cooldown_seconds


SOURCE_CIRCUIT_BREAKER = SourceCircuitBreaker()


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
    elapsed_seconds: float = 0.0

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
            "error": self.error,
            "local_latest_date": self.local_latest_date,
            "target_update_date": self.target_update_date,
            "elapsed_seconds": round(float(self.elapsed_seconds or 0.0), 3),
        }


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def _normalize_etf_item(item: dict[str, Any]) -> dict[str, str]:
    symbol = normalize_symbol(item.get("symbol", ""))
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
        "sector_l1": str(item.get("sector_l1", "")).strip(),
        "sector_l2": str(item.get("sector_l2", "")).strip(),
        "is_defensive": item.get("is_defensive", ""),
        "is_broad_market": item.get("is_broad_market", ""),
        "tracking_index": str(item.get("tracking_index", sector or category)).strip(),
        "listing_date": str(item.get("listing_date", "")).strip(),
        "latest_date": str(item.get("latest_date", "")).strip(),
        "avg_amount_20": str(item.get("avg_amount_20", "")).strip(),
        "data_rows": str(item.get("data_rows", "")).strip(),
        "is_active": str(item.get("is_active", "")).strip(),
        "filter_reason": str(item.get("filter_reason", "")).strip(),
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
        allowed = {normalize_symbol(symbol) for symbol in preset_symbols}
        return [item for item in normalized if item["symbol"] in allowed]
    return normalized


def _pick_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    normalized = {str(col).strip(): col for col in df.columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def normalize_source_frame(symbol: str, raw: pd.DataFrame) -> pd.DataFrame:
    symbol = normalize_symbol(symbol) or str(symbol)
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
    symbol = normalize_symbol(symbol)
    return f"sh{symbol}" if symbol.startswith(("5", "6")) else f"sz{symbol}"


def _exchange_suffix_symbol(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    suffix = "SH" if symbol.startswith(("5", "6")) else "SZ"
    return f"{symbol}.{suffix}"


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


def _rate_limit_source(label: str) -> None:
    source = label.split("(", 1)[0]
    with SOURCE_RATE_LOCK:
        now = time.perf_counter()
        previous = SOURCE_LAST_CALL.get(source, 0.0)
        wait = SOURCE_MIN_INTERVAL_SECONDS - (now - previous)
        if wait > 0:
            time.sleep(wait)
        SOURCE_LAST_CALL[source] = time.perf_counter()


def _call_with_timeout(label: str, fetcher: Callable[[], pd.DataFrame], timeout_seconds: float | None) -> pd.DataFrame:
    _rate_limit_source(label)
    if not timeout_seconds or timeout_seconds <= 0:
        return fetcher()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fetcher)
    try:
        return future.result(timeout=float(timeout_seconds))
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"{label} timed out after {timeout_seconds:.0f}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _retry_call(
    label: str,
    fetcher: Callable[[], pd.DataFrame],
    retries: int,
    retry_delay: float,
    timeout_seconds: float | None = None,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            print(f"    source={label} attempt={attempt}/{retries}")
            return _call_with_timeout(label, fetcher, timeout_seconds)
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
    timeout_seconds: float | None = None,
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
            timeout_seconds=timeout_seconds,
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
    timeout_seconds: float | None = None,
) -> pd.DataFrame:
    raw = _retry_call(
        "fund_etf_hist_sina",
        lambda: ak.fund_etf_hist_sina(symbol=_sina_symbol(symbol)),
        retries=retries,
        retry_delay=retry_delay,
        timeout_seconds=timeout_seconds,
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
    timeout_per_source: float | None = None,
    max_sources: int | None = None,
    circuit_breaker: SourceCircuitBreaker | None = None,
) -> tuple[pd.DataFrame, str]:
    symbol = normalize_symbol(symbol)
    if not symbol:
        raise ValueError("ETF symbol is empty")
    try:
        import akshare as ak
    except ImportError as exc:
        raise ImportError("AKShare is not installed; run pip install -r requirements.txt") from exc

    end = end_date or datetime.now().strftime("%Y%m%d")
    errors: list[str] = []
    for attempt in range(1, retries + 1):
        print(f"    ETF download attempt={attempt}/{retries}")
        sources: list[tuple[str, Callable[[], pd.DataFrame]]] = [
            ("akshare.fund_etf_hist_sina", lambda: _download_sina(ak, symbol, start_date, end, 1, retry_delay, timeout_per_source)),
            ("akshare.fund_etf_hist_em.qfq", lambda: _download_em_chunked(ak, symbol, start_date, end, "qfq", 1, retry_delay, timeout_per_source)),
            ("akshare.fund_etf_hist_em.none", lambda: _download_em_chunked(ak, symbol, start_date, end, "", 1, retry_delay, timeout_per_source)),
        ]
        if max_sources is not None and max_sources > 0:
            sources = sources[: int(max_sources)]

        for source_name, fetcher in sources:
            if circuit_breaker is not None and not circuit_breaker.allow(source_name):
                errors.append(f"{source_name}: circuit breaker open")
                continue
            try:
                df = fetcher()
                if df.empty:
                    raise ValueError("source returned empty data")
                if circuit_breaker is not None:
                    circuit_breaker.record_success(source_name)
                return df, source_name
            except Exception as exc:  # noqa: BLE001
                msg = f"{source_name}: {exc}"
                errors.append(msg)
                if circuit_breaker is not None:
                    circuit_breaker.record_failure(source_name)
                print(f"    fallback after {source_name} failed: {exc}")

        if attempt < retries:
            time.sleep(retry_delay * attempt)

    raise RuntimeError(f"all data sources failed after {retries} attempts: {' | '.join(errors)}")


def _status_from_df(symbol: str, name: str, df: pd.DataFrame, source: str, cached: bool, meta: dict[str, str] | None = None) -> DataStatus:
    symbol = normalize_symbol(symbol)
    frame = df.reset_index() if "date" not in df.columns else df.copy()
    if "date" not in frame.columns:
        raise ValueError(f"{symbol} CSV 字段缺失: date")
    quality = analyze_single_etf(symbol, name, frame)
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.dropna().empty:
        raise ValueError(f"{symbol} 日期解析失败: date 列没有有效日期")
    meta = meta or {}
    amount = pd.to_numeric(frame.get("amount"), errors="coerce") if "amount" in frame.columns else pd.Series(dtype=float)
    avg_amount_20 = float(amount.tail(20).mean()) if len(amount.dropna()) else None
    latest_date = str(dates.max().date())
    listing_date = str(dates.min().date())
    quality_notes = "; ".join([*quality.errors, *quality.warnings])
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
        theme=meta.get("theme", ""),
        sector=meta.get("sector", ""),
        source=source,
        start_date=listing_date,
        end_date=latest_date,
        rows=int(len(frame)),
        missing_count=quality.missing_count,
        duplicate_count=quality.duplicate_count,
        status="cached_success" if cached else "success",
        failure_reason="",
        filter_reason=quality_notes,
        cached=cached,
        local_latest_date=latest_date,
    )


def _failed_status(symbol: str, name: str, reason: str, meta: dict[str, str] | None = None) -> DataStatus:
    symbol = normalize_symbol(symbol) or str(symbol)
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


def _skipped_status(symbol: str, name: str, reason: str, meta: dict[str, str] | None = None) -> DataStatus:
    status = _failed_status(symbol, name, reason, meta)
    status.success = True
    status.status = "skipped"
    status.source = "local_metadata"
    return status


def _looks_inactive_or_unsupported(meta: dict[str, str], error: str) -> bool:
    active_text = str(meta.get("is_active", "")).strip().lower()
    if active_text in {"false", "0", "no", "否"}:
        return True
    reason = f"{meta.get('filter_reason', '')} {error}".lower()
    skip_markers = ["退市", "delist", "not found", "不存在", "unsupported", "不支持", "no such symbol"]
    return any(marker in reason for marker in skip_markers)


def _target_complete_date(end_date: str | None = None) -> pd.Timestamp:
    if end_date:
        return pd.Timestamp(end_date).normalize()
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    calendar = load_a_share_trading_calendar(
        start=(pd.Timestamp(now.date()) - pd.Timedelta(days=30)).strftime("%Y%m%d"),
        end=(pd.Timestamp(now.date()) + pd.Timedelta(days=10)).strftime("%Y%m%d"),
    )
    today = pd.Timestamp(now.date()).normalize()
    if today not in calendar:
        previous = calendar[calendar < today]
        if previous.empty:
            return pd.Timestamp(get_current_trading_day(now, calendar)).normalize()
        return pd.Timestamp(previous[-1]).normalize()
    if now.time() < datetime.strptime("15:30", "%H:%M").time():
        previous = calendar[calendar < today]
        if not previous.empty:
            return pd.Timestamp(previous[-1]).normalize()
    return today


def _next_calendar_day(date_text: str) -> str:
    return (pd.Timestamp(date_text) + timedelta(days=1)).strftime("%Y%m%d")


def _merge_history(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    old_frame = old.reset_index() if "date" not in old.columns else old.copy()
    merged = pd.concat([old_frame, new], ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce")
    return merged.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def append_today_bar_from_quote(etf_code: str, daily_df: pd.DataFrame, quote: dict[str, Any]) -> pd.DataFrame:
    code = str(etf_code).zfill(6)
    if str(quote.get("code", "")).zfill(6) != code:
        raise ValueError(f"{code} quote code mismatch")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    calendar = load_a_share_trading_calendar(
        start=(pd.Timestamp(now.date()) - pd.Timedelta(days=30)).strftime("%Y%m%d"),
        end=(pd.Timestamp(now.date()) + pd.Timedelta(days=10)).strftime("%Y%m%d"),
    )
    if get_market_phase(now, calendar) != "已收盘":
        raise ValueError(f"{code} realtime close patch requires market closed")
    today = get_current_trading_day(now, calendar)
    quote_date = pd.Timestamp(quote.get("quote_date")).date()
    if quote_date != today:
        raise ValueError(f"{code} quote_date {quote_date} is not current trading day {today}")

    open_price = float(quote.get("open") or 0)
    high = float(quote.get("high") or 0)
    low = float(quote.get("low") or 0)
    close = float(quote.get("close") or quote.get("latest_price") or 0)
    latest = float(quote.get("latest_price") or close or 0)
    prev_close = float(quote.get("prev_close") or 0)
    volume = float(quote.get("volume") or 0)
    amount = float(quote.get("amount") or 0)
    if min(open_price, high, low, close, latest, prev_close) <= 0:
        raise ValueError(f"{code} quote OHLC/latest/prev_close must be positive")
    if volume <= 0 or amount <= 0:
        raise ValueError(f"{code} quote volume/amount must be positive")
    if not (low <= close <= high and high >= open_price >= low):
        raise ValueError(f"{code} quote OHLC relationship is invalid")
    if abs(latest - close) > 1e-8:
        raise ValueError(f"{code} latest_price and close are inconsistent")

    frame = daily_df.reset_index() if "date" not in daily_df.columns else daily_df.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    source = f"{quote.get('source') or 'quote'}_realtime_close_patch"
    row = {
        "date": pd.Timestamp(today),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "symbol": code,
        "name": str(quote.get("name") or ""),
        "source": source,
    }
    patched = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
    return patched.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def refresh_daily_data_after_close(etf_pool: list[dict[str, str]], today: str | datetime | pd.Timestamp | None = None) -> list[str]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    calendar = load_a_share_trading_calendar(
        start=(pd.Timestamp(now.date()) - pd.Timedelta(days=30)).strftime("%Y%m%d"),
        end=(pd.Timestamp(now.date()) + pd.Timedelta(days=10)).strftime("%Y%m%d"),
    )
    if get_market_phase(now, calendar) != "已收盘":
        return []
    trade_day = pd.Timestamp(today).date() if today is not None else get_current_trading_day(now, calendar)
    targets: list[dict[str, str]] = []
    for etf in etf_pool:
        try:
            local = load_etf_data(etf["symbol"], name=etf.get("name", ""))
            if not local.empty and pd.Timestamp(local.index.max()).date() >= trade_day:
                continue
            targets.append(etf)
        except Exception:
            targets.append(etf)
    if not targets:
        return []
    from data.quotes import get_etf_quotes

    quotes = get_etf_quotes({item["symbol"] for item in targets})
    patched: list[str] = []
    for etf in targets:
        symbol = etf["symbol"]
        try:
            local = load_etf_data(symbol, name=etf.get("name", ""))
            quote = quotes.get(symbol) or {}
            updated = append_today_bar_from_quote(symbol, local, quote)
            save_etf_data(symbol, updated, name=etf.get("name", ""), source=str(quote.get("source") or "quote"))
            patched.append(symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: failed to patch {symbol} daily bar from realtime close quote: {exc}")
    return patched


def _write_failed_log(statuses: list[DataStatus], path: Path = FAILED_LOG_PATH) -> None:
    rows = [status.to_row() for status in statuses if status.status in {"failed", "cached_success", "skipped"} and status.error]
    columns = list(DataStatus(symbol="", name="", success=False).to_row().keys())
    for output_path in {path, UPDATE_FAILURE_REPORT_PATH}:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=columns).to_csv(output_path, index=False, encoding="utf-8-sig")


def _status_from_coverage_row(row: dict[str, Any], meta: dict[str, str]) -> DataStatus:
    def int_value(value: Any) -> int:
        parsed = pd.to_numeric(value, errors="coerce")
        return 0 if pd.isna(parsed) else int(float(parsed))

    avg_amount = pd.to_numeric(row.get("avg_amount_20", pd.NA), errors="coerce")
    status = DataStatus(
        symbol=normalize_symbol(row.get("symbol", meta["symbol"])),
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
        status=str(row.get("status", "cached_success") or "cached_success"),
        failure_reason=str(row.get("failure_reason", "")),
        cached=True,
        local_latest_date=str(row.get("latest_date", row.get("end_date", ""))),
        target_update_date="",
    )
    return status


def _status_from_metadata_row(row: dict[str, Any], meta: dict[str, str], status_text: str = "up_to_date") -> DataStatus:
    symbol = normalize_symbol(row.get("symbol", meta.get("symbol", "")))
    latest_date = str(row.get("latest_date", "") or "")
    start_date = str(row.get("start_date", "") or "")
    parsed_rows = pd.to_numeric(row.get("rows", 0), errors="coerce")
    rows = 0 if pd.isna(parsed_rows) else int(float(parsed_rows))
    status = DataStatus(
        symbol=symbol,
        name=str(row.get("name") or meta.get("name", "")),
        success=True,
        exchange=meta.get("exchange", ""),
        asset_class=meta.get("asset_class", ""),
        category=meta.get("category", ""),
        tracking_index=meta.get("tracking_index", meta.get("sector", "")),
        listing_date=start_date,
        latest_date=latest_date,
        data_rows=rows,
        is_active=True,
        theme=meta.get("theme", ""),
        sector=meta.get("sector", ""),
        source=str(row.get("source") or "local_metadata"),
        start_date=start_date,
        end_date=latest_date,
        rows=rows,
        status=status_text,
        cached=True,
        local_latest_date=latest_date,
    )
    return status


def _metadata_for_symbol(symbol: str, name: str, all_metadata: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    symbol = normalize_symbol(symbol)
    row = (all_metadata or {}).get(symbol)
    if row and cache_metadata_is_fresh(row):
        return row
    return scan_cache_metadata(symbol, name=name)


def _cached_failure_attempted_today(row: dict[str, Any]) -> bool:
    if str(row.get("status") or "") != "cached_success" or not str(row.get("last_error") or "").strip():
        return False
    try:
        attempted = pd.Timestamp(row.get("last_update_time")).date()
    except Exception:
        return False
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return attempted == today


def _lookback_start_date(local_latest: str, target_date: pd.Timestamp, max_lookback_days: int) -> str:
    target = pd.Timestamp(target_date).normalize()
    local = pd.Timestamp(local_latest).normalize()
    calendar = load_a_share_trading_calendar(
        start=(min(local, target) - pd.Timedelta(days=30)).strftime("%Y%m%d"),
        end=(target + pd.Timedelta(days=5)).strftime("%Y%m%d"),
    )
    del max_lookback_days
    after_local = calendar[(calendar > local) & (calendar <= target)]
    if len(after_local) > 0:
        start = pd.Timestamp(after_local[0]).normalize()
    else:
        start = local + pd.Timedelta(days=1)
    return start.strftime("%Y%m%d")


def update_one_etf(
    etf: dict[str, str],
    start_date: str,
    end_date: str | None,
    mode: str = "incremental",
    debug: bool = False,
    max_lookback_days: int = 5,
    retries: int | None = None,
    timeout_per_symbol: float = 20,
    metadata: dict[str, dict[str, Any]] | None = None,
    circuit_breaker: SourceCircuitBreaker | None = None,
) -> DataStatus:
    started = time.perf_counter()
    symbol = normalize_symbol(etf["symbol"])
    name = etf["name"]
    path = get_csv_path(symbol)
    target_date = _target_complete_date(end_date)
    local_df: pd.DataFrame | None = None
    local_latest = ""
    cache_error = ""
    meta_row = _metadata_for_symbol(symbol, name, metadata)

    if meta_row:
        local_latest = str(meta_row.get("latest_date") or "")

    if mode in {"incremental", "refresh"} and local_latest:
        try:
            local_latest_ts = pd.Timestamp(local_latest).normalize()
        except Exception:
            print(f"缓存日期异常: {symbol} {name} latest_date={local_latest}，将重新检查本地 CSV")
            cache_error = f"缓存日期异常: latest_date={local_latest}"
            local_latest = ""
            fetch_start = start_date
        else:
            if local_latest_ts >= target_date:
                print(f"使用缓存，无需更新: {symbol} {name} 本地最新={local_latest} 目标={target_date.date()}")
                status = _status_from_metadata_row(meta_row or {}, etf, status_text="up_to_date")
                status.local_latest_date = local_latest
                status.target_update_date = str(target_date.date())
                status.elapsed_seconds = time.perf_counter() - started
                return status
            fetch_start = _lookback_start_date(local_latest, target_date, max_lookback_days)
            print(f"从 {local_latest} 增量更新: {symbol} {name} 下载区间={fetch_start}->{target_date.strftime('%Y%m%d')}")
    else:
        fetch_start = start_date

    try:
        if path.exists():
            try:
                local_df = load_etf_data(symbol, name=name)
                if not local_df.empty and not local_latest:
                    local_latest = str(pd.Timestamp(local_df.index.max()).date())
            except Exception as exc:  # noqa: BLE001
                cache_error = f"本地缓存读取失败: {exc}"
        cold_start = local_df is None or not local_latest
        effective_retries = retries if retries is not None else (1 if mode in {"incremental", "refresh"} else 3)
        max_sources = 2 if mode in {"incremental", "refresh"} else None
        source_timeout = max(1.0, float(timeout_per_symbol) / max(1, max_sources or 3))
        df, source = download_etf_history(
            symbol=symbol,
            start_date=fetch_start,
            end_date=target_date.strftime("%Y%m%d"),
            retries=effective_retries,
            timeout_per_source=source_timeout,
            max_sources=max_sources,
            circuit_breaker=circuit_breaker,
        )
        if mode in {"incremental", "refresh", "repair_missing"} and local_df is not None:
            df = _merge_history(local_df, df)
        save_etf_data(symbol, df, name=name, source=source)
        print(f"缓存写入成功: {symbol} {name} source={source} rows={len(df)}")
        status = _status_from_df(symbol, name, df, source, cached=False, meta=etf)
        if cold_start:
            status.status = "cold_start"
    except Exception as exc:  # noqa: BLE001
        if debug:
            import traceback

            print(traceback.format_exc())
        reason = str(exc)
        if cache_error:
            reason = f"{cache_error}; 下载失败: {reason}"
        if local_df is None and path.exists():
            try:
                local_df = load_etf_data(symbol, name=name)
                if not local_df.empty:
                    local_latest = str(pd.Timestamp(local_df.index.max()).date())
            except Exception:
                local_df = None
        if local_df is not None and local_latest:
            status = _status_from_df(symbol, name, local_df.reset_index(), "local_cache_after_update_failure", cached=True, meta=etf)
            status.status = "cached_success"
            status.failure_reason = f"联网更新失败，已保留本地缓存: {reason}"
            update_cache_metadata(symbol, local_df.reset_index(), name=name, source="local_cache_after_update_failure", status="cached_success", last_error=status.failure_reason)
        elif _looks_inactive_or_unsupported(etf, reason):
            status = _skipped_status(symbol, name, reason, etf)
            print(f"ETF 单独失败，已跳过: {symbol} {name} reason={reason}")
        else:
            status = _failed_status(symbol, name, reason, etf)
            print(f"ETF 单独失败，已跳过: {symbol} {name} reason={reason}")
    status.local_latest_date = status.latest_date if status.status in {"success", "cold_start"} else local_latest
    status.target_update_date = str(target_date.date())
    if not status.latest_date and local_latest:
        status.latest_date = local_latest
        status.end_date = local_latest
    status.elapsed_seconds = time.perf_counter() - started
    return status


def update_many_etfs(
    etf_pool: list[dict[str, str]],
    start_date: str,
    end_date: str | None = None,
    mode: str = "incremental",
    symbols: set[str] | None = None,
    max_count: int | None = None,
    max_workers: int = 6,
    debug: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    max_lookback_days: int = 5,
    retries: int | None = None,
    timeout_per_symbol: float = 20,
    circuit_breaker_threshold: int = 20,
    cold_start_limit: int | None = None,
) -> list[DataStatus]:
    targets = etf_pool
    if symbols:
        normalized_symbols = {normalize_symbol(symbol) for symbol in symbols}
        targets = [etf for etf in etf_pool if normalize_symbol(etf["symbol"]) in normalized_symbols]
    if max_count is not None and max_count > 0:
        targets = targets[: int(max_count)]
    target_symbols = {normalize_symbol(etf["symbol"]) for etf in targets}
    limited_scope = bool(symbols or max_count)

    total = len(targets)
    statuses: list[DataStatus] = []
    counts = {"success": 0, "cached_success": 0, "skipped": 0, "up_to_date": 0, "cold_start": 0, "failed": 0}
    started = time.perf_counter()
    target_date = str(_target_complete_date(end_date).date())
    metadata_by_symbol = metadata_records()
    breaker = SourceCircuitBreaker(threshold=circuit_breaker_threshold, cooldown_seconds=300)
    download_targets: list[dict[str, str]] = []
    cold_start_seen = 0
    latest_seen = ""
    for scan_current, etf in enumerate(targets, start=1):
        symbol = normalize_symbol(etf["symbol"])
        cached_row = _metadata_for_symbol(symbol, etf["name"], metadata_by_symbol)
        cached_latest = str(cached_row.get("latest_date", "")) if cached_row else ""
        if cached_latest:
            latest_seen = max(latest_seen, cached_latest)
        cached_is_current = False
        if cached_row and cached_latest:
            try:
                cached_is_current = pd.Timestamp(cached_latest) >= pd.Timestamp(target_date)
            except Exception:
                cached_is_current = False
                print(f"缓存日期异常: {symbol} {etf['name']} latest_date={cached_latest}")
        if mode in {"incremental", "refresh", "repair_missing"} and cached_is_current and get_csv_path(symbol).exists():
            status = _status_from_metadata_row(cached_row, etf, status_text="up_to_date")
            status.target_update_date = target_date
            statuses.append(status)
            counts["up_to_date"] += 1
            status_text = "使用缓存，无需更新"
        elif mode in {"incremental", "refresh"} and cached_row and _cached_failure_attempted_today(cached_row):
            status = _status_from_metadata_row(cached_row, etf, status_text="cached_success")
            status.failure_reason = str(cached_row.get("last_error") or "今日已尝试增量更新失败，保留本地缓存")
            status.target_update_date = target_date
            statuses.append(status)
            counts["cached_success"] += 1
            status_text = "ETF 单独失败，已跳过：今日已尝试增量更新失败，保留缓存"
        elif mode in {"incremental", "refresh"} and not cached_row and cold_start_limit is not None and not limited_scope and cold_start_seen >= cold_start_limit:
            status = _skipped_status(etf["symbol"], etf["name"], "cold_start_limit reached in daily incremental mode; use repair-missing or full-refresh", etf)
            status.status = "cold_start_deferred"
            statuses.append(status)
            counts["skipped"] += 1
            status_text = "无缓存，已延后；请使用修复缺失或全量重建"
        else:
            if not cached_row:
                cold_start_seen += 1
            download_targets.append(etf)
            status_text = f"从 {cached_latest or start_date} 增量更新" if cached_latest else "无本地缓存，准备下载"
        if progress_callback:
            progress_callback(
                {
                    "stage": "扫描本地缓存",
                    "mode": "日常增量" if mode in {"incremental", "refresh"} else ("修复缺失" if mode == "repair_missing" else "全量重建"),
                    "current": scan_current,
                    "total": total,
                    "symbol": symbol,
                    "name": etf["name"],
                    "local_latest_date": cached_latest or "无缓存",
                    "latest_data_date": latest_seen,
                    "target_date": target_date,
                    "status": status_text,
                    "need_update_count": len(download_targets),
                    "up_to_date_count": counts["up_to_date"],
                    "skipped_count": counts["skipped"],
                    "failed_count": counts["failed"],
                    "cached_success_count": counts["cached_success"],
                    "success_count": counts["success"],
                    "cold_start_count": counts["cold_start"],
                    "elapsed_seconds": time.perf_counter() - started,
                    "eta_seconds": 0,
                }
            )

    def emit(current: int, etf: dict[str, str], status: DataStatus) -> None:
        elapsed = time.perf_counter() - started
        rate = current / elapsed if elapsed > 0 else 0.0
        eta = (total - current) / rate if rate > 0 else 0.0
        if progress_callback:
            progress_callback(
                {
                    "stage": "增量下载" if mode in {"incremental", "refresh"} else ("修复缺失行情" if mode == "repair_missing" else "全量重建数据"),
                    "mode": "日常增量" if mode in {"incremental", "refresh"} else ("修复缺失" if mode == "repair_missing" else "全量重建"),
                    "current": current,
                    "total": total,
                    "symbol": etf["symbol"],
                    "name": etf["name"],
                    "local_latest_date": status.local_latest_date or status.latest_date,
                    "target_date": target_date,
                    "status": status.status,
                    "error": status.error,
                    "need_update_count": len(download_targets),
                    "success_count": counts["success"],
                    "cached_success_count": counts["cached_success"],
                    "up_to_date_count": counts["up_to_date"],
                    "cold_start_count": counts["cold_start"],
                    "skipped_count": counts["skipped"],
                    "failed_count": counts["failed"],
                    "elapsed_seconds": elapsed,
                    "item_elapsed_seconds": status.elapsed_seconds,
                    "eta_seconds": eta,
                }
            )

    current_offset = len(statuses)
    for current, status in enumerate(statuses, start=1):
        emit(current, {"symbol": status.symbol, "name": status.name}, status)

    if download_targets:
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            futures = {
                executor.submit(
                    update_one_etf,
                    etf,
                    start_date,
                    end_date,
                    mode,
                    debug,
                    max_lookback_days,
                    retries,
                    timeout_per_symbol,
                    metadata_by_symbol,
                    breaker,
                ): etf
                for etf in download_targets
            }
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
                    if debug:
                        import traceback

                        print(traceback.format_exc())
                    status = _failed_status(etf["symbol"], etf["name"], str(exc), etf)
                statuses.append(status)
                if not status.success:
                    counts["failed"] += 1
                elif status.status == "cached_success":
                    counts["cached_success"] += 1
                elif status.status == "up_to_date":
                    counts["up_to_date"] += 1
                elif status.status == "cold_start":
                    counts["cold_start"] += 1
                elif status.status in {"skipped", "cold_start_deferred"}:
                    counts["skipped"] += 1
                else:
                    counts["success"] += 1
                emit(current, etf, status)

    status_by_symbol = {status.symbol: status for status in statuses}
    for etf in etf_pool:
        symbol = normalize_symbol(etf["symbol"])
        if symbol in status_by_symbol:
            continue
        if limited_scope and symbol not in target_symbols:
            cached_row = metadata_by_symbol.get(symbol)
            if cached_row:
                status_by_symbol[symbol] = _status_from_metadata_row(cached_row, etf, status_text=str(cached_row.get("status") or "cached_success"))
            continue
        try:
            df = load_etf_data(symbol, name=etf["name"]).reset_index()
            status_by_symbol[symbol] = _status_from_df(symbol, etf["name"], df, "local_cache", cached=True, meta=etf)
        except Exception as exc:  # noqa: BLE001
            status_by_symbol[symbol] = _failed_status(symbol, etf["name"], str(exc), etf)

    patch_scope = targets if symbols or max_count else etf_pool
    patched_symbols = [] if mode in {"incremental", "refresh"} else refresh_daily_data_after_close(patch_scope, today=target_date)
    for etf in etf_pool:
        symbol = normalize_symbol(etf["symbol"])
        if symbol not in patched_symbols:
            continue
        try:
            df = load_etf_data(symbol, name=etf["name"]).reset_index()
            status = _status_from_df(symbol, etf["name"], df, "realtime_close_patch", cached=False, meta=etf)
            status.target_update_date = target_date
            status_by_symbol[symbol] = status
        except Exception as exc:  # noqa: BLE001
            status_by_symbol[symbol] = _failed_status(symbol, etf["name"], str(exc), etf)

    ordered = [status_by_symbol[normalize_symbol(etf["symbol"])] for etf in etf_pool if normalize_symbol(etf["symbol"]) in status_by_symbol]
    write_coverage_report(ordered)
    _write_failed_log(ordered)
    if limited_scope:
        return [status_by_symbol[normalize_symbol(etf["symbol"])] for etf in targets if normalize_symbol(etf["symbol"]) in status_by_symbol]
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
        return {normalize_symbol(symbol) for symbol in failed["symbol"].tolist()}
    if "ETF代码" in report.columns:
        ok_col = "是否下载成功"
        failed = report[report.get(ok_col, "") != "是"]
        return {normalize_symbol(symbol) for symbol in failed["ETF代码"].tolist()}
    return set()


def write_coverage_report(statuses: list[DataStatus], path: Path = COVERAGE_REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    coverage = pd.DataFrame([status.to_row() for status in statuses])
    coverage.to_csv(path, index=False, encoding="utf-8-sig")
    _write_failed_log(statuses)
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
    previous_rows: dict[str, dict[str, Any]] = {}
    if path.exists():
        try:
            old = pd.read_csv(path, dtype={"symbol": str, "ETF代码": str}).fillna("")
            if "symbol" in old.columns:
                previous_errors = {normalize_symbol(row["symbol"]): str(row.get("failure_reason", "")) for _, row in old.iterrows()}
                previous_rows = {normalize_symbol(row["symbol"]): row.to_dict() for _, row in old.iterrows()}
            elif "ETF代码" in old.columns:
                previous_errors = {normalize_symbol(row["ETF代码"]): str(row.get("失败原因", "")) for _, row in old.iterrows()}
        except Exception:
            previous_errors = {}

    for etf in etf_pool:
        symbol = normalize_symbol(etf["symbol"])
        name = etf["name"]
        try:
            df = load_etf_data(symbol, name=name).reset_index()
            save_etf_data(symbol, df, name=name, source="local_cache")
            status = _status_from_df(symbol, name, df, "local_cache", cached=True, meta=etf)
            previous = previous_rows.get(symbol, {})
            previous_error = str(previous.get("error") or previous.get("failure_reason") or "")
            previous_target = str(previous.get("target_update_date") or "")
            previous_status = str(previous.get("status") or "")
            if previous_target:
                status.target_update_date = previous_target
            if previous_error and previous_status in {"cached_success", "failed", "skipped"}:
                status.status = previous_status if previous_status == "cached_success" else status.status
                status.failure_reason = previous_error
                status.source = str(previous.get("source") or status.source)
            statuses.append(status)
        except Exception as exc:  # noqa: BLE001
            error = previous_errors.get(symbol) or str(exc)
            statuses.append(_failed_status(symbol, name, error, etf))

    write_coverage_report(statuses, path)
    return statuses


def update_all_data_incremental(
    etf_pool: list[dict[str, str]],
    expected_signal_date: str | pd.Timestamp,
    max_lookback_days: int = 5,
    max_workers: int = 8,
    timeout_per_symbol: int = 20,
    start_date: str = "20190101",
    symbols: set[str] | None = None,
    max_count: int | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    debug: bool = False,
    retries: int = 1,
    source_circuit_breaker_threshold: int = 20,
) -> list[DataStatus]:
    target = pd.Timestamp(expected_signal_date).normalize()
    return update_many_etfs(
        etf_pool=etf_pool,
        start_date=start_date,
        end_date=target.strftime("%Y-%m-%d"),
        mode="incremental",
        symbols=symbols,
        max_count=max_count,
        max_workers=max_workers,
        debug=debug,
        progress_callback=progress_callback,
        max_lookback_days=max_lookback_days,
        retries=retries,
        timeout_per_symbol=timeout_per_symbol,
        circuit_breaker_threshold=source_circuit_breaker_threshold,
        cold_start_limit=20,
    )


def update_all_data(
    etf_pool: list[dict[str, str]],
    start_date: str,
    end_date: str | None = None,
    refresh: bool = False,
    retry_failed_only: bool = False,
    mode: str | None = None,
    symbols: set[str] | None = None,
    max_count: int | None = None,
    max_workers: int = 6,
    debug: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[str], list[str], list[DataStatus]]:
    if mode in {"incremental", "refresh", "repair_missing", "rebuild", "full_refresh"} and not retry_failed_only:
        if mode in {"incremental", "refresh"}:
            statuses = update_all_data_incremental(
                etf_pool=etf_pool,
                expected_signal_date=_target_complete_date(end_date),
                max_lookback_days=5,
                max_workers=max_workers,
                timeout_per_symbol=20,
                start_date=start_date,
                symbols=symbols,
                max_count=max_count,
                progress_callback=progress_callback,
                debug=debug,
                retries=1,
                source_circuit_breaker_threshold=20,
            )
        else:
            normalized_mode = "rebuild" if mode == "full_refresh" else mode
            statuses = update_many_etfs(
                etf_pool=etf_pool,
                start_date=start_date,
                end_date=end_date,
                mode=normalized_mode,
                symbols=symbols,
                max_count=max_count,
                max_workers=3 if normalized_mode == "rebuild" else max_workers,
                debug=debug,
                progress_callback=progress_callback,
                max_lookback_days=5,
                retries=3 if normalized_mode in {"repair_missing", "rebuild"} else 1,
                timeout_per_symbol=45 if normalized_mode in {"repair_missing", "rebuild"} else 20,
                circuit_breaker_threshold=50 if normalized_mode == "rebuild" else 20,
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
    if symbols:
        normalized_symbols = {normalize_symbol(symbol) for symbol in symbols}
        targets = [etf for etf in targets if normalize_symbol(etf["symbol"]) in normalized_symbols]
    if max_count is not None and max_count > 0:
        targets = targets[: int(max_count)]

    if retry_failed_only and not targets:
        print("No failed or missing ETFs found for retry.")
        statuses = build_data_coverage_report(etf_pool)
        return successes, errors, statuses

    total = len(targets)
    for idx, etf in enumerate(targets, start=1):
        symbol = normalize_symbol(etf["symbol"])
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
            df, source = download_etf_history(symbol=symbol, start_date=start_date, end_date=end_date)
            path = save_etf_data(symbol, df, name=name, source=source)
            status = _status_from_df(symbol, name, df, source, cached=False, meta=etf)
            statuses.append(status)
            msg = f"{symbol} {name}: downloaded source={source} rows={status.rows} range={status.start_date}->{status.end_date} path={path}"
            successes.append(msg)
            print(f"    OK source={source} rows={status.rows}")
        except Exception as exc:  # noqa: BLE001
            if debug:
                import traceback

                print(traceback.format_exc())
            reason = str(exc)
            if _looks_inactive_or_unsupported(etf, reason):
                status = _skipped_status(symbol, name, reason, etf)
                statuses.append(status)
            else:
                statuses.append(_failed_status(symbol, name, reason, etf))
                errors.append(f"{symbol} {name}: {reason}")
            print(f"    ERR {symbol} {name}: {reason}")
        time.sleep(0.05)

    status_by_symbol = {status.symbol: status for status in statuses}
    for etf in etf_pool:
        symbol = normalize_symbol(etf["symbol"])
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
