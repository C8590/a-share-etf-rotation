from __future__ import annotations

import pandas as pd

from .feature_builder import build_etf_features_for_day
from .config import HistoricalMLConfig
from .schemas import REPLAY_FORBIDDEN_LABEL_COLUMNS


def assert_signal_execution_separation(samples: pd.DataFrame) -> None:
    if not {"signal_date", "execution_date"}.issubset(samples.columns):
        raise AssertionError("samples must include signal_date and execution_date")
    sig = pd.to_datetime(samples["signal_date"])
    exe = pd.to_datetime(samples["execution_date"])
    bad = samples.loc[exe.notna() & (exe <= sig)]
    if not bad.empty:
        raise AssertionError(f"execution_date must be after signal_date; bad rows={len(bad)}")


def assert_required_columns(df: pd.DataFrame, columns: list[str], table_name: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise AssertionError(f"{table_name} missing columns: {missing}")


def assert_no_replay_label_columns(df: pd.DataFrame, table_name: str) -> None:
    leaked = [c for c in REPLAY_FORBIDDEN_LABEL_COLUMNS if c in df.columns]
    leaked += [c for c in df.columns if c.startswith("future_return_") and c not in leaked]
    if leaked:
        raise AssertionError(f"{table_name} contains future label columns in replay stage: {sorted(leaked)}")


def assert_source_is_historical_replay(df: pd.DataFrame, table_name: str) -> None:
    if "source" not in df.columns:
        raise AssertionError(f"{table_name} missing source column")
    values = set(df["source"].dropna().astype(str).unique())
    if values != {"historical_replay"}:
        raise AssertionError(f"{table_name} source must be historical_replay only; got {sorted(values)}")


def assert_no_future_feature_leakage(price_df: pd.DataFrame, trade_date, config: HistoricalMLConfig) -> None:
    """Perturb future rows and confirm feature rows for trade_date do not change."""

    base_etf, _ = build_etf_features_for_day(price_df, trade_date, config)
    mutated = price_df.copy()
    trade_date = pd.Timestamp(trade_date).normalize()
    future_mask = pd.to_datetime(mutated["date"]) > trade_date
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in mutated.columns:
            mutated.loc[future_mask, col] = mutated.loc[future_mask, col] * 1000 + 123
    mutated_etf, _ = build_etf_features_for_day(mutated, trade_date, config)

    compare_cols = [
        "code",
        "momentum_score",
        "acceleration_score",
        "entry_score",
        "trend_maturity",
        "sector_rank",
        "etf_rank",
    ]
    a = base_etf[compare_cols].sort_values("code").reset_index(drop=True)
    b = mutated_etf[compare_cols].sort_values("code").reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)
