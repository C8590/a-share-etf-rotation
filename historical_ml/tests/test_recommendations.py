from __future__ import annotations

import pandas as pd

from historical_ml.cli import main
from historical_ml.recommendations import (
    RECOMMENDATION_COLUMNS,
    build_entry_rule_review_recommendations,
    make_recommendations_from_artifacts,
)


def _stability_report() -> str:
    return """# ml_stability_report

## Rolling Time Validation

| split | sample_count | high_risk_lift |
|---|---:|---:|
| 2025Q3 | 100 | 2.10 |
| 2025Q4 | 120 | 1.80 |
| 2026YTD | 110 | 1.40 |

## Market State Validation

| market_state | sample_count | high_risk_lift | status |
|---|---:|---:|---|
| offense | 80 | 1.60 | ok |
| defense | 35 | 0.00 | model_fails |

## Sector L2 Validation

| sector_l2 | sample_count | high_risk_lift | status |
|---|---:|---:|---|
| tech_growth | 90 | 1.70 | ok |
| gold_goods | 40 | 0.90 | model_fails |

## Label Policy Sensitivity

| label_policy | sample_count | high_risk_lift |
|---|---:|---:|
| strict | 100 | 1.50 |
| default | 110 | 1.40 |
| loose | 105 | 1.30 |
"""


def _risk_scores() -> pd.DataFrame:
    rows = []
    for i in range(50):
        rows.append(
            {
                "trade_date": "2026-01-01",
                "code": f"{510000 + i:06d}",
                "name": f"ETF{i}",
                "sector_l1": "theme",
                "sector_l2": "tech_growth",
                "market_state": "offense",
                "trend_maturity": 0.4,
                "entry_score": 0.5,
                "bad_entry_risk_score": i / 50,
                "bad_entry_risk_bucket": "high" if i >= 40 else ("medium" if i >= 20 else "low"),
                "auto_label": "bad_entry" if i >= 40 or i % 7 == 0 else "neutral_entry",
                "label_status": "ok",
                "was_selected": i % 3 == 0,
                "was_bought": i % 10 == 0,
            }
        )
    return pd.DataFrame(rows)


def _calibration() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "suggestion_id": "CAL-001",
                "parameter_area": "trend_maturity",
                "current_pattern": "overheat bad_rate elevated",
                "evidence_metric": "overheat_bad_rate_delta",
                "evidence_value": "0.30",
                "suggested_action": "review overheat penalty manually",
                "confidence": "high",
                "affected_market_state": "all",
                "affected_sector_state": "all",
                "sample_count": 60,
                "good_rate": 0.1,
                "bad_rate": 0.5,
                "avg_future_return_10d": -0.03,
                "max_drawdown_warning": -0.08,
                "notes": "",
            }
        ]
    )


def test_defense_model_fails_enters_do_not_adopt_yet():
    df = build_entry_rule_review_recommendations({"ml_stability_report": _stability_report(), "ml_entry_risk_scores": _risk_scores()})

    row = df.loc[df["title"].str.contains("defense", case=False)].iloc[0]
    assert row["grade"] == "do_not_adopt_yet"
    assert "model_fails" in row["evidence_value"]


def test_stable_lift_over_one_enters_review_bucket():
    df = build_entry_rule_review_recommendations({"ml_stability_report": _stability_report(), "ml_entry_risk_scores": _risk_scores()})

    row = df.loc[df["title"].eq("Overall bad_entry risk stratification")].iloc[0]
    assert row["grade"] in {"recommend_adopt", "recommend_observe"}
    assert "rolling_lifts" in row["evidence_value"]


def test_automatic_actions_are_forbidden_auto_apply():
    df = build_entry_rule_review_recommendations({"ml_stability_report": _stability_report()})

    forbidden = df.loc[df["grade"].eq("forbidden_auto_apply")]
    assert not forbidden.empty
    assert forbidden["recommendation"].str.contains("automatically|realtime|QMT", case=False, regex=True).any()


def test_missing_inputs_report_degrades_with_warning(tmp_path):
    result = make_recommendations_from_artifacts(tmp_path / "missing_artifacts", tmp_path)

    assert (tmp_path / "entry_rule_review_recommendations.md").exists()
    assert (tmp_path / "entry_rule_review_recommendations.csv").exists()
    assert result.warnings
    assert "missing optional input" in result.report


def test_recommendation_csv_fields_complete(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _calibration().to_csv(artifacts / "entry_calibration_suggestions.csv", index=False)
    _risk_scores().to_csv(artifacts / "ml_entry_risk_scores.csv", index=False)
    (artifacts / "ml_stability_report.md").write_text(_stability_report(), encoding="utf-8")

    result = make_recommendations_from_artifacts(artifacts, tmp_path)
    written = pd.read_csv(tmp_path / "entry_rule_review_recommendations.csv")

    assert list(written.columns) == RECOMMENDATION_COLUMNS
    assert list(result.recommendations.columns) == RECOMMENDATION_COLUMNS


def test_cli_make_recommendations_runs(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _risk_scores().to_csv(artifacts / "ml_entry_risk_scores.csv", index=False)
    (artifacts / "ml_stability_report.md").write_text(_stability_report(), encoding="utf-8")

    rc = main(["make-recommendations", "--artifacts", str(artifacts), "--out", str(tmp_path)])

    assert rc == 0
    assert (tmp_path / "entry_rule_review_recommendations.md").exists()
    assert (tmp_path / "entry_rule_review_recommendations.csv").exists()
