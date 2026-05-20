from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .io_utils import read_table
from .ml_baseline import _fit_logistic, _roc_auc
from .ml_dataset import build_feature_frame, prepare_ml_samples


ROLLING_WINDOWS = [
    ("train_to_2025_06_30__test_2025_q3", "2025-06-30", "2025-07-01", "2025-09-30"),
    ("train_to_2025_09_30__test_2025_q4", "2025-09-30", "2025-10-01", "2025-12-31"),
    ("train_to_2025_12_31__test_2026_ytd", "2025-12-31", "2026-01-01", "2026-05-19"),
]
LABEL_POLICIES = {
    "strict": {"bad_return": -0.04, "bad_drawdown": -0.07, "good_return": 0.06},
    "default": {},
    "loose": {"bad_return": -0.02, "bad_drawdown": -0.04, "good_return": 0.03},
}


@dataclass
class StabilityRunResult:
    rolling: pd.DataFrame
    market_state: pd.DataFrame
    sector_l2: pd.DataFrame
    label_policy: pd.DataFrame
    report: str


def run_ml_stability_from_file(samples_path: str | Path, out_dir: str | Path) -> StabilityRunResult:
    samples = read_table(samples_path)
    return run_ml_stability(samples, out_dir=out_dir)


def run_ml_stability(labeled_samples: pd.DataFrame, out_dir: str | Path) -> StabilityRunResult:
    raw = labeled_samples.copy(deep=True)
    df = prepare_ml_samples(raw)
    if df.empty:
        raise ValueError("no label_status=ok samples available for ML stability diagnostics")

    rolling = rolling_split_diagnostics(df)
    scores = _fit_full_bad_model_scores(df)
    scored = _with_risk_bucket(df, scores)
    market = grouped_stability(scored, "market_state")
    sector = grouped_stability(scored, "sector_l2")
    policy = label_policy_diagnostics(raw)
    report = build_ml_stability_report(rolling, market, sector, policy)

    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "ml_stability_report.md").write_text(report, encoding="utf-8")
    return StabilityRunResult(rolling=rolling, market_state=market, sector_l2=sector, label_policy=policy, report=report)


