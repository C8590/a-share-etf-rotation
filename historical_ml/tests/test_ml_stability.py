from __future__ import annotations

import pandas as pd

from historical_ml.cli import main
from historical_ml.ml_stability import (
    derive_label_policy,
    grouped_stability,
    rolling_split_diagnostics,
    run_ml_stability,
)
from historical_ml.ml_dataset import prepare_ml_samples


def _row(i: int, **overrides):
    trade_date = pd.Timestamp("2024-10-01") + pd.Timedelta(days=i * 18)
    row = {
        "trade_date": trade_date,
        "signal_date": trade_date,
        "execution_date": trade_date + pd.Timedelta(days=1),
        "code": f"{510000 + i:06d}",
        "name": f"ETF{i}",
        "sector": "tech" if i % 2 else "finance",
        "sector_l1": "theme",
        "sector_l2": "tech" if i % 2 else "finance",
        "theme": "ai" if i % 2 else "broker",
        "asset_class": "equity",
        "market_state": "offense" if i % 3 else "defense",
        "sector_state": "strong" if i % 2 else "neutral",
        "momentum_score": float(i % 7 - 3),
        "acceleration_score": float(i % 5) / 5,
        "entry_score": float(i % 10) / 10,
        "trend_maturity": 0.85 if i % 4 == 0 else 0.35,
        "sector_rank": i % 5 + 1,
        "etf_rank": i % 4 + 1,
        "was_candidate": True,
        "was_selected": i % 3 == 0,
        "was_bought": i % 6 == 0,
        "exclude_reason": "selected" if i % 3 == 0 else "entry_not_selected",
        "label_status": "ok",
        "auto_label": "bad_entry" if i % 4 == 0 else ("good_entry" if i % 5 == 0 else "neutral_entry"),
        "future_return_10d": -0.05 if i % 4 == 0 else (0.07 if i % 5 == 0 else 0.01),
        "future_max_gain_10d": 0.08,
        "future_max_drawdown_10d": -0.06 if i % 4 == 0 else -0.01,
        "outperform_market_10d": i % 5 == 0,
        "outperform_sector_10d": i % 5 == 0,
        "source": "historical_replay",
    }
    row.update(overrides)
    return row


def _sample_df(n: int = 70) -> pd.DataFrame:
    return pd.DataFrame([_row(i) for i in range(n)])


def test_rolling_split_is_chronological_not_random():
    df = prepare_ml_samples(_sample_df(70))

    rolling = rolling_split_diagnostics(df)

    assert list(rolling["train_end"]) == ["2025-06-30", "2025-09-30", "2025-12-31"]
    assert list(rolling["test_start"]) == ["2025-07-01", "2025-10-01", "2026-01-01"]
    assert set(rolling["sample_count"].ge(0)) == {True}


def test_market_state_group_small_sample_does_not_crash():
    df = prepare_ml_samples(_sample_df(10))
    df["bad_entry_risk_score"] = [i / 10 for i in range(len(df))]
    df["bad_entry_risk_bucket"] = ["high" if i >= 8 else "low" for i in range(len(df))]

    grouped = grouped_stability(df, "market_state")

    assert not grouped.empty
    assert "small_sample" in set(grouped["status"])


def test_sector_l2_group_small_sample_does_not_crash():
    df = prepare_ml_samples(_sample_df(10))
    df["bad_entry_risk_score"] = [i / 10 for i in range(len(df))]
    df["bad_entry_risk_bucket"] = ["high" if i >= 8 else "low" for i in range(len(df))]

    grouped = grouped_stability(df, "sector_l2")

    assert not grouped.empty
    assert "high_risk_lift" in grouped.columns


def test_label_policy_derivation_does_not_mutate_original_samples():
    original = _sample_df(12)
    before = original["auto_label"].copy()

    derived = derive_label_policy(original, "strict")

    assert original["auto_label"].equals(before)
    assert derived is not original
    assert "auto_label" in derived.columns


def test_ml_stability_report_generates(tmp_path):
    result = run_ml_stability(_sample_df(70), tmp_path)

    assert (tmp_path / "ml_stability_report.md").exists()
    assert "ml_stability_report" in result.report
    assert "Rolling Time Validation" in result.report


def test_cli_ml_stability_runs_on_small_fixture(tmp_path):
    samples = tmp_path / "samples.csv"
    _sample_df(70).to_csv(samples, index=False)

    rc = main(["ml-stability", "--samples", str(samples), "--out", str(tmp_path)])

    assert rc == 0
    assert (tmp_path / "ml_stability_report.md").exists()
