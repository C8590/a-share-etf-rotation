from __future__ import annotations

from pathlib import Path

import pandas as pd


CORE_STRATEGY_NAME = "日频右侧确认型 ETF 动量轮动策略"
CORE_STATUS = "recommended_for_observation"
CORE_REASON = "唯一保留策略：日频数据确认动量、趋势形态、成交活跃度和相对强弱后，再生成买入、持有、减仓、卖出或观察建议。"


def build_strategy_review(output_dir: str | Path = "output") -> pd.DataFrame:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "strategy_name": CORE_STRATEGY_NAME,
            "strategy_status": CORE_STATUS,
            "reason": CORE_REASON,
            "total_return": "",
            "annual_return": "",
            "max_drawdown": "",
            "sharpe_ratio": "",
            "calmar_ratio": "",
            "trade_count": "",
        }
    ]
    df = pd.DataFrame(rows)
    df.to_csv(output_path / "strategy_review.csv", index=False, encoding="utf-8-sig")
    return df


def strategy_status(strategy_name: str) -> str:
    return CORE_STATUS
