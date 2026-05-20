from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


FORBIDDEN_FEATURE_EXACT = {
    "auto_label",
    "label_status",
    "label_base_date",
    "exit_within_3d",
    "market_return_10d",
    "sector_return_10d",
}
FORBIDDEN_FEATURE_PREFIXES = (
    "future_return_",
    "future_max_",
    "outperform_",
)

NUMERIC_FEATURES = [
    "momentum_score",
    "acceleration_score",
    "entry_score",
    "sector_rank",
    "etf_rank",
]
PRE_TRADE_CATEGORICAL_FEATURES = [
    "market_state",
    "sector_state",
    "trend_maturity_bucket",
    "sector_l1",
    "sector_l2",
    "theme",
    "asset_class",
    "is_defensive",
    "is_broad_market",
]
BEHAVIOR_FEATURES = [
    "was_candidate",
    "was_selected",
    "was_bought",
    "exclude_reason",
]
IDENTITY_COLUMNS = ["code", "name"]
TARGETS = {
    "good_entry": "is_good_entry",
    "bad_entry": "is_bad_entry",
}


@dataclass(frozen=True)
class FeatureFrame:
    matrix: pd.DataFrame
    feature_names: list[str]
    numeric_features: list[str]
    categorical_features: list[str]


@dataclass(frozen=True)
class SplitResult:
    train_mask: pd.Series
    test_mask: pd.Series
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    fallback_used: bool
    note: str


def prepare_ml_samples(samples: pd.DataFrame) -> pd.DataFrame:
    df = samples.copy()
    if "label_status" not in df.columns:
        df["label_status"] = ""
    if "auto_label" not in df.columns:
        df["auto_label"] = "unlabeled"
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.loc[df["label_status"].fillna("").astype(str).eq("ok")].copy()
    df = df.loc[df["auto_label"].isin(["good_entry", "bad_entry", "neutral_entry"])].copy()
    df["is_good_entry"] = (df["auto_label"] == "good_entry").astype(int)
    df["is_bad_entry"] = (df["auto_label"] == "bad_entry").astype(int)

    if "sector_l2" not in df.columns:
        df["sector_l2"] = df.get("sector", "unknown")
    for col in ["sector_l1", "sector_l2", "theme", "asset_class", "market_state", "sector_state", "exclude_reason"]:
        if col not in df.columns:
            df[col] = "unknown"
        df[col] = df[col].fillna("unknown").astype(str).replace("", "unknown")
    for col in ["is_defensive", "is_broad_market", "was_candidate", "was_selected", "was_bought"]:
        if col not in df.columns:
            df[col] = False
        df[col] = _bool_series(df[col])
    for col in NUMERIC_FEATURES + ["trend_maturity", "entry_score"]:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["trend_maturity_bucket"] = df["trend_maturity"].map(_trend_maturity_bucket)
    return df.sort_values(["trade_date", "code"]).reset_index(drop=True)


def build_time_split(
    df: pd.DataFrame,
    train_start: str = "2024-09-24",
    train_end: str = "2025-12-31",
    test_start: str = "2026-01-01",
    test_end: str = "2026-05-19",
    min_test_rows: int = 20,
) -> SplitResult:
    dates = pd.to_datetime(df["trade_date"], errors="coerce")
    train_mask = dates.between(pd.Timestamp(train_start), pd.Timestamp(train_end), inclusive="both")
    test_mask = dates.between(pd.Timestamp(test_start), pd.Timestamp(test_end), inclusive="both")
    fallback = False
    note = "fixed calendar split"
    if int(train_mask.sum()) == 0 or int(test_mask.sum()) < min_test_rows:
        unique_dates = pd.Series(dates.dropna().sort_values().unique())
        if unique_dates.empty:
            train_mask = pd.Series(False, index=df.index)
            test_mask = pd.Series(False, index=df.index)
            split_date = pd.NaT
        else:
            split_idx = max(0, min(len(unique_dates) - 1, int(np.floor(len(unique_dates) * 0.70)) - 1))
            split_date = pd.Timestamp(unique_dates.iloc[split_idx])
            train_mask = dates <= split_date
            test_mask = dates > split_date
        fallback = True
        note = "fallback 70/30 chronological split because fixed test set was too small"
        train_start = _date_min(dates[train_mask])
        train_end = "" if pd.isna(split_date) else str(split_date.date())
        test_start = _date_min(dates[test_mask])
        test_end = _date_max(dates[test_mask])
    return SplitResult(
        train_mask=pd.Series(train_mask, index=df.index),
        test_mask=pd.Series(test_mask, index=df.index),
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        fallback_used=fallback,
        note=note,
    )


