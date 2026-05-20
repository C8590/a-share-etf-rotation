from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


PORTFOLIO_PATH = Path("data") / "portfolio.csv"
TRADE_HISTORY_PATH = Path("data") / "portfolio_trades.csv"

PORTFOLIO_COLUMNS = [
    "ETF代码",
    "ETF名称",
    "持仓份额",
    "平均买入价",
    "持仓成本",
    "最近买入日期",
    "备注",
]

TRADE_COLUMNS = [
    "日期",
    "ETF代码",
    "ETF名称",
    "操作类型",
    "成交价格",
    "成交份额",
    "成交金额",
    "交易原因",
    "备注",
]


def _normalize_symbol(value: Any) -> str:
    raw = str(value or "").strip()
    return raw.zfill(6) if raw.isdigit() else raw


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None) or pd.isna(value):
            return default
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_weighted_average_cost(
    old_shares: float,
    old_average_price: float,
    buy_shares: float,
    buy_price: float,
) -> float:
    total_shares = float(old_shares) + float(buy_shares)
    if total_shares <= 0:
        return 0.0
    old_cost = float(old_shares) * float(old_average_price)
    buy_amount = float(buy_shares) * float(buy_price)
    return (old_cost + buy_amount) / total_shares


def load_portfolio(path: str | Path = PORTFOLIO_PATH) -> pd.DataFrame:
    portfolio_path = Path(path)
    if not portfolio_path.exists():
        return pd.DataFrame(columns=PORTFOLIO_COLUMNS)
    frame = pd.read_csv(portfolio_path, dtype={"ETF代码": str}, encoding="utf-8-sig").fillna("")
    for col in PORTFOLIO_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    frame["ETF代码"] = frame["ETF代码"].map(_normalize_symbol)
    for col in ["持仓份额", "平均买入价", "持仓成本"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
    return frame[PORTFOLIO_COLUMNS]


def load_trade_history(path: str | Path = TRADE_HISTORY_PATH) -> pd.DataFrame:
    trade_path = Path(path)
    if not trade_path.exists():
        return pd.DataFrame(columns=TRADE_COLUMNS)
    frame = pd.read_csv(trade_path, dtype={"ETF代码": str}, encoding="utf-8-sig").fillna("")
    for col in TRADE_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    frame["ETF代码"] = frame["ETF代码"].map(_normalize_symbol)
    for col in ["成交价格", "成交份额", "成交金额"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
    return frame[TRADE_COLUMNS]


def append_trade(trade: dict[str, Any], path: str | Path = TRADE_HISTORY_PATH) -> None:
    trade_path = Path(path)
    trade_path.parent.mkdir(parents=True, exist_ok=True)
    row = {col: trade.get(col, "") for col in TRADE_COLUMNS}
    row["ETF代码"] = _normalize_symbol(row["ETF代码"])
    row["成交价格"] = _safe_float(row["成交价格"])
    row["成交份额"] = _safe_float(row["成交份额"])
    row["成交金额"] = _safe_float(row["成交金额"], row["成交价格"] * row["成交份额"])
    history = load_trade_history(trade_path)
    history = pd.concat([history, pd.DataFrame([row])], ignore_index=True)
    history.to_csv(trade_path, index=False, encoding="utf-8-sig")


def save_portfolio(
    holdings: list[dict[str, Any]],
    cash: float = 0.0,
    current_empty: bool = False,
    portfolio_path: str | Path = PORTFOLIO_PATH,
    current_position_path: str | Path | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not current_empty:
        for item in holdings:
            symbol = _normalize_symbol(item.get("symbol") or item.get("ETF代码"))
            shares = _safe_float(item.get("shares", item.get("持仓份额")))
            average_price = _safe_float(item.get("average_buy_price", item.get("cost_price", item.get("平均买入价"))))
            if not symbol or shares <= 0:
                continue
            rows.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": str(item.get("name") or item.get("ETF名称") or ""),
                    "持仓份额": shares,
                    "平均买入价": average_price,
                    "持仓成本": shares * average_price,
                    "最近买入日期": str(item.get("last_buy_date") or item.get("最近买入日期") or ""),
                    "备注": str(item.get("note") or item.get("备注") or ""),
                }
            )

    frame = pd.DataFrame(rows, columns=PORTFOLIO_COLUMNS)
    portfolio_path = Path(portfolio_path)
    portfolio_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(portfolio_path, index=False, encoding="utf-8-sig")

    if current_position_path is not None:
        payload_holdings = [
            {
                "symbol": row["ETF代码"],
                "name": row["ETF名称"],
                "shares": float(row["持仓份额"]),
                "average_buy_price": float(row["平均买入价"]),
                "cost_price": float(row["平均买入价"]),
                "last_buy_date": row["最近买入日期"],
                "note": row["备注"],
            }
            for row in rows
        ]
        payload: dict[str, Any] = {"cash": float(cash), "current_empty": bool(current_empty), "holdings": payload_holdings}
        if current_empty:
            payload["holdings"] = []
        current_path = Path(current_position_path)
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    return frame


def trade_from_buy(
    symbol: str,
    name: str,
    price: float,
    shares: float,
    trade_date: str | date | None = None,
    reason: str = "手动新增持仓",
    note: str = "",
) -> dict[str, Any]:
    actual_date = trade_date or date.today().isoformat()
    return {
        "日期": str(actual_date),
        "ETF代码": _normalize_symbol(symbol),
        "ETF名称": name,
        "操作类型": "买入",
        "成交价格": float(price),
        "成交份额": float(shares),
        "成交金额": float(price) * float(shares),
        "交易原因": reason,
        "备注": note,
    }
