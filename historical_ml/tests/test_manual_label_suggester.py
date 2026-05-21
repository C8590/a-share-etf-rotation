from __future__ import annotations

import pandas as pd

from historical_ml.manual_label_suggester import (
    adopt_high_confidence_suggestions,
    low_confidence_review_rows,
    pending_review_rows,
    suggest_manual_labels,
)


def _row(**overrides):
    row = {
        "review_reason": "large_loss_entry",
        "auto_label": "bad_entry",
        "was_candidate": True,
        "was_bought": True,
        "exit_within_3d": False,
        "future_return_10d": -0.05,
        "future_max_drawdown_10d": -0.08,
        "trend_maturity": 0.4,
        "overheat_score": 0.1,
        "market_state": "offense",
        "sector_state": "strong",
        "risk_score": 0.1,
        "sector_score": 0.5,
        "data_quality_flag": "ok",
        "missing_ratio_60d": 0.0,
    }
    row.update(overrides)
    return row


def test_large_loss_bad_entry_gets_high_confidence_valid_bad_entry():
    out, summary = suggest_manual_labels(pd.DataFrame([_row(overheat_score=0.4)]))

    assert out.iloc[0]["suggested_manual_label"] == "valid_bad_entry"
    assert out.iloc[0]["suggested_confidence"] == "high"
    assert "趋势过热" in out.iloc[0]["suggested_failure_reason"]
    assert summary["high_confidence_rows"] == 1


def test_quick_failure_prioritizes_false_breakout_reason():
    out, _ = suggest_manual_labels(pd.DataFrame([_row(review_reason="quick_failure_entry", exit_within_3d=True)]))

    assert out.iloc[0]["suggested_manual_label"] == "valid_bad_entry"
    assert "假突破" in out.iloc[0]["suggested_failure_reason"]
    assert bool(out.iloc[0]["need_human_review"]) is False


def test_missed_winner_candidate_not_bought_gets_medium_prefill():
    out, _ = suggest_manual_labels(
        pd.DataFrame([_row(review_reason="missed_big_winner", auto_label="good_entry", was_candidate=True, was_bought=False)])
    )

    assert out.iloc[0]["suggested_manual_label"] == "valid_missed_opportunity"
    assert "entry过于保守" in out.iloc[0]["suggested_failure_reason"]
    assert out.iloc[0]["suggested_confidence"] == "medium"


def test_data_issue_excludes_from_training():
    out, _ = suggest_manual_labels(pd.DataFrame([_row(data_quality_flag="missing_data")]))

    assert out.iloc[0]["suggested_manual_label"] == "data_issue"
    assert out.iloc[0]["suggested_action"] == "不纳入训练"
    assert out.iloc[0]["suggested_confidence"] == "high"


def test_low_confidence_rows_are_human_review_subset():
    prefilled, _ = suggest_manual_labels(pd.DataFrame([_row(), _row(review_reason="unknown_review_reason", auto_label="neutral_entry")]))

    low, summary = low_confidence_review_rows(prefilled)

    assert summary["low_confidence_rows"] == 1
    assert len(low) == 1
    assert bool(low.iloc[0]["need_human_review"]) is True


def test_adopt_high_confidence_fills_manual_columns():
    prefilled, _ = suggest_manual_labels(pd.DataFrame([_row(), _row(review_reason="missed_big_winner", auto_label="good_entry", was_bought=False)]))

    adopted, summary = adopt_high_confidence_suggestions(prefilled)

    assert summary["adopted_rows"] == 1
    assert summary["adopted_missed_winner_rows"] == 0
    assert summary["pending_missed_winner_rows"] == 1
    assert "敢买类样本覆盖不足" in summary["manual_label_balance_warning"]
    assert adopted.iloc[0]["manual_label"] == "valid_bad_entry"
    assert adopted.iloc[1]["manual_label"] == ""


def test_adopt_medium_confidence_can_accept_missed_winner():
    prefilled, _ = suggest_manual_labels(pd.DataFrame([_row(review_reason="missed_big_winner", auto_label="good_entry", was_candidate=True, was_bought=False)]))

    adopted, summary = adopt_high_confidence_suggestions(prefilled, min_confidence="medium")

    assert summary["adopted_rows"] == 1
    assert summary["adopted_missed_winner_rows"] == 1
    assert adopted.iloc[0]["manual_label"] == "valid_missed_opportunity"


def test_pending_review_rows_include_pending_missed_winner():
    prefilled, _ = suggest_manual_labels(
        pd.DataFrame([_row(), _row(review_reason="missed_big_winner", auto_label="good_entry", was_candidate=True, was_bought=False)])
    )
    adopted, _ = adopt_high_confidence_suggestions(prefilled)

    pending, summary = pending_review_rows(adopted)

    assert summary["pending_missed_winner_rows"] == 1
    assert len(pending) == 1
    assert pending.iloc[0]["review_reason"] == "missed_big_winner"
