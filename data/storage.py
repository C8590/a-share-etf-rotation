from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import threading
from typing import Iterable

import pandas as pd


DATA_DIR = Path("data") / "cache"
CACHE_METADATA_FILE = "_metadata.csv"
PRICE_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]
METADATA_COLUMNS = ["symbol", "name", "source"]
REQUIRED_COLUMNS = PRICE_COLUMNS + METADATA_COLUMNS
OHLC_COLUMNS = ["open", "high", "low", "close"]
CACHE_METADATA_COLUMNS = [
    "symbol",
    "name",
    "cache_path",
    "rows",
    "start_date",
    "latest_date",
    "last_update_time",
    "source",
    "status",
    "file_mtime",
    "file_size",
    "last_error",
]
CACHE_METADATA_LOCK = threading.RLock()


def normalize_symbol(symbol: object) -> str:
    text = str(symbol or "").strip().upper()
    if not text:
        return ""
    if text.startswith(("SH", "SZ")):
        text = text[2:]
    if "." in text:
        text = text.split(".", 1)[0]
    match = re.search(r"\d{6}", text)
    if match:
        return match.group(0)
    digits = re.sub(r"\D", "", text)
    return digits.zfill(6)[-6:] if digits else ""


def ensure_data_dir(data_dir: Path = DATA_DIR) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_csv_path(symbol: str, data_dir: Path = DATA_DIR) -> Path:
    code = normalize_symbol(symbol)
    if not code:
        raise ValueError(f"Invalid ETF symbol: {symbol}")
    return ensure_data_dir(data_dir) / f"{code}.csv"


def get_metadata_path(data_dir: Path = DATA_DIR) -> Path:
    return ensure_data_dir(data_dir) / CACHE_METADATA_FILE


def _file_signature(path: Path) -> tuple[float, int]:
    stat = path.stat()
    return float(stat.st_mtime), int(stat.st_size)


