from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_STRATEGY_STATUS = {
    "original": "rejected",
    "balanced": "research_only",
    "conservative": "defensive_only",
    "equal_weight_monthly": "recommended_for_observation",
    "reduced_equal_weight_monthly": "recommended_for_observation",
    "momentum_rotation_monthly": "research_observation_candidate",
}


STATUS_REASON = {
    "original": "Legacy rotation profile is kept for comparison but is not suitable for observation.",
    "balanced": "Full-sample performance is good, but out-of-sample stability is not strong enough for recommendation.",
    "conservative": "Defensive profile can be useful in risk-off research, but it is not the main observation strategy.",
    "equal_weight_monthly": "Simple monthly equal-weight approach has shown better out-of-sample stability.",
    "reduced_equal_weight_monthly": "Fixed selected-basket equal-weight benchmark; useful as a stable baseline, not a dynamic rotation strategy.",
    "momentum_rotation_monthly": "Dynamic close-momentum rotation is the next main observation candidate after additional live validation.",
}


def build_strategy_review(
    output_dir: str | Path = "output",
    metrics_by_strategy: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    rows = []
    metrics_by_strategy = metrics_by_strategy or {}
    for strategy_name, status in DEFAULT_STRATEGY_STATUS.items():
        metric = metrics_by_strategy.get(strategy_name, {})
        rows.append(
            {
                "strategy_name": strategy_name,
                "strategy_status": status,
                "reason": STATUS_REASON.get(strategy_name, ""),
                "annual_return": metric.get("annual_return"),
                "max_drawdown": metric.get("max_drawdown"),
                "sharpe": metric.get("sharpe", metric.get("sharpe_ratio")),
                "calmar": metric.get("calmar", metric.get("calmar_ratio")),
            }
        )
    result = pd.DataFrame(rows)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path / "strategy_review.csv", index=False, encoding="utf-8-sig")
    return result


def strategy_status(strategy_name: str) -> str:
    return DEFAULT_STRATEGY_STATUS.get(strategy_name, "research_only")
