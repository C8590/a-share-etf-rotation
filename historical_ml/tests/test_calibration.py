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
        "outperform_market_10d": False,
        "outperform_sector_10d": False,
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


def test_true_missed_winner_requires_market_or_sector_outperformance():
    rows = [
        _row(0, was_bought=False, future_return_10d=0.08, auto_label="good_entry"),
        _row(
            1,
            was_bought=False,
            future_return_10d=0.09,
            auto_label="good_entry",
            outperform_market_10d=True,
        ),
    ]

    suggestions, report = build_entry_calibration(pd.DataFrame(rows), config=_config())

    missed = suggestions.loc[suggestions["parameter_area"] == "missed_winner"]
    assert not missed.empty
    assert set(missed["evidence_metric"]) == {"true_missed_winner_count"}
    assert "true_missed_winner" in report
    assert "| true_missed_winner" in report


def test_small_sample_does_not_emit_high_confidence():
    rows = []
    for i in range(5):
        rows.append(_row(i, momentum_score=-1.0, auto_label="bad_entry", future_return_10d=-0.05))
    for i in range(5, 10):
        rows.append(_row(i, momentum_score=1.0, auto_label="good_entry", future_return_10d=0.07))

    suggestions, _ = build_entry_calibration(pd.DataFrame(rows), config=_config())

    assert "high" not in set(suggestions["confidence"])


def test_concentrated_code_adds_warning_to_report():
    rows = []
    for i in range(40):
        rows.append(
            _row(
                i,
                code="159915",
                sector="tech",
                trend_maturity=0.9,
                auto_label="bad_entry",
                future_return_10d=-0.05,
            )
        )
    for i in range(40, 80):
        rows.append(
            _row(
                i,
                code=f"{i:06d}",
                sector="finance" if i % 2 else "consumer",
                trend_maturity=0.2,
                auto_label="good_entry",
                future_return_10d=0.06,
            )
        )

    _, report = build_entry_calibration(pd.DataFrame(rows), config=_config())

    assert "concentration warning" in report
    assert "top_code=159915" in report


def test_selected_bad_entry_attribution_splits_by_market_state():
    rows = []
    for i in range(4):
        rows.append(_row(i, was_selected=True, market_state="defense", auto_label="bad_entry", future_return_10d=-0.04))
    for i in range(4, 8):
        rows.append(_row(i, was_selected=True, market_state="offense", auto_label="bad_entry", future_return_10d=-0.04))

    _, report = build_entry_calibration(pd.DataFrame(rows), config=_config())

    assert "selected_bad_entry Attribution" in report
    assert "#### market_state" in report
    assert "defense" in report
    assert "offense" in report


def test_report_warns_when_sector_benchmark_is_limited():
    rows = []
    for i in range(4):
        rows.append(
            _row(
                i,
                code=f"{i:06d}",
                sector=f"ETF{i}",
                auto_label="good_entry",
                future_return_10d=0.08,
                outperform_market_10d=True,
                outperform_sector_10d=False,
            )
        )

    _, report = build_entry_calibration(pd.DataFrame(rows), config=_config())

    assert "sector_outperform has no true rows" in report
    assert "single ETF" in report


def test_report_splits_buy_error_and_missed_opportunity_sections():
    rows = [
        _row(0, review_reason="large_loss_entry", was_bought=True, auto_label="bad_entry", future_return_10d=-0.05),
        _row(
            1,
            review_reason="missed_big_winner",
            was_bought=False,
            was_candidate=True,
            auto_label="good_entry",
            future_return_10d=0.09,
            outperform_market_10d=True,
        ),
    ]

    _, report = build_entry_calibration(pd.DataFrame(rows), config=_config())

    assert "## A. 错误买入分析" in report
    assert "## B. 错过机会分析" in report
    assert "### 防错建议" in report
    assert "### 敢买建议" in report
    assert "当前报告主要基于失败类样本，未充分覆盖错过机会样本" in report
