from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .io_utils import read_table, write_table
from .ml_dataset import (
    TARGETS,
    build_feature_frame,
    build_time_split,
    prepare_ml_samples,
)


FEATURE_SETS = ["pre_trade_features_only", "behavior_augmented"]


@dataclass
class BaselineModelResult:
    feature_set: str
    target_name: str
    model_name: str
    metrics: dict[str, Any]
    importance: pd.DataFrame
    test_scores: np.ndarray


@dataclass
class BaselineRunResult:
    samples: pd.DataFrame
    split: Any
    model_results: list[BaselineModelResult]
    risk_scores: pd.DataFrame
    report: str


def run_baseline_from_file(
    samples_path: str | Path,
    out_dir: str | Path,
    target: str = "both",
    output_format: str = "csv",
) -> BaselineRunResult:
    samples = read_table(samples_path)
    result = run_baseline(samples, out_dir=out_dir, target=target, output_format=output_format)
    return result


def run_baseline(
    labeled_samples: pd.DataFrame,
    out_dir: str | Path,
    target: str = "both",
    output_format: str = "csv",
) -> BaselineRunResult:
    from .ml_report import build_ml_baseline_report

    df = prepare_ml_samples(labeled_samples)
    if df.empty:
        raise ValueError("no label_status=ok samples available for baseline ML")
    selected_targets = _target_names(target)
    split = build_time_split(df)
    results: list[BaselineModelResult] = []

    for feature_set in FEATURE_SETS:
        feature_frame = build_feature_frame(df.copy(), feature_set)
        x_all = feature_frame.matrix
        x_train = x_all.loc[split.train_mask].to_numpy(dtype=float)
        x_test = x_all.loc[split.test_mask].to_numpy(dtype=float)
        feature_names = list(feature_frame.matrix.columns)
        for target_name in selected_targets:
            y = df[TARGETS[target_name]].astype(int).to_numpy()
            y_train = y[split.train_mask.to_numpy()]
            y_test = y[split.test_mask.to_numpy()]
            logit = _fit_logistic(x_train, y_train)
            logit_scores = logit.predict_proba(x_test)
            results.append(
                BaselineModelResult(
                    feature_set=feature_set,
                    target_name=target_name,
                    model_name="logistic_regression_fallback",
                    metrics=_classification_metrics(y_train, y_test, logit_scores, split, df, target_name),
                    importance=_logistic_importance(feature_names, logit),
                    test_scores=logit_scores,
                )
            )
            tree = _fit_tree(x_train, y_train, feature_names)
            tree_scores = tree.predict_proba(x_test)
            results.append(
                BaselineModelResult(
                    feature_set=feature_set,
                    target_name=target_name,
                    model_name="decision_tree_fallback",
                    metrics=_classification_metrics(y_train, y_test, tree_scores, split, df, target_name),
                    importance=tree.importance_frame(),
                    test_scores=tree_scores,
                )
            )

    risk_scores = _build_risk_scores(df, split)
    out_path = Path(out_dir)
    write_table(risk_scores, out_path, "ml_entry_risk_scores", output_format)
    report = build_ml_baseline_report(df, split, results, risk_scores)
    (out_path / "ml_baseline_report.md").write_text(report, encoding="utf-8")
    return BaselineRunResult(samples=df, split=split, model_results=results, risk_scores=risk_scores, report=report)


def _target_names(target: str) -> list[str]:
    if target == "both":
        return ["good_entry", "bad_entry"]
    if target in TARGETS:
        return [target]
    raise ValueError("--target must be one of: both, good_entry, bad_entry")


@dataclass
class _LogisticModel:
    coef: np.ndarray
    intercept: float
    mean: np.ndarray
    scale: np.ndarray

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return np.array([], dtype=float)
        z = ((x - self.mean) / self.scale) @ self.coef + self.intercept
        return _sigmoid(z)


