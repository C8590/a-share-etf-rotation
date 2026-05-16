from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Iterable

import pandas as pd


DATA_DIR = Path("data") / "cache"
CACHE_META_DIR = Path("data") / "cache_meta"
PRICE_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]
METADATA_COLUMNS = ["symbol", "name", "source"]
REQUIRED_COLUMNS = PRICE_COLUMNS + METADATA_COLUMNS
OHLC_COLUMNS = ["open", "high", "low", "close"]
CACHE_SCHEMA_VERSION = "1.0"
DATA_SCHEMA_VERSION = "1.0"
CACHE_METADATA_CREATED_BY = "ETF-GAP-003B"


def ensure_data_dir(data_dir: Path = DATA_DIR) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def ensure_cache_meta_dir(meta_dir: Path = CACHE_META_DIR) -> Path:
    meta_dir.mkdir(parents=True, exist_ok=True)
    return meta_dir


def get_csv_path(symbol: str, data_dir: Path = DATA_DIR) -> Path:
    return ensure_data_dir(data_dir) / f"{str(symbol).zfill(6)}.csv"


def get_cache_metadata_path(symbol: str, meta_dir: Path = CACHE_META_DIR) -> Path:
    return ensure_cache_meta_dir(meta_dir) / f"{str(symbol).zfill(6)}.json"


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def infer_cache_source_metadata(source: str) -> dict[str, Any]:
    text = str(source or "").strip().lower()
    if "fund_etf_hist_sina" in text:
        return {
            "adjust": "unknown",
            "api_name": "fund_etf_hist_sina",
            "endpoint": "akshare.fund_etf_hist_sina",
            "download_method": "akshare_sina",
        }
    if "fund_etf_hist_em" in text and ("qfq" in text or "adjust=qfq" in text):
        return {
            "adjust": "qfq",
            "api_name": "fund_etf_hist_em",
            "endpoint": "akshare.fund_etf_hist_em",
            "download_method": "akshare_em_chunked_qfq",
        }
    if "fund_etf_hist_em" in text and (
        "none" in text
        or "adjust=none" in text
        or 'adjust=""' in text
        or "adjust=''" in text
        or "adjust=)" in text
    ):
        return {
            "adjust": "none",
            "api_name": "fund_etf_hist_em",
            "endpoint": "akshare.fund_etf_hist_em",
            "download_method": "akshare_em_chunked_none",
        }
    return {
        "adjust": "unknown" if text == "local_cache" else "",
        "api_name": "local_cache" if text == "local_cache" else "",
        "endpoint": "",
        "download_method": "local_cache" if text == "local_cache" else "unknown",
    }


def build_cache_metadata(
    symbol: str,
    df: pd.DataFrame,
    *,
    name: str = "",
    source: str = "",
    cache_file: str | Path = "",
    downloaded_at: str | None = None,
    fallback_used: bool | None = None,
    fallback_chain: list[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    symbol = str(symbol).zfill(6)
    frame = df.reset_index() if "date" not in df.columns else df.copy()
    dates = pd.to_datetime(frame["date"], errors="coerce") if "date" in frame.columns else pd.Series(dtype="datetime64[ns]")
    inferred = infer_cache_source_metadata(source)
    metadata: dict[str, Any] = {
        "symbol": symbol,
        "name": name,
        "source": source,
        "adjust": inferred["adjust"],
        "api_name": inferred["api_name"],
        "endpoint": inferred["endpoint"],
        "download_method": inferred["download_method"],
        "fallback_used": bool(fallback_used) if fallback_used is not None else False,
        "fallback_chain": fallback_chain or ([source] if source else []),
        "cache_file": str(cache_file or get_csv_path(symbol)),
        "downloaded_at": downloaded_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "start_date": _date_text(dates.min()) if not dates.empty else "",
        "end_date": _date_text(dates.max()) if not dates.empty else "",
        "row_count": int(len(frame)),
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "data_schema_version": DATA_SCHEMA_VERSION,
        "created_by": CACHE_METADATA_CREATED_BY,
    }
    metadata.update({key: value for key, value in overrides.items() if value is not None})
    return metadata


def write_cache_metadata(
    symbol: str,
    metadata: dict[str, Any],
    meta_dir: Path = CACHE_META_DIR,
) -> Path:
    path = get_cache_metadata_path(symbol, meta_dir)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_cache_metadata(symbol: str, meta_dir: Path = CACHE_META_DIR) -> dict[str, Any] | None:
    path = get_cache_metadata_path(symbol, meta_dir)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_for_storage(
    symbol: str,
    df: pd.DataFrame,
    name: str = "",
    source: str = "",
) -> pd.DataFrame:
    symbol = str(symbol).zfill(6)
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
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    out["name"] = out.get("name", name)
    out["name"] = out["name"].replace("", name)
    out["source"] = out.get("source", source)
    out["source"] = out["source"].replace("", source)

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
    return path


def load_etf_data(
    symbol: str,
    data_dir: Path = DATA_DIR,
    name: str = "",
    source: str = "",
    recent_rows: int | None = None,
) -> pd.DataFrame:
    symbol = str(symbol).zfill(6)
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
        symbol = str(raw_symbol).zfill(6)
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