def build_feature_frame(df: pd.DataFrame, feature_set: str) -> FeatureFrame:
    if feature_set not in {"pre_trade_features_only", "behavior_augmented"}:
        raise ValueError(f"unknown feature_set: {feature_set}")
    numeric = [col for col in NUMERIC_FEATURES if col in df.columns]
    categorical = list(PRE_TRADE_CATEGORICAL_FEATURES)
    if feature_set == "behavior_augmented":
        categorical += BEHAVIOR_FEATURES
    for col in categorical:
        if col not in df.columns:
            df[col] = "unknown"

    pieces: list[pd.DataFrame] = []
    if numeric:
        num = df[numeric].apply(pd.to_numeric, errors="coerce")
        med = num.median(numeric_only=True).fillna(0.0)
        pieces.append(num.fillna(med).astype(float))
    cat = df[categorical].copy()
    for col in categorical:
        if col in {"is_defensive", "is_broad_market", "was_candidate", "was_selected", "was_bought"}:
            cat[col] = _bool_series(cat[col]).map({True: "true", False: "false"})
        else:
            cat[col] = cat[col].fillna("unknown").astype(str).replace("", "unknown")
    pieces.append(pd.get_dummies(cat, columns=categorical, prefix_sep="=", dtype=float))
    matrix = pd.concat(pieces, axis=1)
    matrix = matrix.loc[:, ~matrix.columns.duplicated()].astype(float)
    feature_names = list(matrix.columns)
    validate_no_forbidden_features(feature_names)
    return FeatureFrame(matrix=matrix, feature_names=feature_names, numeric_features=numeric, categorical_features=categorical)


def validate_no_forbidden_features(feature_names: Iterable[str]) -> None:
    leaked = []
    for name in feature_names:
        base = str(name).split("=", 1)[0]
        if base in FORBIDDEN_FEATURE_EXACT or str(name) in FORBIDDEN_FEATURE_EXACT:
            leaked.append(str(name))
        if any(str(name).startswith(prefix) or base.startswith(prefix) for prefix in FORBIDDEN_FEATURE_PREFIXES):
            leaked.append(str(name))
        if base in IDENTITY_COLUMNS or str(name) in IDENTITY_COLUMNS:
            leaked.append(str(name))
    if leaked:
        raise ValueError(f"forbidden ML feature leakage detected: {sorted(set(leaked))}")


def align_feature_frames(train: pd.DataFrame, test: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    columns = sorted(set(train.columns) | set(test.columns))
    return train.reindex(columns=columns, fill_value=0.0), test.reindex(columns=columns, fill_value=0.0)


def _trend_maturity_bucket(value: object) -> str:
    v = pd.to_numeric(value, errors="coerce")
    if pd.isna(v):
        return "unknown"
    if float(v) <= 0.25:
        return "startup"
    if float(v) <= 0.50:
        return "confirmation"
    if float(v) <= 0.75:
        return "main_uptrend"
    return "overheat"


def _bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.fillna(False)
    return s.fillna(False).map(lambda v: str(v).strip().lower() in {"1", "true", "yes", "y", "selected"})


def _date_min(s: pd.Series) -> str:
    return "" if s.dropna().empty else str(pd.Timestamp(s.min()).date())


def _date_max(s: pd.Series) -> str:
    return "" if s.dropna().empty else str(pd.Timestamp(s.max()).date())
