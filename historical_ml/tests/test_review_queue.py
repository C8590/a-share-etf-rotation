import pandas as pd

from historical_ml.config import HistoricalMLConfig
from historical_ml.review_queue import build_manual_review_queue


def test_review_queue_picks_large_loss_and_missed_winner():
    df = pd.DataFrame(
        [
            {
                "trade_date": "2024-10-01",
                "execution_date": "2024-10-02",
                "code": "A",
                "name": "ETFA",
                "sector": "科技",
                "market_state": "offense",
                "sector_state": "strong",
                "momentum_score": 1.0,
                "acceleration_score": 0.5,
                "entry_score": 1.2,
                "trend_maturity": 0.3,
                "sector_rank": 1,
                "etf_rank": 1,
                "global_rank": 1,
                "was_candidate": True,
                "was_selected": True,
                "was_bought": True,
                "exclude_reason": "selected",
                "future_return_3d": -0.04,
                "future_return_10d": -0.06,
                "future_max_drawdown_10d": -0.08,
                "exit_within_3d": True,
                "auto_label": "bad_entry",
                "source": "historical_replay",
            },
            {
                "trade_date": "2024-10-01",
                "execution_date": "2024-10-02",
                "code": "B",
                "name": "ETFB",
                "sector": "消费",
                "market_state": "offense",
                "sector_state": "strong",
                "momentum_score": 0.8,
                "acceleration_score": 0.2,
                "entry_score": 0.9,
                "trend_maturity": 0.2,
                "sector_rank": 2,
                "etf_rank": 1,
                "global_rank": 2,
                "was_candidate": True,
                "was_selected": False,
                "was_bought": False,
                "exclude_reason": "portfolio_slot_limit",
                "future_return_3d": 0.03,
                "future_return_10d": 0.08,
                "future_max_drawdown_10d": -0.01,
                "exit_within_3d": False,
                "auto_label": "good_entry",
                "source": "historical_replay",
            },
        ]
    )
    q = build_manual_review_queue(df, HistoricalMLConfig())
    assert {"large_loss_entry", "quick_failure_entry", "bought_and_knocked_out", "missed_big_winner"}.issubset(set(q["review_reason"]))
