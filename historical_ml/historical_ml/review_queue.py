from __future__ import annotations

import pandas as pd

from .config import HistoricalMLConfig
from .io_utils import reorder_columns
from .schemas import REVIEW_QUEUE_COLUMNS


def build_manual_review_queue(labeled_samples: pd.DataFrame, config: HistoricalMLConfig = HistoricalMLConfig()) -> pd.DataFrame:
    """Select only high-value samples for human review."""

    if labeled_samples.empty:
        return pd.DataFrame(columns=REVIEW_QUEUE_COLUMNS)

    df = labeled_samples.copy()
    exclude = df.get("exclude_reason", pd.Series("", index=df.index)).fillna("").astype(str)
    reasons: list[tuple[str, pd.Series, int]] = [
        (
            "large_loss_entry",
            df.get("was_bought", False).astype(bool)
            & ((df["future_return_10d"] <= config.bad_return_10d) | (df["future_max_drawdown_10d"] <= config.bad_drawdown_10d)),
            95,
        ),
        (
            "quick_failure_entry",
            df.get("was_bought", False).astype(bool)
            & ((df["future_return_3d"] <= config.quick_failure_return_3d) | df["exit_within_3d"].astype(bool)),
            90,
        ),
        (
            "bought_and_knocked_out",
            df.get("was_bought", False).astype(bool) & df["exit_within_3d"].astype(bool),
            88,
        ),
        (
            "missed_big_winner",
            (~df.get("was_bought", False).astype(bool)) & (df["future_return_10d"] >= config.missed_big_winner_return_10d),
            85,
        ),
        (
            "top_rank_filtered",
            (~df.get("was_candidate", False).astype(bool)) & (df.get("global_rank", 9999) <= config.candidate_top_n_per_sector * 2),
            78,
        ),
        (
            "same_sector_skipped",
            exclude.str.contains("same_sector", case=False),
            72,
        ),
        (
            "data_abnormal",
            exclude.str.contains("data_abnormal|missing_data|bad_price|insufficient_history", case=False),
            70,
        ),
    ]

    frames = []
    for reason, mask, priority in reasons:
        part = df.loc[mask].copy()
        if part.empty:
            continue
        part["review_reason"] = reason
        part["review_priority"] = priority
        frames.append(part)

    if not frames:
        return pd.DataFrame(columns=REVIEW_QUEUE_COLUMNS)

    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["review_priority", "trade_date", "entry_score"], ascending=[False, True, False])
    return reorder_columns(out, REVIEW_QUEUE_COLUMNS)
