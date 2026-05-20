from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import HistoricalMLConfig
from .io_utils import reorder_columns
from .schemas import ENTRY_CANDIDATE_COLUMNS, FUTURE_LABEL_COLUMNS


@dataclass
class FutureLabeler:
    """Attach future performance labels after replay samples are generated.

    This stage intentionally uses future data and must run after feature sample
    generation.  It never feeds values back into the replay engine.
    """

    price_df: pd.DataFrame
    config: HistoricalMLConfig = HistoricalMLConfig()

    def __post_init__(self):
        self.price_df = self.price_df.copy()
        self.price_df["date"] = pd.to_datetime(self.price_df["date"]).dt.normalize()
        self.price_df["code"] = self.price_df["code"].astype(str)
        if "high" not in self.price_df.columns:
            self.price_df["high"] = self.price_df["close"]
        if "low" not in self.price_df.columns:
            self.price_df["low"] = self.price_df["close"]
        if "sector_l1" not in self.price_df.columns:
            self.price_df["sector_l1"] = self.price_df["sector"]
        self.price_df = self.price_df.sort_values(["date", "code"]).reset_index(drop=True)
        self.trading_dates = list(sorted(pd.to_datetime(self.price_df["date"].unique())))
        self.date_pos = {pd.Timestamp(d): i for i, d in enumerate(self.trading_dates)}
        self.by_code = {code: g.sort_values("date").set_index("date") for code, g in self.price_df.groupby("code")}

    def attach_labels(self, candidates: pd.DataFrame) -> pd.DataFrame:
        if candidates.empty:
            return candidates.copy()

        rows = []
        for _, row in candidates.iterrows():
            labels = self._label_one(row)
            rows.append(labels)
        label_df = pd.DataFrame(rows)
        out = pd.concat([candidates.reset_index(drop=True), label_df.reset_index(drop=True)], axis=1)
        out = self._assign_auto_label(out)
        return reorder_columns(out, list(ENTRY_CANDIDATE_COLUMNS) + FUTURE_LABEL_COLUMNS)

    def _resolve_base_date(self, row) -> pd.Timestamp | None:
        execution_date = row.get("execution_date", pd.NaT)
        if pd.notna(execution_date):
            execution_date = pd.Timestamp(execution_date).normalize()
            if execution_date in self.date_pos:
                return execution_date
        trade_date = pd.Timestamp(row["trade_date"]).normalize()
        for d in self.trading_dates:
            if d > trade_date:
                return d
        return None

    def _label_one(self, row) -> dict:
        code = str(row["code"])
        sector = str(row.get("sector", ""))
        base_date = self._resolve_base_date(row)
        labels = {c: np.nan for c in FUTURE_LABEL_COLUMNS if c not in {"outperform_market_10d", "outperform_sector_10d", "exit_within_3d", "auto_label", "label_status"}}
        labels.update(
            {
                "outperform_market_10d": False,
                "outperform_sector_10d": False,
                "exit_within_3d": False,
                "auto_label": "unlabeled",
                "label_status": "ok",
            }
        )
        if base_date is None or code not in self.by_code:
            labels["label_status"] = "missing_base_date_or_code"
            return labels
        labels["label_base_date"] = base_date
        g = self.by_code[code]
        if base_date not in g.index or pd.isna(g.loc[base_date, "close"]):
            labels["label_status"] = "missing_base_price"
            return labels
        base_close = float(g.loc[base_date, "close"])
        if base_close <= 0:
            labels["label_status"] = "bad_base_price"
            return labels

        base_pos = self.date_pos[base_date]
        max_horizon = max(self.config.label_horizons)
        if base_pos + max_horizon >= len(self.trading_dates):
            labels["label_status"] = "insufficient_future_data"

        for h in self.config.label_horizons:
            if base_pos + h < len(self.trading_dates):
                target_date = self.trading_dates[base_pos + h]
                if target_date in g.index and pd.notna(g.loc[target_date, "close"]):
                    labels[f"future_return_{h}d"] = float(g.loc[target_date, "close"] / base_close - 1.0)

        horizon_dates_10 = self.trading_dates[base_pos + 1 : min(base_pos + 11, len(self.trading_dates))]
        future = g.loc[g.index.intersection(horizon_dates_10)]
        if not future.empty:
            labels["future_max_gain_10d"] = float(future["high"].max() / base_close - 1.0)
            labels["future_max_drawdown_10d"] = float(future["low"].min() / base_close - 1.0)

        # Equal-weight market and sector returns use only the label window; this is label side only.
        if base_pos + 10 < len(self.trading_dates):
            target_10d = self.trading_dates[base_pos + 10]
            labels["market_return_10d"] = self._basket_return(base_date, target_10d, sector=None)
            labels["sector_return_10d"] = self._basket_return(base_date, target_10d, sector=sector)
            if pd.notna(labels.get("future_return_10d")) and pd.notna(labels.get("market_return_10d")):
                labels["outperform_market_10d"] = bool(labels["future_return_10d"] > labels["market_return_10d"])
            if pd.notna(labels.get("future_return_10d")) and pd.notna(labels.get("sector_return_10d")):
                labels["outperform_sector_10d"] = bool(labels["future_return_10d"] > labels["sector_return_10d"])

        min_3d = labels.get("future_max_drawdown_10d")
        if base_pos + 3 < len(self.trading_dates):
            dates_3 = self.trading_dates[base_pos + 1 : base_pos + 4]
            f3 = g.loc[g.index.intersection(dates_3)]
            if not f3.empty:
                draw3 = float(f3["low"].min() / base_close - 1.0)
                ret3 = labels.get("future_return_3d")
                labels["exit_within_3d"] = bool(
                    draw3 <= self.config.quick_failure_return_3d
                    or (pd.notna(ret3) and ret3 <= self.config.quick_failure_return_3d)
                )
        return labels

    def _basket_return(self, base_date, target_date, sector: str | None) -> float:
        base = self.price_df.loc[self.price_df["date"] == base_date, ["code", "close", "sector"]].rename(columns={"close": "base_close"})
        target = self.price_df.loc[self.price_df["date"] == target_date, ["code", "close"]].rename(columns={"close": "target_close"})
        merged = base.merge(target, on="code", how="inner")
        if sector is not None:
            merged = merged.loc[merged["sector"].astype(str) == str(sector)]
        merged = merged.loc[(merged["base_close"] > 0) & merged["target_close"].notna()]
        if merged.empty:
            return np.nan
        return float((merged["target_close"] / merged["base_close"] - 1.0).mean())

    def _assign_auto_label(self, out: pd.DataFrame) -> pd.DataFrame:
        out = out.copy()
        out["auto_label"] = "neutral_entry"
        incomplete = out["label_status"].fillna("").astype(str) != "ok"
        good = (
            (out["future_return_10d"] >= self.config.good_return_10d)
            & (out["future_max_drawdown_10d"] > self.config.bad_drawdown_10d)
            & (out["outperform_market_10d"].astype(bool) | out["outperform_sector_10d"].astype(bool))
        )
        bad = (
            (out["future_return_10d"] <= self.config.bad_return_10d)
            | (out["future_max_drawdown_10d"] <= self.config.bad_drawdown_10d)
            | out["exit_within_3d"].astype(bool)
        )
        out.loc[good, "auto_label"] = "good_entry"
        out.loc[bad, "auto_label"] = "bad_entry"
        out.loc[incomplete, "auto_label"] = "unlabeled"
        return out
