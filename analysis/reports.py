from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from analysis.performance import drawdown


def build_yearly_returns(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    output_dir: str | Path = "output",
) -> pd.DataFrame:
    rows = []
    equity = equity_curve["equity"].astype(float)
    trades = trades.copy()
    if not trades.empty:
        trades["date"] = pd.to_datetime(trades["date"])

    for year, series in equity.groupby(equity.index.year):
        year_trades = trades[trades["date"].dt.year == year] if not trades.empty else trades
        holdings = []
        if "holding_symbols" in equity_curve.columns:
            holdings = (
                equity_curve.loc[equity_curve.index.year == year, "holding_symbols"]
                .dropna()
                .astype(str)
                .str.split(",")
                .explode()
            )
            holdings = [item for item in holdings.tolist() if item]
        top_holdings = pd.Series(holdings).value_counts().head(5).index.tolist() if holdings else []
        rows.append(
            {
                "年份": int(year),
                "年初资产": float(series.iloc[0]),
                "年末资产": float(series.iloc[-1]),
                "年度收益率": float(series.iloc[-1] / series.iloc[0] - 1.0),
                "年度最大回撤": float(drawdown(series).min()),
                "年度交易次数": int(len(year_trades[year_trades["action"].isin(["BUY", "SELL"])]) if not year_trades.empty else 0),
                "当年主要持仓ETF": ",".join(top_holdings),
            }
        )

    result = pd.DataFrame(rows)
    result.to_csv(Path(output_dir) / "yearly_returns.csv", index=False, encoding="utf-8-sig")
    return result


def _reason_column(trades: pd.DataFrame) -> str | None:
    for candidate in ["调仓原因", "璋冧粨鍘熷洜"]:
        if candidate in trades.columns:
            return candidate
    for col in trades.columns:
        if "原因" in col:
            return col
    return None


def build_trade_diagnostics(
    trades: pd.DataFrame,
    close: pd.DataFrame,
    output_dir: str | Path = "output",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if trades.empty:
        result = pd.DataFrame()
        result.to_csv(Path(output_dir) / "trade_diagnostics.csv", index=False, encoding="utf-8-sig")
        return result, {}

    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades["date"])
    reason_col = _reason_column(trades)
    open_lots: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []

    for _, trade in trades.sort_values("date").iterrows():
        symbol = trade["symbol"]
        if trade["action"] == "BUY":
            open_lots.setdefault(symbol, []).append(
                {
                    "date": trade["date"],
                    "price": float(trade.get("成交价格", trade.get("鎴愪氦浠锋牸", 0))),
                    "shares": float(trade.get("实际成交份额", trade.get("瀹為檯鎴愪氦浠介", 0))),
                    "reason": str(trade.get(reason_col, "")) if reason_col else "",
                }
            )
        elif trade["action"] == "SELL":
            sell_shares = float(trade.get("实际成交份额", trade.get("瀹為檯鎴愪氦浠介", 0)))
            sell_price = float(trade.get("成交价格", trade.get("鎴愪氦浠锋牸", 0)))
            sell_reason = str(trade.get(reason_col, "")) if reason_col else ""
            while sell_shares > 1e-8 and open_lots.get(symbol):
                lot = open_lots[symbol][0]
                matched = min(sell_shares, lot["shares"])
                start = lot["date"]
                end = trade["date"]
                price_path = close.loc[(close.index >= start) & (close.index <= end), symbol].dropna()
                buy_price = float(lot["price"])
                single_return = sell_price / buy_price - 1.0 if buy_price > 0 else 0.0
                path_returns = price_path / buy_price - 1.0 if not price_path.empty and buy_price > 0 else pd.Series(dtype=float)
                rows.append(
                    {
                        "调仓日期": str(end.date()),
                        "ETF代码": symbol,
                        "ETF名称": trade.get("name", symbol),
                        "买入ETF": f"{symbol} {trade.get('name', symbol)}",
                        "卖出ETF": f"{symbol} {trade.get('name', symbol)}",
                        "买入原因": lot["reason"],
                        "卖出原因": sell_reason,
                        "持有天数": int((end - start).days),
                        "单笔收益率": float(single_return),
                        "单笔最大浮亏": float(path_returns.min()) if not path_returns.empty else 0.0,
                        "单笔最大浮盈": float(path_returns.max()) if not path_returns.empty else 0.0,
                        "是否盈利": bool(single_return > 0),
                        "匹配份额": float(matched),
                    }
                )
                lot["shares"] -= matched
                sell_shares -= matched
                if lot["shares"] <= 1e-8:
                    open_lots[symbol].pop(0)

    result = pd.DataFrame(rows)
    result.to_csv(Path(output_dir) / "trade_diagnostics.csv", index=False, encoding="utf-8-sig")
    summary: dict[str, Any] = {}
    if not result.empty:
        by_symbol = result.groupby(["ETF代码", "ETF名称"])["单笔收益率"]
        summary = {
            "profit_contributors": by_symbol.sum().sort_values(ascending=False).head(10).to_dict(),
            "loss_contributors": by_symbol.sum().sort_values().head(10).to_dict(),
            "trade_counts": result.groupby(["ETF代码", "ETF名称"]).size().sort_values(ascending=False).head(10).to_dict(),
        }
    return result, summary
