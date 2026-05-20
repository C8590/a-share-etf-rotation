import pandas as pd

from historical_ml.calibration import build_entry_calibration, generate_entry_calibration_outputs
from historical_ml.config import HistoricalMLConfig
from historical_ml.schemas import CALIBRATION_SUGGESTION_COLUMNS


def _row(i, **overrides):
    row = {
        "trade_date": pd.Timestamp("2024-10-01") + pd.Timedelta(days=i),
        "signal_date": pd.Timestamp("2024-10-01") + pd.Timedelta(days=i),
        "execution_date": pd.Timestamp("2024-10-02") + pd.Timedelta(days=i),
        "code": f"{i:06d}",
        "name": f"ETF{i}",
        "sector": "tech",
        "sector_l1": "core",
        "market_state": "offense",
        "sector_state": "strong",
        "momentum_score": 1.0,
        "acceleration_score": 0.1,
        "entry_score": 1.0,
        "trend_maturity": 0.3,
        "sector_rank": 1,
        "etf_rank": 1,
        "global_rank": i + 1,
        "was_candidate": True,
        "was_selected": i % 2 == 0,
        "was_bought": i % 4 == 0,
        "exclude_reason": "selected",
        "source": "historical_replay",
        "label_status": "ok",
        "auto_label": "neutral_entry",
        "future_return_10d": 0.0,
        "future_max_drawdown_10d": -0.01,
    }
    row.update(overrides)
    return row


def _config():
    return HistoricalMLConfig(min_group_size_for_report=1)


def test_calibration_report_handles_tiny_sample():
    df = pd.DataFrame([_row(0, auto_label="bad_entry", future_return_10d=-0.04)])

    suggestions, report = build_entry_calibration(df, config=_config())

    assert list(suggestions.columns) == CALIBRATION_SUGGESTION_COLUMNS
    assert "entry_calibration_report" in report


def test_calibration_detects_low_momentum_high_bad_rate():
    rows = []
    for i in range(8):
        rows.append(_row(i, momentum_score=-1.0, auto_label="bad_entry", future_return_10d=-0.05))
    for i in range(8, 16):
        rows.append(_row(i, momentum_score=1.0, auto_label="good_entry", future_return_10d=0.07))

    suggestions, _ = build_entry_calibration(pd.DataFrame(rows), config=_config())

    assert "momentum_score" in set(suggestions["parameter_area"])


def test_calibration_detects_overheat_high_bad_rate():
    rows = []
    for i in range(5):
        rows.append(_row(i, trend_maturity=0.9, auto_label="bad_entry", future_return_10d=-0.04))
    for i in range(5, 10):
        rows.append(_row(i, trend_maturity=0.2, auto_label="good_entry", future_return_10d=0.06))

    suggestions, _ = build_entry_calibration(pd.DataFrame(rows), config=_config())

    assert "trend_maturity" in set(suggestions["parameter_area"])


def test_calibration_detects_defense_market_state_high_bad_rate():
    rows = []
    for i in range(5):
        rows.append(_row(i, market_state="defense", auto_label="bad_entry", future_return_10d=-0.04))
    for i in range(5, 10):
        rows.append(_row(i, market_state="offense", auto_label="good_entry", future_return_10d=0.06))

    suggestions, _ = build_entry_calibration(pd.DataFrame(rows), config=_config())

    market = suggestions.loc[suggestions["parameter_area"] == "market_state"]
    assert not market.empty
    assert set(market["affected_market_state"]) == {"defense"}


def test_calibration_outputs_suggestions_csv_fields(tmp_path):
    rows = []
    for i in range(5):
        rows.append(_row(i, momentum_score=-1.0, auto_label="bad_entry", future_return_10d=-0.05))
    for i in range(5, 10):
        rows.append(_row(i, momentum_score=1.0, auto_label="good_entry", future_return_10d=0.07))

    suggestions, report = generate_entry_calibration_outputs(pd.DataFrame(rows), tmp_path, config=_config())
    written = pd.read_csv(tmp_path / "entry_calibration_suggestions.csv")

    assert list(suggestions.columns) == CALIBRATION_SUGGESTION_COLUMNS
    assert list(written.columns) == CALIBRATION_SUGGESTION_COLUMNS
    assert (tmp_path / "entry_calibration_report.md").exists()
    assert "Structured Suggestions" in report
