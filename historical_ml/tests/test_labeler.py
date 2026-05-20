import pandas as pd

from historical_ml.config import HistoricalMLConfig
from historical_ml.labeler import FutureLabeler
from historical_ml.replay_engine import HistoricalReplayEngine
from historical_ml.tests.test_helpers import make_price_data


def test_labeler_attaches_future_labels_from_execution_date():
    prices = make_price_data(days=80)
    config = HistoricalMLConfig(min_history_days=5, replay_start=pd.Timestamp("2024-10-01").date(), replay_end=pd.Timestamp("2024-10-04").date())
    outputs = HistoricalReplayEngine(prices, config=config).run(config.replay_start, config.replay_end)
    labeled = FutureLabeler(prices, config=config).attach_labels(outputs["entry_candidate_samples"])
    assert "future_return_10d" in labeled.columns
    assert "auto_label" in labeled.columns
    assert labeled["label_base_date"].notna().all()
    assert set(labeled["label_status"]).issubset({"ok", "insufficient_future_data"})
