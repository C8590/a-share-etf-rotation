from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def build_ml_baseline_report(df: pd.DataFrame, split: Any, results: list[Any], risk_scores: pd.DataFrame) -> str:
    lines: list[str] = []
    lines.append("# ml_baseline_report")
    lines.append("")
    lines.append("This is an offline diagnostic report. It does not generate live trading advice and does not write back entry parameters.")
    lines.append("")
    lines.append("## Data And Split")
    lines.append("")
    lines.append(f"- samples_used: {len(df):,}")
    lines.append(f"- label_status: ok only")
    lines.append(f"- split: {split.train_start} to {split.train_end} train; {split.test_start} to {split.test_end} test")
    lines.append(f"- split_note: {split.note}")
    lines.append("- model_backend: lightweight numpy fallback because sklearn is not installed")
    lines.append("")
    lines.append("## Feature Sets")
    lines.append("")
    lines.append("- `pre_trade_features_only`: excludes `was_selected`, `was_bought`, and `exclude_reason`; use this for cleaner pre-trade diagnostics.")
    lines.append("- `behavior_augmented`: includes historical behavior features `was_candidate`, `was_selected`, `was_bought`, and `exclude_reason` to explain replay behavior failures.")
    lines.append("- `was_selected` and `was_bought` are historical system behavior features, not pre-trade alpha features.")
    lines.append("- Forbidden inputs are excluded from training: `future_return_*`, `future_max_*`, `outperform_*`, `auto_label`, `label_status`, `code`, and `name`.")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append(_metrics_table(results))
    lines.append("")
    lines.append("## Feature Importance")
    for result in results:
        lines.append("")
        lines.append(f"### {result.feature_set} / {result.target_name} / {result.model_name}")
        lines.append(result.importance.head(20).to_markdown(index=False))
    lines.append("")
    lines.append("## Risk Buckets")
    lines.append("")
    lines.append(_risk_bucket_table(risk_scores))
    lines.append("")
    lines.append(f"- selected_high_risk_share: {_high_risk_share(risk_scores, 'was_selected')}")
    lines.append(f"- bought_high_risk_share: {_high_risk_share(risk_scores, 'was_bought')}")
    lines.append("")
    lines.append("## Market State Diagnostics")
    lines.append("")
    lines.append(_group_risk_table(risk_scores, "market_state"))
    lines.append("")
    lines.append("## Trend Maturity Diagnostics")
    lines.append("")
    lines.append(_trend_table(risk_scores))
    lines.append("")
    lines.append("## Sector L2 Diagnostics")
    lines.append("")
    lines.append(_group_risk_table(risk_scores, "sector_l2"))
    lines.append("")
    lines.append("## Diagnostic Answers")
    lines.append("")
    lines.extend(_diagnostic_answers(df, results, risk_scores))
    lines.append("")
    lines.append("## Manual Suggestions For Entry Department")
    lines.append("")
    lines.append("- Use high bad-risk buckets as a review queue for parameter calibration, not as direct buy/sell instructions.")
    lines.append("- Compare `pre_trade_features_only` against `behavior_augmented`; if only behavior features explain failures, adjust review workflow before changing thresholds.")
    lines.append("- Treat overheat `trend_maturity` and weak market-state slices as candidates for manual entry rule review.")
    lines.append("- Sector-level findings are now structurally usable on the full 61 ETF pool, but should still be checked for concentration before changing rules.")
    lines.append("")
    lines.append("## Not For Trading")
    lines.append("")
    lines.append("- The model is trained on future labels and is only valid for offline diagnostics.")
    lines.append("- `was_selected`, `was_bought`, and `exclude_reason` are replay behavior fields; they must not be treated as live alpha inputs.")
    lines.append("- No entry, exit, QMT, or realtime trading configuration is modified by this report.")
    return "\n".join(lines) + "\n"


