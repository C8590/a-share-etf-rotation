from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


MAIN_STRATEGY = "reduced_equal_weight_monthly"
STRATEGY_ORDER = [
    "reduced_equal_weight_monthly",
    "equal_weight_monthly",
    "balanced",
    "conservative",
]


@dataclass(frozen=True)
class DashboardData:
    overview: dict[str, Any]
    signals: pd.DataFrame
    qa_report: dict[str, Any]
    strategy_review: pd.DataFrame
    etf_names: dict[str, str]


BUY_RE = re.compile(
    r"(?P<code>\d{6})\s+(?P<name>.*?):\s+预计买入\s+(?P<shares>[\d.]+)\s+份，"
    r"预计成交金额\s+(?P<amount>[\d.]+)\s+元。原因:\s*(?P<reason>.*)"
)

SKIP_RE = re.compile(
    r"跳过ETF\s+(?P<code>\d{6})\s+(?P<name>.*?):\s+跳过原因:\s*(?P<reason>.*?)；"
    r"一手金额:\s*(?P<lot_cash>.*?)；当前可用现金:\s*(?P<available_cash>.*?)；目标金额:\s*(?P<target_amount>.*)"
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def load_etf_names(project_root: Path) -> dict[str, str]:
    path = project_root / "config" / "etf_pool.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    items = raw.get("etfs", raw.get("etf_pool", raw if isinstance(raw, list) else []))
    return {str(item.get("symbol", "")).zfill(6): str(item.get("name", "")) for item in items if item.get("symbol")}


def ordered_signals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "strategy_name" not in df.columns:
        return df
    order_map = {name: idx for idx, name in enumerate(STRATEGY_ORDER)}
    result = df.copy()
    result["_order"] = result["strategy_name"].map(order_map).fillna(999)
    return result.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)


def _txt_overview(project_root: Path) -> dict[str, str]:
    path = project_root / "output" / "compare_signal.txt"
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("- "):
            continue
        text = line[2:]
        if ": " in text:
            key, value = text.split(": ", 1)
            result[key.strip()] = value.strip()
    return result


def build_overview(project_root: Path, signals: pd.DataFrame, qa_report: dict[str, Any]) -> dict[str, Any]:
    txt = _txt_overview(project_root)
    main_row = pd.Series(dtype=object)
    if not signals.empty:
        main = signals[signals["strategy_name"] == MAIN_STRATEGY]
        main_row = main.iloc[0] if not main.empty else signals.iloc[0]

    signal_date = str(main_row.get("signal_date", txt.get("当前信号日期", "")) or "")
    latest_data_date = str(main_row.get("latest_data_date", txt.get("数据最新日期", "")) or "")
    current_cash = main_row.get("current_cash", txt.get("当前真实现金", ""))
    if current_cash not in ("", None):
        try:
            current_cash = f"{float(current_cash):.2f} 元"
        except (TypeError, ValueError):
            current_cash = str(current_cash)
    current_positions = str(main_row.get("current_positions", txt.get("当前真实持仓", "空仓")) or "空仓")
    allow_small_observation = qa_report.get("allow_small_observation")
    if allow_small_observation is None:
        allow_small_observation = txt.get("是否允许小额观察", "UNKNOWN")
    allow_text = "YES" if allow_small_observation is True else "NO" if allow_small_observation is False else str(allow_small_observation)

    if signal_date and latest_data_date and signal_date != latest_data_date:
        risk_status = "需确认信号日期"
    elif allow_text == "YES":
        risk_status = "允许小额观察"
    else:
        risk_status = "质量检查未通过"

    return {
        "signal_date": signal_date or "N/A",
        "latest_data_date": latest_data_date or "N/A",
        "current_cash": current_cash,
        "current_positions": current_positions,
        "main_strategy": MAIN_STRATEGY,
        "allow_small_observation": allow_text,
        "risk_status": risk_status,
    }


def load_dashboard_data(project_root: Path) -> DashboardData:
    output = project_root / "output"
    signals = ordered_signals(_read_csv(output / "compare_signal.csv"))
    qa_report = _read_json(output / "qa_report.json")
    strategy_review = _read_csv(output / "strategy_review.csv")
    etf_names = load_etf_names(project_root)
    overview = build_overview(project_root, signals, qa_report)
    return DashboardData(
        overview=overview,
        signals=signals,
        qa_report=qa_report,
        strategy_review=strategy_review,
        etf_names=etf_names,
    )


def split_pipe_items(value: Any) -> list[str]:
    if value is None or pd.isna(value):
        return []
    text = str(value).strip()
    if not text or text == "无":
        return []
    return [item.strip() for item in text.split(" | ") if item.strip()]


def parse_target_table(row: pd.Series, etf_names: dict[str, str]) -> pd.DataFrame:
    symbols = [item.strip() for item in str(row.get("target_symbols", "")).split(",") if item.strip() and item.strip() != "空仓"]
    weight = f"{100 / len(symbols):.1f}%" if symbols else ""
    return pd.DataFrame(
        [{"ETF代码": symbol, "ETF名称": etf_names.get(symbol, symbol), "目标权重": weight} for symbol in symbols]
    )


def parse_buy_table(row: pd.Series) -> pd.DataFrame:
    records = []
    target_symbols = [item.strip() for item in str(row.get("target_symbols", "")).split(",") if item.strip() and item.strip() != "空仓"]
    target_weight = f"{100 / len(target_symbols):.1f}%" if target_symbols else ""
    for item in split_pipe_items(row.get("buy_share_advice")):
        match = BUY_RE.match(item)
        if not match:
            continue
        records.append(
            {
                "ETF代码": match.group("code"),
                "ETF名称": match.group("name"),
                "建议份额": float(match.group("shares")),
                "预计成交金额": float(match.group("amount")),
                "目标权重": target_weight,
                "原因": match.group("reason"),
            }
        )
    return pd.DataFrame(records)


def parse_skip_table(row: pd.Series) -> pd.DataFrame:
    records = []
    for item in split_pipe_items(row.get("skipped_buy_advice")):
        match = SKIP_RE.match(item)
        if not match:
            continue
        records.append(
            {
                "ETF代码": match.group("code"),
                "ETF名称": match.group("name"),
                "跳过原因": match.group("reason"),
                "一手金额": match.group("lot_cash"),
                "当前可用现金": match.group("available_cash"),
                "目标金额": match.group("target_amount"),
            }
        )
    return pd.DataFrame(records)


def strategy_row(signals: pd.DataFrame, strategy_name: str) -> pd.Series:
    if signals.empty:
        return pd.Series(dtype=object)
    matched = signals[signals["strategy_name"] == strategy_name]
    if matched.empty:
        return pd.Series(dtype=object)
    return matched.iloc[0]