def _fit_logistic(x: np.ndarray, y: np.ndarray, iterations: int = 350, lr: float = 0.08, l2: float = 0.01) -> _LogisticModel:
    if x.size == 0:
        return _LogisticModel(np.zeros(0), 0.0, np.zeros(0), np.ones(0))
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0
    xs = (x - mean) / scale
    n, p = xs.shape
    coef = np.zeros(p, dtype=float)
    pos_rate = float(np.clip(y.mean() if len(y) else 0.0, 1e-4, 1 - 1e-4))
    intercept = float(np.log(pos_rate / (1 - pos_rate)))
    if len(np.unique(y)) < 2:
        return _LogisticModel(coef, intercept, mean, scale)
    for _ in range(iterations):
        pred = _sigmoid(xs @ coef + intercept)
        err = pred - y
        grad = (xs.T @ err) / max(n, 1) + l2 * coef
        grad_i = float(err.mean())
        coef -= lr * grad
        intercept -= lr * grad_i
    return _LogisticModel(coef, intercept, mean, scale)


def _logistic_importance(feature_names: list[str], model: _LogisticModel) -> pd.DataFrame:
    rows = [
        {"feature": name, "importance": float(abs(coef)), "signed_value": float(coef)}
        for name, coef in zip(feature_names, model.coef)
    ]
    return pd.DataFrame(rows).sort_values("importance", ascending=False).head(20).reset_index(drop=True)


@dataclass
class _TreeNode:
    probability: float
    feature_index: int | None = None
    threshold: float | None = None
    left: Any = None
    right: Any = None

    @property
    def is_leaf(self) -> bool:
        return self.feature_index is None


class _DecisionTree:
    def __init__(self, root: _TreeNode, feature_names: list[str], importances: dict[str, float]) -> None:
        self.root = root
        self.feature_names = feature_names
        self.importances = importances

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        return np.array([self._predict_row(row, self.root) for row in x], dtype=float)

    def importance_frame(self) -> pd.DataFrame:
        rows = [{"feature": k, "importance": float(v), "signed_value": float(v)} for k, v in self.importances.items()]
        if not rows:
            rows = [{"feature": "(constant)", "importance": 0.0, "signed_value": 0.0}]
        return pd.DataFrame(rows).sort_values("importance", ascending=False).head(20).reset_index(drop=True)

    def _predict_row(self, row: np.ndarray, node: _TreeNode) -> float:
        while not node.is_leaf:
            if row[int(node.feature_index)] <= float(node.threshold):
                node = node.left
            else:
                node = node.right
        return float(node.probability)


def _fit_tree(
    x: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    max_depth: int = 3,
    min_leaf: int = 30,
) -> _DecisionTree:
    importances: dict[str, float] = {}

    def build(x_sub: np.ndarray, y_sub: np.ndarray, depth: int) -> _TreeNode:
        probability = float(y_sub.mean()) if len(y_sub) else 0.0
        if depth >= max_depth or len(y_sub) < min_leaf * 2 or len(np.unique(y_sub)) < 2:
            return _TreeNode(probability=probability)
        parent = _gini(y_sub)
        best: tuple[float, int, float, np.ndarray] | None = None
        for j in range(x_sub.shape[1]):
            values = x_sub[:, j]
            unique = np.unique(values)
            if len(unique) <= 1:
                continue
            qs = np.linspace(0.1, 0.9, 9)
            thresholds = np.unique(np.quantile(values, qs))
            for threshold in thresholds:
                left_mask = values <= threshold
                left_n = int(left_mask.sum())
                right_n = len(y_sub) - left_n
                if left_n < min_leaf or right_n < min_leaf:
                    continue
                gain = parent - (left_n / len(y_sub)) * _gini(y_sub[left_mask]) - (right_n / len(y_sub)) * _gini(y_sub[~left_mask])
                if best is None or gain > best[0]:
                    best = (float(gain), j, float(threshold), left_mask)
        if best is None or best[0] <= 0:
            return _TreeNode(probability=probability)
        gain, j, threshold, left_mask = best
        importances[feature_names[j]] = importances.get(feature_names[j], 0.0) + gain * len(y_sub)
        return _TreeNode(
            probability=probability,
            feature_index=j,
            threshold=threshold,
            left=build(x_sub[left_mask], y_sub[left_mask], depth + 1),
            right=build(x_sub[~left_mask], y_sub[~left_mask], depth + 1),
        )

    root = build(x, y, 0) if x.size else _TreeNode(probability=0.0)
    total = sum(importances.values()) or 1.0
    importances = {k: v / total for k, v in importances.items()}
    return _DecisionTree(root, feature_names, importances)