def _metadata_frame(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    path = get_metadata_path(data_dir)
    if not path.exists():
        return pd.DataFrame(columns=CACHE_METADATA_COLUMNS)
    try:
        frame = pd.read_csv(path, dtype={"symbol": str}).fillna("")
    except Exception:
        return pd.DataFrame(columns=CACHE_METADATA_COLUMNS)
    for column in CACHE_METADATA_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    return frame[CACHE_METADATA_COLUMNS]


def read_cache_metadata(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Read the cache metadata index without touching individual ETF CSV files."""
    return _metadata_frame(data_dir)


def write_cache_metadata(frame: pd.DataFrame, data_dir: Path = DATA_DIR) -> Path:
    with CACHE_METADATA_LOCK:
        path = get_metadata_path(data_dir)
        out = frame.copy()
        for column in CACHE_METADATA_COLUMNS:
            if column not in out.columns:
                out[column] = ""
        out["symbol"] = out["symbol"].map(normalize_symbol)
        out = out[out["symbol"].astype(str).str.len() == 6]
        out = out.drop_duplicates("symbol", keep="last").sort_values("symbol")
        out[CACHE_METADATA_COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
        return path


def cache_metadata_is_fresh(row: dict[str, object], data_dir: Path = DATA_DIR) -> bool:
    cache_path = Path(str(row.get("cache_path") or ""))
    if not cache_path.is_absolute():
        cache_path = data_dir / cache_path.name
    if not cache_path.exists():
        return False
    try:
        mtime, size = _file_signature(cache_path)
        return abs(float(row.get("file_mtime") or 0) - mtime) < 1e-6 and int(float(row.get("file_size") or 0)) == size
    except Exception:
        return False


def metadata_records(data_dir: Path = DATA_DIR) -> dict[str, dict[str, object]]:
    frame = read_cache_metadata(data_dir)
    return {normalize_symbol(row["symbol"]): row for row in frame.to_dict("records") if normalize_symbol(row.get("symbol", ""))}


def _metadata_row_from_frame(
    symbol: str,
    df: pd.DataFrame,
    path: Path,
    name: str = "",
    source: str = "",
    status: str = "ok",
    last_error: str = "",
) -> dict[str, object]:
    frame = df.reset_index() if "date" not in df.columns else df.copy()
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna() if "date" in frame.columns else pd.Series(dtype="datetime64[ns]")
    file_mtime, file_size = _file_signature(path)
    source_values = frame["source"].dropna().astype(str).str.strip() if "source" in frame.columns else pd.Series(dtype=str)
    return {
        "symbol": normalize_symbol(symbol),
        "name": name or (str(frame["name"].dropna().iloc[-1]) if "name" in frame.columns and not frame["name"].dropna().empty else ""),
        "cache_path": str(path),
        "rows": int(len(frame)),
        "start_date": "" if dates.empty else str(dates.min().date()),
        "latest_date": "" if dates.empty else str(dates.max().date()),
        "last_update_time": datetime.now().isoformat(timespec="seconds"),
        "source": source or (str(source_values.iloc[-1]) if not source_values.empty else ""),
        "status": status,
        "file_mtime": file_mtime,
        "file_size": file_size,
        "last_error": last_error,
    }


def update_cache_metadata(
    symbol: str,
    df: pd.DataFrame,
    data_dir: Path = DATA_DIR,
    name: str = "",
    source: str = "",
    status: str = "ok",
    last_error: str = "",
) -> None:
    symbol = normalize_symbol(symbol)
    if not symbol:
        return
    with CACHE_METADATA_LOCK:
        path = get_csv_path(symbol, data_dir)
        if not path.exists():
            return
        frame = read_cache_metadata(data_dir)
        row = _metadata_row_from_frame(symbol, df, path, name=name, source=source, status=status, last_error=last_error)
        frame = frame[frame["symbol"].map(normalize_symbol) != symbol]
        frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
        write_cache_metadata(frame, data_dir)


def scan_cache_metadata(symbol: str, data_dir: Path = DATA_DIR, name: str = "", source: str = "") -> dict[str, object] | None:
    symbol = normalize_symbol(symbol)
    if not symbol:
        return None
    try:
        df = load_etf_data(symbol, data_dir=data_dir, name=name, source=source).reset_index()
        update_cache_metadata(symbol, df, data_dir=data_dir, name=name, source=source or "local_cache")
        return metadata_records(data_dir).get(symbol)
    except Exception:
        return None


def normalize_for_storage(
    symbol: str,
    df: pd.DataFrame,
    name: str = "",
    source: str = "",
) -> pd.DataFrame:
    symbol = normalize_symbol(symbol)
    if not symbol:
        raise ValueError("ETF symbol is empty")
    missing = [col for col in PRICE_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"{symbol} data missing required columns: {', '.join(missing)}")
    if "source" not in df.columns and not source:
        raise ValueError(f"{symbol} data missing required source field")

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out["symbol"] = out.get("symbol", symbol)
    out["symbol"] = out["symbol"].map(lambda value: normalize_symbol(value) or symbol)
    out["name"] = out.get("name", name)
    out["name"] = out["name"].fillna(name).replace("", name)
    out["source"] = out.get("source", source)
    out["source"] = out["source"].fillna(source).replace("", source)

    required_non_null = ["date", *OHLC_COLUMNS, "volume", "amount", "source"]
    invalid = [col for col in required_non_null if out[col].isna().any() or (out[col].astype(str).str.strip() == "").any()]
    if invalid:
        raise ValueError(f"{symbol} data contains null or invalid required fields: {', '.join(invalid)}")

    out = out.sort_values("date")
    out = out.drop_duplicates("date", keep="last")
    return out[REQUIRED_COLUMNS]


def save_etf_data(
    symbol: str,
    df: pd.DataFrame,
    data_dir: Path = DATA_DIR,
    name: str = "",
    source: str = "",
) -> Path:
    path = get_csv_path(symbol, data_dir)
    out = normalize_for_storage(symbol, df, name=name, source=source)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    update_cache_metadata(symbol, out, data_dir=data_dir, name=name, source=source, status="ok")
    return path


def load_etf_data(
    symbol: str,
    data_dir: Path = DATA_DIR,
    name: str = "",
    source: str = "",
    recent_rows: int | None = None,
) -> pd.DataFrame:
    symbol = normalize_symbol(symbol)
    if not symbol:
        raise ValueError("ETF symbol is empty")
    path = get_csv_path(symbol, data_dir)
    if not path.exists():
        raise FileNotFoundError(f"Local data for {symbol} not found at {path}; run python main.py update-data first")

    df = pd.read_csv(path, dtype={"symbol": str})
    df = normalize_for_storage(symbol, df, name=name, source=source)
    if recent_rows is not None and recent_rows > 0:
        df = df.tail(int(recent_rows)).copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def load_market_data(
    symbols: Iterable[str],
    data_dir: Path = DATA_DIR,
    allow_partial: bool = False,
    etf_info: dict[str, dict[str, str]] | None = None,
    recent_rows: int | None = None,
) -> dict[str, pd.DataFrame]:
    market_data: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    etf_info = etf_info or {}

    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        if not symbol:
            errors.append(f"{raw_symbol}: invalid ETF symbol")
            continue
        try:
            item = etf_info.get(symbol, {})
            data = load_etf_data(symbol, data_dir, name=item.get("name", ""), recent_rows=recent_rows)
        except Exception as exc:  # noqa: BLE001 - keep CLI feedback clear for data issues
            errors.append(f"{symbol}: {exc}")
            continue

        if data.empty:
            errors.append(f"{symbol}: local CSV is empty")
            continue
        market_data[symbol] = data

    if errors and (not allow_partial or not market_data):
        joined = "\n".join(f"- {item}" for item in errors)
        raise RuntimeError(f"Failed to load local market data:\n{joined}")

    if errors:
        joined = "\n".join(f"- {item}" for item in errors)
        print(f"Warning: these ETFs were skipped because local data is unavailable:\n{joined}")

    return market_data


def build_price_matrix(market_data: dict[str, pd.DataFrame], field: str) -> pd.DataFrame:
    frames = []
    for symbol, df in market_data.items():
        if field not in df.columns:
            raise ValueError(f"{symbol} data missing field: {field}")
        frames.append(df[field].rename(symbol))

    if not frames:
        raise ValueError("No usable market data")

    return pd.concat(frames, axis=1).sort_index()
