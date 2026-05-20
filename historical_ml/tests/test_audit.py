import pandas as pd
import pytest

from historical_ml.audit import generate_replay_audit_report
from historical_ml.config import HistoricalMLConfig
from historical_ml.labeler import FutureLabeler
from historical_ml.reports import generate_entry_threshold_report
from historical_ml.tests.test_helpers import make_price_data


def _candidate_rows(**overrides):
    row = {
        "trade_date": "2024-10-01",
        "signal_date": "2024-10-01",
        "execution_date": "2024-10-02",
        "code": "A",
        "name": "ETFA",
        "sector": "tech",
        "sector_l1": "tech",
        "market_state": "offense",
        "sector_state": "strong",
        "momentum_score": 1.0,
        "acceleration_score": 0.2,
        "entry_score": 1.1,
        "trend_maturity": 0.3,
        "sector_rank": 1,
        "etf_rank": 1,
        "global_rank": 1,
        "was_candidate": True,
        "was_selected": True,
        "was_bought": True,
        "exclude_reason": "entry_bought",
        "source": "historical_replay",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_audit_detects_future_return_in_unlabeled_samples():
    outputs = {"entry_candidate_samples_unlabeled": _candidate_rows(future_return_10d=0.1)}
    with pytest.raises(AssertionError, match="future label columns"):
        generate_replay_audit_report(outputs, pd.DataFrame(), config=HistoricalMLConfig())


def test_audit_detects_non_historical_replay_source():
    outputs = {"entry_candidate_samples_unlabeled": _candidate_rows(source="manual")}
    with pytest.raises(AssertionError, match="source must be historical_replay"):
        generate_replay_audit_report(outputs, pd.DataFrame(), config=HistoricalMLConfig())


def test_audit_detects_execution_date_not_after_signal_date():
    outputs = {"entry_candidate_samples_unlabeled": _candidate_rows(execution_date="2024-10-01")}
    with pytest.raises(AssertionError, match="execution_date must be after signal_date"):
        generate_replay_audit_report(outputs, pd.DataFrame(), config=HistoricalMLConfig())


def test_insufficient_future_data_rows_stay_unlabeled():
    prices = make_price_data(days=30)
    samples = _candidate_rows(trade_date="2024-10-10", signal_date="2024-10-10", execution_date="2024-10-11")

    labeled = FutureLabeler(prices, config=HistoricalMLConfig()).attach_labels(samples)

    assert set(labeled["label_status"]) == {"insufficient_future_data"}
    assert set(labeled["auto_label"]) == {"unlabeled"}


def test_entry_threshold_report_handles_tiny_sample():
    tiny = _candidate_rows()
    tiny["label_status"] = "ok"
    tiny["auto_label"] = "bad_entry"
    tiny["future_return_3d"] = -0.02
    tiny["future_return_10d"] = -0.05
    tiny["future_max_drawdown_10d"] = -0.08
    tiny["outperform_market_10d"] = False
    tiny["outperform_sector_10d"] = False
    tiny["exit_within_3d"] = True

    report = generate_entry_threshold_report(tiny, config=HistoricalMLConfig(min_group_size_for_report=10))

    assert "Phase 2 Quality Diagnostics" in report
    assert "was_bought=True and bad_entry Top 20" in report
