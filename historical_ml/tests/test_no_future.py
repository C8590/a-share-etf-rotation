from historical_ml.config import HistoricalMLConfig
from historical_ml.validators import assert_no_future_feature_leakage
from historical_ml.tests.test_helpers import make_price_data


def test_feature_builder_does_not_change_when_future_prices_are_perturbed():
    prices = make_price_data(days=100)
    config = HistoricalMLConfig(min_history_days=5)
    assert_no_future_feature_leakage(prices, "2024-11-15", config)