def _classification_metrics(y_train: np.ndarray, y_test: np.ndarray, scores: np.ndarray, split: Any, df: pd.DataFrame, target_name: str) -> dict[str, Any]:
    pred = (scores >= 0.5).astype(int)
    tp = int(((pred == 1) & (y_test == 1)).sum())
    tn = int(((pred == 0) & (y_test == 0)).sum())
    fp = int(((pred == 1) & (y_test == 0)).sum())
    fn = int(((pred == 0) & (y_test == 1)).sum())
    accuracy = (tp + tn) / len(y_test) if len(y_test) else np.nan
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "sample_count": int(len(y_train) + len(y_test)),
        "train_count": int(len(y_train)),
        "test_count": int(len(y_test)),
        "positive_rate": float(np.mean(np.concatenate([y_train, y_test]))) if len(y_train) + len(y_test) else np.nan,
        "train_positive_rate": float(y_train.mean()) if len(y_train) else np.nan,
        "test_positive_rate": float(y_test.mean()) if len(y_test) else np.nan,
        "train_start": split.train_start,
        "train_end": split.train_end,
        "test_start": split.test_start,
        "test_end": split.test_end,
        "split_note": split.note,
        "accuracy": float(accuracy),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "roc_auc": _roc_auc(y_test, scores),
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "target": target_name,
    }


def _build_risk_scores(df: pd.DataFrame, split: Any) -> pd.DataFrame:
    feature_frame = build_feature_frame(df.copy(), "pre_trade_features_only")
    x = feature_frame.matrix.to_numpy(dtype=float)
    y = df["is_bad_entry"].astype(int).to_numpy()
    model = _fit_logistic(x[split.train_mask.to_numpy()], y[split.train_mask.to_numpy()])
    scores = model.predict_proba(x)
    risk = df.copy()
    risk["bad_entry_risk_score"] = scores
    q40 = float(np.quantile(scores, 0.40)) if len(scores) else 0.0
    q80 = float(np.quantile(scores, 0.80)) if len(scores) else 0.0
    risk["bad_entry_risk_bucket"] = np.where(scores >= q80, "high", np.where(scores >= q40, "medium", "low"))
    if "sector_l2" not in risk.columns:
        risk["sector_l2"] = risk.get("sector", "unknown")
    cols = [
        "trade_date",
        "code",
        "name",
        "sector_l1",
        "sector_l2",
        "market_state",
        "trend_maturity",
        "entry_score",
        "bad_entry_risk_score",
        "bad_entry_risk_bucket",
        "auto_label",
        "label_status",
        "was_selected",
        "was_bought",
    ]
    for col in cols:
        if col not in risk.columns:
            risk[col] = ""
    return risk[cols].sort_values(["trade_date", "bad_entry_risk_score"], ascending=[True, False]).reset_index(drop=True)


def _gini(y: np.ndarray) -> float:
    if len(y) == 0:
        return 0.0
    p = float(y.mean())
    return 1.0 - p * p - (1.0 - p) * (1.0 - p)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -35, 35)
    return 1.0 / (1.0 + np.exp(-z))


def _roc_auc(y: np.ndarray, score: np.ndarray) -> float | str:
    if len(y) == 0 or len(np.unique(y)) < 2:
        return "N/A"
    order = np.argsort(score)
    ranks = np.empty(len(score), dtype=float)
    ranks[order] = np.arange(1, len(score) + 1)
    pos = y == 1
    n_pos = int(pos.sum())
    n_neg = int((~pos).sum())
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))
