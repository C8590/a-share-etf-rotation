from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd


DATA_DIR = Path("data") / "cache"
PRICE_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]
METADATA_COLUMNS = ["symbol", "name", "source"]
REQUIRED_COLUMNS = PRICE_COLUMNS + METADATA_COLUMNS


def ensure_data_dir(data_dir: Path = DATA_DIR) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_csv_path(symbol: str, data_dir: Path = DATA_DIR) -> Path:
    return ensure_data_dir(data_dir) / f"{str(symbol).zfill(6)}.csv"


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

    out = out.dropna(subset=["date", "close"]).sort_values("date")
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
    source: str = "local_cache",
) -> pd.DataFrame:
    symbol = str(symbol).zfill(6)
    path = get_csv_path(symbol, data_dir)
    if not path.exists():
        raise FileNotFoundError(f"Local data for {symbol} not found at {path}; run python main.py update-data first")

    df = pd.read_csv(path, dtype={"symbol": str})
    df = normalize_for_storage(symbol, df, name=name, source=source)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def load_market_data(
    symbols: Iterable[str],
    data_dir: Path = DATA_DIR,
    allow_partial: bool = False,
    etf_info: dict[str, dict[str, str]] | None = None,
) -> dict[str, pd.DataFrame]:
    market_data: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    etf_info = etf_info or {}

    for raw_symbol in symbols:
        symbol = str(raw_symbol).zfill(6)
        try:
            item = etf_info.get(symbol, {})
            data = load_etf_data(symbol, data_dir, name=item.get("name", ""))
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
