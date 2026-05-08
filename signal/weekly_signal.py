from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from backtest.portfolio import FeeConfig, calculate_trade_cost
from strategy.etf_rotation import ETFRotationStrategy, get_rebalance_dates


DEFAULT_POSITION = {"cash": 10000, "positions": {}}
INSUFFICIENT_DATA_WARNING = "当前有效ETF数量不足，结果仅用于流程测试，不代表策略有效性。"


def _format_pct(value: float) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{value * 100:.2f}%"


def ensure_current_position(path: str | Path = "config/current_position.yaml") -> dict[str, Any]:
    current_path = Path(path)
    if not current_path.exists():
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_text(yaml.safe_dump(DEFAULT_POSITION, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return DEFAULT_POSITION.copy()

    with current_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if "cash" not in data:
        data["cash"] = 10000
    if "positions" not in data or data["positions"] is None:
        data["positions"] = {}
    return data


def _position_lines(positions: dict[str, Any], etf_info: dict[str, dict[str, str]]) -> list[str]:
    if not positions:
        return ["- 空仓"]
    lines = []
    for symbol, item in positions.items():
        name = item.get("name") or etf_info.get(symbol, {}).get("name", symbol)
        shares = float(item.get("shares", 0))
        cost_price = float(item.get("cost_price", 0))
        lines.append(f"- {symbol} {name}: {shares:.0f} 份，成本价 {cost_price:.4f}")
    return lines


def _cash_after_sell(
    cash: float,
    positions: dict[str, Any],
    sell_symbols: list[str],
    prices: pd.Series,
    fee_config: FeeConfig,
) -> tuple[float, list[str]]:
    notes: list[str] = []
    available_cash = cash
    for symbol in sell_symbols:
        item = positions.get(symbol, {})
        price = prices.get(symbol)
        shares = float(item.get("shares", 0))
        if shares <= 0 or pd.isna(price):
            notes.append(f"- {symbol}: 缺少可用价格或份额，无法估算卖出资金")
            continue
        notional = shares * float(price)
        costs = calculate_trade_cost(notional, "sell", fee_config)
        available_cash += notional - costs["total_cost"]
    return available_cash, notes


def _round_buy_shares(target_notional: float, price: float, cash: float, lot_size: int, fee_config: FeeConfig) -> tuple[float, float, float]:
    if target_notional <= 0 or price <= 0 or cash <= 0:
        return 0.0, 0.0, cash
    shares = np.floor((target_notional / price) / lot_size) * lot_size
    while shares >= lot_size:
        notional = float(shares * price)
        costs = calculate_trade_cost(notional, "buy", fee_config)
        total_cash = notional + costs["total_cost"]
        if total_cash <= cash + 1e-8:
            return float(shares), notional, cash - total_cash
        shares -= lot_size
    return 0.0, 0.0, cash


def generate_weekly_signal_text(
    strategy: ETFRotationStrategy,
    equity_curve: pd.DataFrame,
    etf_info: dict[str, dict[str, str]],
    signal_weekday: int,
    output_path: str | Path = "output/weekly_signal.txt",
    current_position_path: str | Path = "config/current_position.yaml",
    fee_config: FeeConfig | None = None,
    lot_size: int = 100,
    enable_lot_rounding: bool = True,
    effective_etf_count: int | None = None,
    min_effective_etf_count: int = 5,
    rebalance_frequency: str = "weekly",
) -> str:
    signal_dates = get_rebalance_dates(strategy.close.index, rebalance_frequency, signal_weekday)
    if not signal_dates:
        raise ValueError("行情日期为空，无法生成周信号")

    latest_signal_date = signal_dates[-1]
    if latest_signal_date not in equity_curve.index:
        raise ValueError(f"权益曲线缺少信号日期: {latest_signal_date.date()}")

    fee_config = fee_config or FeeConfig()
    current_position = ensure_current_position(current_position_path)
    cash = float(current_position.get("cash", 0))
    positions = current_position.get("positions", {}) or {}
    current_holdings = [str(symbol).zfill(6) for symbol in positions.keys() if float(positions[symbol].get("shares", 0)) > 0]

    signal = strategy.generate_target(latest_signal_date, current_holdings)
    target = list(signal["target"])
    ranks = signal["ranks"].copy()
    latest_prices = strategy.close.loc[latest_signal_date]

    sell_symbols = [symbol for symbol in current_holdings if symbol not in target]
    cash_for_buy, sell_notes = _cash_after_sell(cash, positions, sell_symbols, latest_prices, fee_config)

    current_values: dict[str, float] = {}
    unknown_value_notes: list[str] = []
    total_value = cash_for_buy
    for symbol, item in positions.items():
        symbol = str(symbol).zfill(6)
        if symbol in sell_symbols:
            continue
        shares = float(item.get("shares", 0))
        price = latest_prices.get(symbol)
        if shares <= 0:
            continue
        if pd.isna(price):
            unknown_value_notes.append(f"- {symbol}: 缺少最新价格，未计入目标仓位估算")
            continue
        value = shares * float(price)
        current_values[symbol] = value
        total_value += value

    target_weight = 1 / len(target) if target else 0
    buy_advice: list[dict[str, Any]] = []
    skipped_buy_advice: list[dict[str, Any]] = []
    estimated_cash = cash_for_buy
    if target:
        target_value = total_value * target_weight
        for symbol in target:
            price = latest_prices.get(symbol)
            if pd.isna(price):
                skipped_buy_advice.append(
                    {
                        "symbol": symbol,
                        "reason": "缺少最新价格，无法估算买入",
                        "one_lot_cash": None,
                        "available_cash": estimated_cash,
                        "target_value": target_value,
                    }
                )
                continue
            buy_gap = max(target_value - current_values.get(symbol, 0.0), 0.0)
            available_cash_before = estimated_cash
            if not enable_lot_rounding:
                shares = buy_gap / float(price)
                notional = shares * float(price)
                costs = calculate_trade_cost(notional, "buy", fee_config)
                if notional + costs["total_cost"] > estimated_cash:
                    shares = max((estimated_cash - costs["total_cost"]) / float(price), 0)
                    notional = shares * float(price)
                estimated_cash -= notional + calculate_trade_cost(notional, "buy", fee_config)["total_cost"]
            else:
                shares, notional, estimated_cash = _round_buy_shares(
                    buy_gap,
                    float(price),
                    estimated_cash,
                    lot_size,
                    fee_config,
                )
            if shares > 0:
                buy_advice.append({"symbol": symbol, "shares": shares, "notional": notional, "reason": signal["buy_reasons"].get(symbol, "补足目标仓位")})
            elif buy_gap > 0:
                one_lot_notional = float(price) * lot_size
                one_lot_cost = calculate_trade_cost(one_lot_notional, "buy", fee_config)["total_cost"]
                one_lot_cash = one_lot_notional + one_lot_cost
                if available_cash_before < one_lot_cash:
                    reason = "当前可用现金不足以买入一手"
                elif buy_gap < one_lot_cash:
                    reason = "目标补足金额不足一手，按交易单位取整后跳过"
                else:
                    reason = "按交易单位和费用约束取整后无法买入一手"
                skipped_buy_advice.append(
                    {
                        "symbol": symbol,
                        "reason": reason,
                        "one_lot_cash": one_lot_cash,
                        "available_cash": available_cash_before,
                        "target_value": target_value,
                    }
                )

    lines: list[str] = []
    lines.append("A股ETF低频轮动系统 - 最新周信号")
    lines.append("=" * 42)
    lines.append(f"信号日期: {latest_signal_date.date()}")
    lines.append("执行时间: 下一个交易日手动执行，回测中按下一交易日开盘价模拟")
    if effective_etf_count is not None and effective_etf_count < min_effective_etf_count:
        lines.append(f"数据质量提示: {INSUFFICIENT_DATA_WARNING}")
        lines.append(f"当前有效ETF数量: {effective_etf_count}/{min_effective_etf_count}")
    lines.append("")

    lines.append(f"当前真实现金: {cash:.2f} 元")
    lines.append("当前真实持仓:")
    lines.extend(_position_lines(positions, etf_info))
    lines.append("")

    lines.append("系统目标持仓:")
    if target:
        for symbol in target:
            lines.append(f"- {symbol} {etf_info.get(symbol, {}).get('name', symbol)}: {target_weight:.0%}")
    else:
        lines.append("- 空仓: 100% 现金")

    lines.append("建议卖出:")
    if sell_symbols:
        for symbol in sell_symbols:
            reason = signal["sell_reasons"].get(symbol, "不再属于系统目标持仓")
            lines.append(f"- {symbol} {etf_info.get(symbol, {}).get('name', symbol)}: 全部卖出。原因: {reason}")
    else:
        lines.append("- 无")

    lines.append("建议买入:")
    if buy_advice:
        for item in buy_advice:
            symbol = item["symbol"]
            lines.append(
                f"- {symbol} {etf_info.get(symbol, {}).get('name', symbol)}: "
                f"预计买入 {item['shares']:.0f} 份，预计成交金额 {item['notional']:.2f} 元。"
                f"原因: {item['reason']}"
            )
    else:
        lines.append("- 无")
    lines.append("跳过买入:")
    if skipped_buy_advice:
        for item in skipped_buy_advice:
            symbol = item["symbol"]
            one_lot_text = "N/A" if item["one_lot_cash"] is None else f"{item['one_lot_cash']:.2f} 元"
            lines.append(
                f"- 跳过ETF {symbol} {etf_info.get(symbol, {}).get('name', symbol)}: "
                f"跳过原因: {item['reason']}；"
                f"一手金额: {one_lot_text}；"
                f"当前可用现金: {item['available_cash']:.2f} 元；"
                f"目标金额: {item['target_value']:.2f} 元"
            )
    else:
        lines.append("- 无")
    lines.append(f"预计剩余现金: {estimated_cash:.2f} 元")
    lines.extend(sell_notes)
    lines.extend(unknown_value_notes)

    lines.append("")
    lines.append("当前排名与指标:")
    if ranks.empty:
        lines.append("- 指标不足，无法排名")
    else:
        for _, row in ranks.iterrows():
            above_text = "是" if bool(row["above_ma"]) else "否"
            lines.append(
                f"- 第{int(row['rank'])}名 {row['symbol']} {row['name']}: "
                f"20日涨跌幅 {_format_pct(row['momentum'])}, "
                f"高于60日均线: {above_text}, "
                f"收盘价 {row['close']:.4f}, 60日均线 {row['ma']:.4f}"
            )

    lines.append("")
    lines.append("操作原因:")
    if not sell_symbols and not buy_advice:
        lines.append("- 当前真实持仓与系统目标基本一致，或现金不足以按交易单位买入。")
    for symbol, reason in signal["keep_reasons"].items():
        if symbol in target:
            lines.append(f"- 继续持有 {symbol}: {reason}")
    if target and enable_lot_rounding:
        lines.append(f"- 买入建议已按 A 股 ETF 最小交易单位 {lot_size} 份取整，剩余现金保留。")

    lines.append("")
    lines.append("风险提示:")
    lines.append("- 本项目不构成投资建议，信号只用于小资金人工验证。")
    lines.append("- 回测不代表未来收益，ETF 可能出现连续回撤、流动性变化和跟踪误差。")
    lines.append("- 第一版不自动下单；实盘下单前请手动确认价格、份额、手续费和账户可用资金。")

    text = "\n".join(lines) + "\n"
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
