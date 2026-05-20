import pandas as pd

from historical_ml.config import HistoricalMLConfig
from historical_ml.replay_engine import HistoricalReplayEngine
from historical_ml.schemas import ENTRY_CANDIDATE_COLUMNS
from historical_ml.validators import assert_required_columns, assert_signal_execution_separation
from historical_ml.tests.test_helpers import make_price_data


def test_replay_outputs_required_columns_and_separates_execution_date():
    prices = make_price_data(days=45)
    config = HistoricalMLConfig(min_history_days=5, replay_start=pd.Timestamp("2024-10-01").date(), replay_end=pd.Timestamp("2024-10-15").date())
    outputs = HistoricalReplayEngine(prices, config=config).run(config.replay_start, config.replay_end)
    samples = outputs["entry_candidate_samples"]
    assert not samples.empty
    assert_required_columns(samples, ENTRY_CANDIDATE_COLUMNS, "entry_candidate_samples")
    assert_signal_execution_separation(samples)
    assert set(samples["source"]) == {"historical_replay"}