def rolling_split_diagnostics(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name, train_end, test_start, test_end in ROLLING_WINDOWS:
        dates = pd.to_datetime(df["trade_date"], errors="coerce")
        train_mask = dates <= pd.Timestamp(train_end)
        test_mask = dates.between(pd.Timestamp(test_start), pd.Timestamp(test_end), inclusive="both")
        scores = _fit_scores(df, train_mask, test_mask)
        y_test = df.loc[test_mask, "is_bad_entry"].astype(int).to_numpy()
        rows.append(
            {
                "split": name,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                **_score_metrics(y_test, scores),
            }
        )
    return pd.DataFrame(rows)


def grouped_stability(scored: pd.DataFrame, group_col: str, min_rows: int = 20) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if group_col not in scored.columns:
        return pd.DataFrame(columns=["group", "sample_count", "bad_entry_rate", "bad_entry_risk_auc", "high_risk_bad_entry_rate", "high_risk_lift", "status"])
    for group, sub in scored.groupby(group_col, dropna=False):
        y = sub["is_bad_entry"].astype(int).to_numpy()
        scores = sub["bad_entry_risk_score"].to_numpy(dtype=float)
        high = sub.loc[sub["bad_entry_risk_bucket"] == "high"]
        bad_rate = float(y.mean()) if len(y) else np.nan
        high_bad = float(high["is_bad_entry"].mean()) if not high.empty else np.nan
        lift = float(high_bad / bad_rate) if bad_rate and not pd.isna(high_bad) else np.nan
        auc = _roc_auc(y, scores)
        status = "small_sample" if len(sub) < min_rows else ("model_fails" if _fails_group(auc, lift) else "ok")
        rows.append(
            {
                group_col: group,
                "sample_count": int(len(sub)),
                "bad_entry_rate": bad_rate,
                "bad_entry_risk_auc": auc,
                "high_risk_bad_entry_rate": high_bad,
                "high_risk_lift": lift,
                "status": status,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["status", "high_risk_lift", "sample_count"], ascending=[True, False, False]).reset_index(drop=True)


def derive_label_policy(samples: pd.DataFrame, policy: str) -> pd.DataFrame:
    if policy not in LABEL_POLICIES:
        raise ValueError(f"unknown label policy: {policy}")
    out = samples.copy(deep=True)
    if policy == "default":
        return out
    cfg = LABEL_POLICIES[policy]
    status = out.get("label_status", pd.Series("", index=out.index)).fillna("").astype(str)
    ok = status.eq("ok")
    ret10 = pd.to_numeric(out.get("future_return_10d"), errors="coerce")
    drawdown = pd.to_numeric(out.get("future_max_drawdown_10d"), errors="coerce")
    label = pd.Series("neutral_entry", index=out.index, dtype=object)
    good = ok & (ret10 >= float(cfg["good_return"])) & (drawdown > float(cfg["bad_drawdown"]))
    bad = ok & ((ret10 <= float(cfg["bad_return"])) | (drawdown <= float(cfg["bad_drawdown"])))
    label.loc[good] = "good_entry"
    label.loc[bad] = "bad_entry"
    label.loc[~ok] = "unlabeled"
    out["auto_label"] = label
    return out


def label_policy_diagnostics(samples: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for policy in LABEL_POLICIES:
        df = prepare_ml_samples(derive_label_policy(samples, policy))
        dates = pd.to_datetime(df["trade_date"], errors="coerce")
        train_mask = dates <= pd.Timestamp("2025-12-31")
        test_mask = dates.between(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-05-19"), inclusive="both")
        scores = _fit_scores(df, train_mask, test_mask)
        y_test = df.loc[test_mask, "is_bad_entry"].astype(int).to_numpy()
        rows.append({"label_policy": policy, **_score_metrics(y_test, scores)})
    return pd.DataFrame(rows)


def build_ml_stability_report(
    rolling: pd.DataFrame,
    market_state: pd.DataFrame,
    sector_l2: pd.DataFrame,
    label_policy: pd.DataFrame,
) -> str:
    lines: list[str] = []
    lines.append("# ml_stability_report")
    lines.append("")
    lines.append("This report validates offline bad_entry risk diagnostics only. It does not generate trading advice or write entry parameters.")
    lines.append("")
    lines.append("## Rolling Time Validation")
    lines.append("")
    lines.append(_markdown(rolling))
    lines.append("")
    lines.append("## Market State Validation")
    lines.append("")
    lines.append(_markdown(market_state))
    lines.append("")
    lines.append("## Sector L2 Validation")
    lines.append("")
    lines.append(_markdown(sector_l2))
    lines.append("")
    lines.append("### Top Risky sector_l2")
    lines.append("")
    if not sector_l2.empty and "high_risk_lift" in sector_l2.columns:
        top = sector_l2.sort_values(["high_risk_lift", "sample_count"], ascending=[False, False]).head(10)
        lines.append(_markdown(top))
    else:
        lines.append("(not available)")
    lines.append("")
    lines.append("### Sectors Where Model Fails")
    lines.append("")
    if not sector_l2.empty and "status" in sector_l2.columns:
        failed = sector_l2.loc[sector_l2["status"].eq("model_fails")]
        lines.append(_markdown(failed) if not failed.empty else "(none)")
    else:
        lines.append("(not available)")
    lines.append("")
    lines.append("## Label Policy Sensitivity")
    lines.append("")
    lines.append(_markdown(label_policy))
    lines.append("")
    lines.append("## Answers")
    lines.append("")
    lines.extend(_answers(rolling, market_state, sector_l2, label_policy))
    return "\n".join(lines) + "\n"


def _fit_full_bad_model_scores(df: pd.DataFrame) -> np.ndarray:
    dates = pd.to_datetime(df["trade_date"], errors="coerce")
    train_mask = dates <= pd.Timestamp("2025-12-31")
    all_mask = pd.Series(True, index=df.index)
    return _fit_scores(df, train_mask, all_mask)


def _fit_scores(df: pd.DataFrame, train_mask: pd.Series, test_mask: pd.Series) -> np.ndarray:
    if not train_mask.any() or not test_mask.any():
        return np.array([], dtype=float)
    features = build_feature_frame(df.copy(), "pre_trade_features_only").matrix
    x = features.to_numpy(dtype=float)
    y = df["is_bad_entry"].astype(int).to_numpy()
    model = _fit_logistic(x[train_mask.to_numpy()], y[train_mask.to_numpy()])
    return model.predict_proba(x[test_mask.to_numpy()])


def _with_risk_bucket(df: pd.DataFrame, scores: np.ndarray) -> pd.DataFrame:
    out = df.copy()
    out["bad_entry_risk_score"] = scores
    q40 = float(np.quantile(scores, 0.40)) if len(scores) else 0.0
    q80 = float(np.quantile(scores, 0.80)) if len(scores) else 0.0
    out["bad_entry_risk_bucket"] = np.where(scores >= q80, "high", np.where(scores >= q40, "medium", "low"))
    return out


def _score_metrics(y: np.ndarray, scores: np.ndarray) -> dict[str, Any]:
    if len(y) == 0:
        return {
            "sample_count": 0,
            "bad_entry_positive_rate": np.nan,
            "bad_entry_roc_auc": "N/A",
            "precision": np.nan,
            "recall": np.nan,
            "f1": np.nan,
            "high_risk_bad_entry_rate": np.nan,
            "high_risk_lift": np.nan,
        }
    pred = scores >= 0.5
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    q80 = float(np.quantile(scores, 0.80)) if len(scores) else np.inf
    high = y[scores >= q80]
    bad_rate = float(y.mean())
    high_bad = float(high.mean()) if len(high) else np.nan
    return {
        "sample_count": int(len(y)),
        "bad_entry_positive_rate": bad_rate,
        "bad_entry_roc_auc": _roc_auc(y, scores),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "high_risk_bad_entry_rate": high_bad,
        "high_risk_lift": float(high_bad / bad_rate) if bad_rate and not pd.isna(high_bad) else np.nan,
    }


def _fails_group(auc: Any, lift: float) -> bool:
    auc_bad = auc != "N/A" and float(auc) < 0.55
    lift_bad = pd.isna(lift) or float(lift) <= 1.0
    return bool(auc_bad or lift_bad)


def _answers(rolling: pd.DataFrame, market: pd.DataFrame, sector: pd.DataFrame, policy: pd.DataFrame) -> list[str]:
    rolling_stable = _all_lift_gt_one(rolling)
    policy_stable = _all_lift_gt_one(policy)
    market_fail = int((market.get("status", pd.Series(dtype=str)) == "model_fails").sum()) if not market.empty else 0
    sector_fail = int((sector.get("status", pd.Series(dtype=str)) == "model_fails").sum()) if not sector.empty else 0
    return [
        f"1. Time stability: {'stable' if rolling_stable else 'mixed'}; high-risk lift should be reviewed by split.",
        f"2. High-risk lift > 1 across rolling splits: {rolling_stable}.",
        f"3. Market-state dependence: {market_fail} market_state group(s) are marked model_fails.",
        f"4. Sector dependence: {sector_fail} sector_l2 group(s) are marked model_fails; check concentration before rule review.",
        f"5. Label policy sensitivity: {'stable' if policy_stable else 'mixed'} across strict/default/loose policies.",
        "6. Results may be shared with entry for manual rule review if high-risk lift is stable and group failures are understood.",
        "7. Do not absorb conclusions from small-sample groups, model_fails groups, or label policies whose lift collapses.",
    ]


def _all_lift_gt_one(df: pd.DataFrame) -> bool:
    if df.empty or "high_risk_lift" not in df.columns:
        return False
    vals = pd.to_numeric(df["high_risk_lift"], errors="coerce").dropna()
    return bool(not vals.empty and (vals > 1.0).all())


def _markdown(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "(no rows)"
    return df.to_markdown(index=False)
