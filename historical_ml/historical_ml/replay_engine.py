from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import HistoricalMLConfig
from .entry_adapter import EntryAdapter, RealEntryAdapter
from .feature_builder import build_etf_features_for_day
from .io_utils import reorder_columns, write_daily_partition, write_table
from .schemas import (
    DAILY_DECISION_SNAPSHOT_COLUMNS,
    DAILY_ETF_SAMPLE_COLUMNS,
    DAILY_SECTOR_SAMPLE_COLUMNS,
    ENTRY_CANDIDATE_COLUMNS,
)


@dataclass
class HistoricalReplayEngine:
    """Replay ETF history day by day and produce feature/candidate samples."""

    price_df: pd.DataFrame
    config: HistoricalMLConfig = HistoricalMLConfig()
    entry_adapter: Optional[EntryAdapter] = None

    def __post_init__(self):
        self.price_df = self.price_df.copy()
        self.price_df["date"] = pd.to_datetime(self.price_df["date"]).dt.normalize()
        self.price_df["code"] = self.price_df["code"].astype(str)
        self.price_df = self.price_df.sort_values(["date", "code"]).reset_index(drop=True)
        if self.entry_adapter is None:
            self.entry_adapter = RealEntryAdapter()

    def _trading_dates(self, start, end) -> list[pd.Timestamp]:
        start = pd.Timestamp(start).normalize()
        end = pd.Timestamp(end).normalize()
        dates = sorted(pd.to_datetime(self.price_df["date"].unique()))
        return [d for d in dates if start <= d <= end]

    def _next_trading_date(self, trade_date) -> pd.Timestamp | pd.NaT:
        dates = sorted(pd.to_datetime(self.price_df["date"].unique()))
        trade_date = pd.Timestamp(trade_date).normalize()
        for d in dates:
            if d > trade_date:
                return d
        return pd.NaT

    def run(self, start=None, end=None, out_dir: str | Path | None = None) -> dict[str, pd.DataFrame]:
        start = start or self.config.replay_start
        end = end or self.config.replay_end
        dates = self._trading_dates(start, end)
        if not dates:
            raise ValueError(f"no trading dates found between {start} and {end}")

        all_etf = []
        all_sector = []
        all_snapshots = []
        all_candidates = []

        for trade_date in dates:
            etf_samples, sector_samples = build_etf_features_for_day(self.price_df, trade_date, self.config)
            if etf_samples.empty:
                continue

            execution_date = self._next_trading_date(trade_date)
            candidates = self.entry_adapter.build_entry_candidates(
                etf_samples=etf_samples,
                sector_samples=sector_samples,
                signal_date=trade_date,
                execution_date=execution_date,
                config=self.config,
            )

            snapshot = self._build_snapshot(trade_date, execution_date, etf_samples, sector_samples, candidates)
            etf_out = reorder_columns(etf_samples, DAILY_ETF_SAMPLE_COLUMNS)
            sector_out = reorder_columns(sector_samples, DAILY_SECTOR_SAMPLE_COLUMNS)
            cand_out = reorder_columns(candidates, ENTRY_CANDIDATE_COLUMNS)

            all_etf.append(etf_out)
            all_sector.append(sector_out)
            all_snapshots.append(snapshot)
            all_candidates.append(cand_out)

            if out_dir and self.config.write_daily_partitions:
                write_daily_partition(etf_out, out_dir, "daily_etf_samples", trade_date, self.config.output_format)
                write_daily_partition(sector_out, out_dir, "daily_sector_samples", trade_date, self.config.output_format)
                write_daily_partition(snapshot, out_dir, "daily_decision_snapshot", trade_date, self.config.output_format)
                write_daily_partition(cand_out, out_dir, "entry_candidate_samples", trade_date, self.config.output_format)

        outputs = {
            "daily_etf_samples": pd.concat(all_etf, ignore_index=True) if all_etf else pd.DataFrame(),
            "daily_sector_samples": pd.concat(all_sector, ignore_index=True) if all_sector else pd.DataFrame(),
            "daily_decision_snapshot": pd.concat(all_snapshots, ignore_index=True) if all_snapshots else pd.DataFrame(),
            "entry_candidate_samples": pd.concat(all_candidates, ignore_index=True) if all_candidates else pd.DataFrame(),
        }

        if out_dir:
            write_table(outputs["daily_etf_samples"], out_dir, "daily_etf_samples", self.config.output_format)
            write_table(outputs["daily_sector_samples"], out_dir, "daily_sector_samples", self.config.output_format)
            write_table(outputs["daily_decision_snapshot"], out_dir, "daily_decision_snapshot", self.config.output_format)
            write_table(outputs["entry_candidate_samples"], out_dir, "entry_candidate_samples_unlabeled", self.config.output_format)

        return outputs

    def _build_snapshot(self, trade_date, execution_date, etf_samples, sector_samples, candidates) -> pd.DataFrame:
        trade_date = pd.Timestamp(trade_date).normalize()
        market_state = str(etf_samples["market_state"].iloc[0]) if not etf_samples.empty else "unknown"
        exclude = candidates.get("exclude_reason", pd.Series(dtype=str)).fillna("").astype(str)
        snapshot = pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "signal_date": trade_date,
                    "execution_date": execution_date,
                    "market_state": market_state,
                    "etf_count": int(len(etf_samples)),
                    "sector_count": int(len(sector_samples)),
                    "candidate_count": int(candidates.get("was_candidate", pd.Series(dtype=bool)).sum()),
                    "selected_count": int(candidates.get("was_selected", pd.Series(dtype=bool)).sum()),
                    "bought_count": int(candidates.get("was_bought", pd.Series(dtype=bool)).sum()),
                    "defense_block_count": int(exclude.str.contains("defense", case=False).sum()),
                    "filtered_count": int((~candidates.get("was_candidate", pd.Series(dtype=bool))).sum()),
                    "data_abnormal_count": int(exclude.str.contains("data_abnormal", case=False).sum()),
                    "source": self.config.source,
                }
            ]
        )
        return reorder_columns(snapshot, DAILY_DECISION_SNAPSHOT_COLUMNS)
