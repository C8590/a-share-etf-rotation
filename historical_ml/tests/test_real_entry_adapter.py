import pandas as pd

from historical_ml.config import HistoricalMLConfig
from historical_ml.entry_adapter import RealEntryAdapter
from historical_ml.labeler import FutureLabeler
from historical_ml.replay_engine import HistoricalReplayEngine
from historical_ml.schemas import FUTURE_LABEL_COLUMNS
from historical_ml.tests.test_helpers import make_price_data


def test_real_entry_adapter_calls_entry_engine_without_future_labels_in_replay():
    prices = make_price_data(days=80)
    config = HistoricalMLConfig(
        min_history_days=5,
        replay_start=pd.Timestamp("2024-10-01").date(),
        replay_end=pd.Timestamp("2024-10-04").date(),
    )

    outputs = HistoricalReplayEngine(prices, config=config, entry_adapter=RealEntryAdapter()).run(
        config.replay_start,
        config.replay_end,
    )
    samples = outputs["entry_candidate_samples"]

    assert not samples.empty
    assert samples["was_candidate"].astype(bool).all()
    assert samples["exclude_reason"].str.contains("entry_action:").any()
    assert not set(FUTURE_LABEL_COLUMNS).intersection(samples.columns)

    labeled = FutureLabeler(prices, config=config).attach_labels(samples)
    assert set(FUTURE_LABEL_COLUMNS).issubset(labeled.columns)
