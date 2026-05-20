from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Tuple


@dataclass(frozen=True)
class HistoricalMLConfig:
    """Configuration for historical replay sample production.

    These defaults match the requested historical window and are deliberately
    conservative.  They should be tuned from reports, not by changing the entry
    department's live rules directly.
    """

    replay_start: date = date(2024, 9, 24)
    replay_end: date = date(2026, 5, 19)

    momentum_windows: Tuple[int, int, int] = (20, 60, 120)
    momentum_weights: Tuple[float, float, float] = (0.30, 0.50, 0.20)
    acceleration_lag: int = 5

    # Candidate sampling controls.  These are sample-production defaults, not
    # live trading recommendations.
    selected_sector_count: int = 3
    candidate_top_n_per_sector: int = 5
    max_selected_entries: int = 3
    min_entry_score: float = -999.0
    defense_allows_candidate: bool = True
    defense_allows_bought: bool = False

    # Liquidity / data-quality defaults.  Set to zero to disable in early dry runs.
    min_history_days: int = 20
    min_avg_amount_20d: float = 0.0
    max_missing_ratio_60d: float = 0.20

    # Label defaults.
    label_horizons: Tuple[int, int, int, int, int] = (1, 3, 5, 10, 20)
    good_return_10d: float = 0.04
    bad_return_10d: float = -0.03
    bad_drawdown_10d: float = -0.05
    quick_failure_return_3d: float = -0.025
    missed_big_winner_return_10d: float = 0.06

    # Optional benchmark.  If None or absent, the labeler uses equal-weight ETF market return.
    market_index_code: Optional[str] = None

    # Output settings.
    output_format: str = "csv"  # csv is dependency-light; parquet can be enabled if pyarrow is installed.
    write_daily_partitions: bool = False
    source: str = "historical_replay"

    # Report settings.
    min_group_size_for_report: int = 10
    report_feature_bins: int = 5

    metadata: dict = field(default_factory=dict)