def _metrics_table(results: list[Any]) -> str:
    rows = []
    for r in results:
        m = r.metrics
        rows.append(
            {
                "feature_set": r.feature_set,
                "target": r.target_name,
                "model": r.model_name,
                "sample_count": m["sample_count"],
                "positive_rate": m["positive_rate"],
                "accuracy": m["accuracy"],
                "precision": m["precision"],
                "recall": m["recall"],
                "f1": m["f1"],
                "roc_auc": m["roc_auc"],
                "confusion_matrix": f"tn={m['tn']}, fp={m['fp']}, fn={m['fn']}, tp={m['tp']}",
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False)


def _risk_bucket_table(risk: pd.DataFrame) -> str:
    if risk.empty:
        return "(no risk scores)"
    table = (
        risk.groupby("bad_entry_risk_bucket", dropna=False)
        .agg(
            sample_count=("auto_label", "count"),
            bad_count=("auto_label", lambda s: int((s == "bad_entry").sum())),
            good_count=("auto_label", lambda s: int((s == "good_entry").sum())),
            bad_rate=("auto_label", lambda s: float((s == "bad_entry").mean())),
            avg_bad_entry_risk_score=("bad_entry_risk_score", "mean"),
        )
        .reset_index()
    )
    order = {"low": 0, "medium": 1, "high": 2}
    table["_order"] = table["bad_entry_risk_bucket"].map(order).fillna(9)
    return table.sort_values("_order").drop(columns="_order").to_markdown(index=False)


def _high_risk_share(risk: pd.DataFrame, col: str) -> str:
    if col not in risk.columns:
        return "N/A"
    mask = _bool_series(risk[col])
    denom = int(mask.sum())
    if denom == 0:
        return "N/A"
    share = float((risk.loc[mask, "bad_entry_risk_bucket"] == "high").mean())
    return f"{share:.4f} ({int((risk.loc[mask, 'bad_entry_risk_bucket'] == 'high').sum())}/{denom})"


def _group_risk_table(risk: pd.DataFrame, col: str) -> str:
    if risk.empty or col not in risk.columns:
        return "(not available)"
    table = (
        risk.groupby(col, dropna=False)
        .agg(
            sample_count=("auto_label", "count"),
            bad_rate=("auto_label", lambda s: float((s == "bad_entry").mean())),
            high_risk_share=("bad_entry_risk_bucket", lambda s: float((s == "high").mean())),
            avg_risk=("bad_entry_risk_score", "mean"),
        )
        .reset_index()
        .sort_values(["high_risk_share", "sample_count"], ascending=[False, False])
        .head(20)
    )
    return table.to_markdown(index=False)


def _trend_table(risk: pd.DataFrame) -> str:
    if risk.empty or "trend_maturity" not in risk.columns:
        return "(not available)"
    tmp = risk.copy()
    tmp["trend_maturity_bucket"] = pd.to_numeric(tmp["trend_maturity"], errors="coerce").map(_trend_bucket)
    return _group_risk_table(tmp, "trend_maturity_bucket")


def _diagnostic_answers(df: pd.DataFrame, results: list[Any], risk: pd.DataFrame) -> list[str]:
    lines: list[str] = []
    entry_auc = _entry_score_auc(df)
    best_bad = _best_result(results, "bad_entry", "pre_trade_features_only")
    best_good = _best_result(results, "good_entry", "pre_trade_features_only")
    bad_auc = best_bad.metrics.get("roc_auc") if best_bad else "N/A"
    lines.append(f"1. baseline ML vs entry_score: entry_score bad_entry roc_auc={entry_auc}; best pre-trade bad_entry roc_auc={bad_auc}.")
    lines.append(f"2. good_entry explanatory features: see top coefficients/importances under `{best_good.feature_set}` if available.")
    lines.append(f"3. bad_entry explanatory features: see top coefficients/importances under `{best_bad.feature_set}` if available.")
    overall_bad = float((risk["auto_label"] == "bad_entry").mean()) if not risk.empty else np.nan
    high = risk.loc[risk["bad_entry_risk_bucket"] == "high"] if not risk.empty else risk
    high_bad = float((high["auto_label"] == "bad_entry").mean()) if high is not None and not high.empty else np.nan
    lines.append(f"4. high bad-risk bucket bad_entry rate: {high_bad:.4f}; overall bad_entry rate: {overall_bad:.4f}.")
    lines.append(f"5. was_selected=True high-risk share: {_high_risk_share(risk, 'was_selected')}.")
    lines.append(f"6. was_bought=True high-risk share: {_high_risk_share(risk, 'was_bought')}.")
    lines.append("7. market_state stability: inspect Market State Diagnostics for slices with high bad_rate or high_risk_share.")
    lines.append("8. trend_maturity: overheat bucket should be reviewed if it has elevated bad_rate or high_risk_share.")
    lines.append("9. sector_l2: available and used as an interpretable one-hot feature; inspect Sector L2 Diagnostics.")
    lines.append("10. overfitting: compare train/test positive rates, AUC, and tree vs logistic gaps; large gaps are a warning.")
    lines.append("11. entry project can use stable high-risk slices and feature importance as manual parameter evidence.")
    lines.append("12. no conclusion here can be directly used as a realtime trading signal.")
    return lines


def _best_result(results: list[Any], target: str, feature_set: str) -> Any | None:
    subset = [r for r in results if r.target_name == target and r.feature_set == feature_set]
    if not subset:
        return None

    def score(r: Any) -> float:
        auc = r.metrics.get("roc_auc")
        return -1.0 if auc == "N/A" else float(auc)

    return max(subset, key=score)


def _entry_score_auc(df: pd.DataFrame) -> str:
    if "entry_score" not in df.columns or "is_bad_entry" not in df.columns:
        return "N/A"
    y = df["is_bad_entry"].astype(int).to_numpy()
    score = -pd.to_numeric(df["entry_score"], errors="coerce").fillna(0.0).to_numpy()
    if len(np.unique(y)) < 2:
        return "N/A"
    order = np.argsort(score)
    ranks = np.empty(len(score), dtype=float)
    ranks[order] = np.arange(1, len(score) + 1)
    pos = y == 1
    n_pos = int(pos.sum())
    n_neg = int((~pos).sum())
    return f"{float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)):.4f}"


def _bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.fillna(False)
    return s.fillna(False).map(lambda v: str(v).strip().lower() in {"1", "true", "yes", "y", "selected"})


def _trend_bucket(value: float) -> str:
    if pd.isna(value):
        return "unknown"
    if float(value) <= 0.25:
        return "startup"
    if float(value) <= 0.50:
        return "confirmation"
    if float(value) <= 0.75:
        return "main_uptrend"
    return "overheat"
