from __future__ import annotations

import pandas as pd

from historical_ml.cli import main
from historical_ml.ml_baseline import run_baseline
from historical_ml.ml_dataset import build_feature_frame, build_time_split, prepare_ml_samples


def _row(i: int, **overrides):
    trade_date = pd.Timestamp("2024-10-01") + pd.Timedelta(days=i * 20)
    row = {
        "trade_date": trade_date,
        "signal_date": trade_date,
        "execution_date": trade_date + pd.Timedelta(days=1),
        "code": f"{510000 + i:06d}",
        "name": f"ETF{i}",
        "sector": "科技成长" if i % 2 else "金融地产",
        "sector_l1": "行业主题",
        "sector_l2": "科技成长" if i % 2 else "金融地产",
        "theme": "人工智能" if i % 2 else "证券",
        "asset_class": "equity",
        "market_state": "offense" if i % 3 else "defense",
        "sector_state": "strong" if i % 2 else "neutral",
        "momentum_score": float(i % 7 - 3),
        "acceleration_score": float(i % 5) / 5,
        "entry_score": float(i % 10) / 10,
        "trend_maturity": 0.9 if i % 4 == 0 else 0.3,
        "sector_rank": i % 5 + 1,
        "etf_rank": i % 4 + 1,
        "was_candidate": True,
        "was_selected": i % 3 == 0,
        "was_bought": i % 6 == 0,
        "exclude_reason": "selected" if i % 3 == 0 else "entry_not_selected",
        "label_status": "ok",
        "auto_label": "bad_entry" if i % 4 == 0 else ("good_entry" if i % 5 == 0 else "neutral_entry"),
        "future_return_10d": -0.05 if i % 4 == 0 else 0.05,
        "future_max_gain_10d": 0.08,
        "future_max_drawdown_10d": -0.06 if i % 4 == 0 else -0.01,
        "outperform_market_10d": i % 5 == 0,
        "outperform_sector_10d": i % 5 == 0,
        "source": "historical_replay",
    }
    row.update(overrides)
    return row


def _sample_df(n: int = 40) -> pd.DataFrame:
    return pd.DataFrame([_row(i) for i in range(n)])


def test_forbidden_future_and_label_columns_do_not_enter_features():
    df = prepare_ml_samples(_sample_df())
    features = build_feature_frame(df, "behavior_augmented")

    names = features.feature_names
    assert not any(name.startswith("future_return_") for name in names)
    assert not any(name.startswith("future_max_") for name in names)
    assert not any(name.startswith("outperform_") for name in names)
    assert "auto_label" not in names
    assert "label_status" not in names
    assert "code" not in names
    assert "name" not in names


def test_time_split_is_chronological_not_random():
    df = prepare_ml_samples(_sample_df())
    split = build_time_split(df, min_test_rows=1)

    assert split.train_mask.any()
    assert split.test_mask.any()
    assert df.loc[split.train_mask, "trade_date"].max() < df.loc[split.test_mask, "trade_date"].min()
    assert "chronological" in split.note or "calendar" in split.note


def test_baseline_report_handles_small_sample(tmp_path):
    result = run_baseline(_sample_df(12), tmp_path)

    assert (tmp_path / "ml_baseline_report.md").exists()
    assert "ml_baseline_report" in result.report
    assert "not pre-trade alpha features" in result.report


def test_risk_bucket_generates_low_medium_high(tmp_path):
    result = run_baseline(_sample_df(60), tmp_path)

    assert {"low", "medium", "high"}.issubset(set(result.risk_scores["bad_entry_risk_bucket"]))
    assert (tmp_path / "ml_entry_risk_scores.csv").exists()


def test_behavior_features_are_marked_not_pretrade_alpha(tmp_path):
    result = run_baseline(_sample_df(30), tmp_path)

    assert "`was_selected` and `was_bought` are historical system behavior features" in result.report
    assert "not pre-trade alpha features" in result.report


def test_cli_train_baseline_runs_on_small_fixture(tmp_path):
    samples = tmp_path / "samples.csv"
    _sample_df(30).to_csv(samples, index=False)

    rc = main(["train-baseline", "--samples", str(samples), "--out", str(tmp_path), "--target", "both"])

    assert rc == 0
    assert (tmp_path / "ml_baseline_report.md").exists()
    assert (tmp_path / "ml_entry_risk_scores.csv").exists()
