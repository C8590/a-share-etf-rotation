from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .schemas import PRICE_REQUIRED_COLUMNS


def read_price_data(path: str | Path) -> pd.DataFrame:
    """Read ETF price data and normalize date/code fields.

    Expected long format columns: date, code, name, close, sector. Optional
    columns: open, high, low, volume, amount, sector_l1.
    """

    path = Path(path)
    if path.suffix.lower() in {".parquet", ".pq"}:
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)

    missing = [c for c in PRICE_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"price data missing required columns: {missing}")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["code"] = df["code"].astype(str)
    df["name"] = df["name"].astype(str)
    df["sector"] = df["sector"].astype(str)
    if "sector_l1" not in df.columns:
        df["sector_l1"] = df["sector"]
    if "high" not in df.columns:
        df["high"] = df["close"]
    if "low" not in df.columns:
        df["low"] = df["close"]
    if "amount" not in df.columns:
        df["amount"] = 0.0
    if "volume" not in df.columns:
        df["volume"] = 0.0
    if "open" not in df.columns:
        df["open"] = df["close"]

    numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values(["date", "code"]).reset_index(drop=True)
    return df


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_table(df: pd.DataFrame, out_dir: str | Path, name: str, fmt: str = "csv") -> Path:
    out_dir = ensure_dir(out_dir)
    if fmt == "parquet":
        path = out_dir / f"{name}.parquet"
        df.to_parquet(path, index=False)
    else:
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def write_daily_partition(df: pd.DataFrame, out_dir: str | Path, table: str, trade_date, fmt: str = "csv") -> Path:
    date_str = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
    part_dir = ensure_dir(Path(out_dir) / table / f"trade_date={date_str}")
    if fmt == "parquet":
        path = part_dir / "part.parquet"
        df.to_parquet(path, index=False)
    else:
        path = part_dir / "part.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path)


def reorder_columns(df: pd.DataFrame, preferred_columns: Iterable[str]) -> pd.DataFrame:
    preferred = [c for c in preferred_columns if c in df.columns]
    rest = [c for c in df.columns if c not in preferred]
    return df[preferred + rest]
