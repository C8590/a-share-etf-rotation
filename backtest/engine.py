from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from backtest.metrics import calculate_drawdown, calculate_performance, save_performance
from backtest.portfolio import (
    FeeConfig,
    Portfolio,
    Position,
    affordable_buy_notional,
    calculate_trade_cost,
)
from data.storage import build_price_matrix
from strategy.equal_weight import EqualWeightMonthlyStrategy
from strategy.etf_rotation import ETFRotationStrategy, MomentumRotationMonthlyStrategy, StrategyConfig, get_rebalance_dates
from strategy.reduced_equal_weight import ReducedEqualWeightMonthlyStrategy


class BacktestEngine:
    def __init__(
        self,
        market_data: dict[str, pd.DataFrame],
        etf_pool: list[dict[str, str]],
        strategy_config: StrategyConfig,
        fee_config: FeeConfig,
        initial_cash: float,
        execution_price: str = "open",
        signal_weekday: int = 4,
        lot_size: int = 100,
        enable_lot_rounding: bool = True,
        min_effective_etf_count: int = 5,
        max_drawdown_stop: float | None = None,
    ):
        self.market_data = market_data
        self.etf_pool = etf_pool
        self.etf_info = {item["symbol"]: item for item in etf_pool}
        self.strategy_config = strategy_config
        self.fee_config = fee_config
        self.initial_cash = float(initial_cash)
        self.execution_price = execution_price
        self.signal_weekday = signal_weekday
        self.lot_size = int(lot_size)
        self.enable_lot_rounding = bool(enable_lot_rounding)
        self.min_effective_etf_count = int(min_effective_etf_count)
        self.max_drawdown_stop = max_drawdown_stop

        self.close = build_price_matrix(market_data, "close")
        self.open = build_price_matrix(market_data, "open")
        self.amount = build_price_matrix(market_data, "amount")
        self.valuation_close = self.close.ffill()
        if strategy_config.strategy_type == "equal_weight_monthly":
            self.strategy = EqualWeightMonthlyStrategy(self.close, self.etf_info, selected_symbols=strategy_config.selected_symbols)
        elif strategy_config.strategy_type == "reduced_equal_weight_monthly":
            self.strategy = ReducedEqualWeightMonthlyStrategy(self.close, self.etf_info, selected_symbols=strategy_config.selected_symbols)
        elif strategy_config.strategy_type == "momentum_rotation_monthly":
            self.strategy = MomentumRotationMonthlyStrategy(self.close, self.etf_info, strategy_config, amount=self.amount)
        else:
            self.strategy = ETFRotationStrategy(self.close, self.etf_info, strategy_config, amount=self.amount)

    def run(self, output_dir: str | Path = "output", save_outputs: bool = True) -> dict[str, Any]:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        portfolio = Portfolio(cash=self.initial_cash, fee_config=self.fee_config)
        signal_dates = set(
            get_rebalance_dates(
                self.close.index,
                frequency=self.strategy_config.rebalance_frequency,
                signal_weekday=self.signal_weekday,
                rebalance_timing=self.strategy_config.rebalance_timing,
                rebalance_day=self.strategy_config.rebalance_day,
                rebalance_day_of_month=self.strategy_config.rebalance_day_of_month,
                rebalance_roll=self.strategy_config.rebalance_roll,
            )
        )
        all_dates = list(self.close.index)
        pending_signal: dict[str, Any] | None = None
        equity_rows: list[dict[str, Any]] = []
        trade_rows: list[dict[str, Any]] = []

        for i, date in enumerate(all_dates):
            if pending_signal is not None and pending_signal["execute_date"] == date:
                trade_rows.extend(self._execute_rebalance(date, pending_signal, portfolio))
                pending_signal = None

            close_prices = self.valuation_close.loc[date]
            equity_rows.append(
                {
                    "date": date,
                    "cash": portfolio.cash,
                    "market_value": portfolio.market_value(close_prices),
                    "equity": portfolio.total_equity(close_prices),
                    "holding_symbols": ",".join(portfolio.current_symbols()),
                    "holdings": portfolio.format_positions(self.etf_info),
                }
            )

            if date in signal_dates and i + 1 < len(all_dates):
                current_holdings = portfolio.current_symbols()
                signal = self.strategy.generate_target(date, current_holdings)
                signal = self._apply_portfolio_drawdown_stop(signal, equity_rows)
                target = signal["target"]
                should_rebalance = set(target) != set(current_holdings) or self.strategy_config.strategy_type in {
                    "equal_weight_monthly",
                    "reduced_equal_weight_monthly",
                    "momentum_rotation_monthly",
                }
                if should_rebalance:
                    pending_signal = {
                        "signal": signal,
                        "execute_date": all_dates[i + 1],
                    }

        equity_curve = pd.DataFrame(equity_rows).set_index("date")
        trades = pd.DataFrame(trade_rows)
        if trades.empty:
            trades = pd.DataFrame(columns=self._trade_columns())

        performance = calculate_performance(
            equity_curve=equity_curve,
            trades=trades,
            current_holdings=self._holding_names(portfolio.current_symbols()),
            initial_cash=self.initial_cash,
        )
        self._attach_data_quality_flags(performance)

        if save_outputs:
            self._save_outputs(equity_curve, trades, performance, output_path)
        return {
            "equity_curve": equity_curve,
            "trades": trades,
            "performance": performance,
            "portfolio": portfolio,
            "strategy": self.strategy,
        }

    def _execute_rebalance(
        self,
        execute_date: pd.Timestamp,
        pending_signal: dict[str, Any],
        portfolio: Portfolio,
    ) -> list[dict[str, Any]]:
        signal = pending_signal["signal"]
        target = list(signal["target"])
        prices = self.open.loc[execute_date] if self.execution_price == "open" else self.close.loc[execute_date]
        before_positions = portfolio.format_positions(self.etf_info)
        rows: list[dict[str, Any]] = []

        current = portfolio.current_symbols()
        sell_symbols = [symbol for symbol in current if symbol not in target]
        for symbol in sell_symbols:
            rows.append(
                self._sell(
                    execute_date,
                    symbol,
                    portfolio.positions[symbol].shares,
                    prices,
                    portfolio,
                    signal["sell_reasons"].get(symbol, "调出目标组合"),
                    before_positions,
                )
            )

        if not target:
            return rows

        equity_after_sells = portfolio.total_equity(self._safe_valuation_prices(execute_date, prices))
        target_value = equity_after_sells / len(target)

        # Trim overweight survivors before buying new ETFs, so buys do not push cash negative.
        for symbol in target:
            pos = portfolio.positions.get(symbol)
            price = prices.get(symbol)
            if pos is None or pd.isna(price):
                continue
            current_value = pos.shares * float(price)
            if current_value > target_value * 1.01:
                shares_to_sell = (current_value - target_value) / float(price)
                rows.append(
                    self._sell(
                        execute_date,
                        symbol,
                        shares_to_sell,
                        prices,
                        portfolio,
                        "目标仓位变化，按等权原则减仓",
                        before_positions,
                    )
                )

        for symbol in target:
            price = prices.get(symbol)
            if pd.isna(price):
                rows.append(
                    self._skip_trade(
                        execute_date,
                        symbol,
                        "BUY",
                        "下一交易日缺少成交价格，按规则跳过该 ETF 交易",
                        before_positions,
                        portfolio,
                    )
                )
                continue

            current_value = 0.0
            pos = portfolio.positions.get(symbol)
            if pos is not None:
                current_value = pos.shares * float(price)
            buy_delta = max(target_value - current_value, 0.0)
            shares, notional = self._calculate_buy_order(buy_delta, float(price), portfolio.cash)
            if shares <= 1e-8 or notional <= 1e-8:
                continue
            rows.append(
                self._buy(
                    execute_date,
                    symbol,
                    notional,
                    shares,
                    float(price),
                    portfolio,
                    signal["buy_reasons"].get(symbol, "调入目标组合"),
                    before_positions,
                    target_value,
                )
            )

        return rows

    def _apply_portfolio_drawdown_stop(
        self,
        signal: dict[str, Any],
        equity_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.max_drawdown_stop is None or self.max_drawdown_stop <= 0 or not equity_rows:
            return signal
        equity = pd.Series([row["equity"] for row in equity_rows], dtype=float)
        running_max = equity.cummax().iloc[-1]
        current_equity = equity.iloc[-1]
        if running_max <= 0:
            return signal
        current_drawdown = current_equity / running_max - 1.0
        if current_drawdown > -abs(self.max_drawdown_stop):
            return signal

        defensive_target: list[str] = []
        if self.strategy_config.enable_cash_etf_fallback and self.strategy_config.cash_etf_symbol in self.close.columns:
            defensive_target = [self.strategy_config.cash_etf_symbol]

        current = signal.get("target", [])
        sell_reasons = dict(signal.get("sell_reasons", {}))
        for symbol in current:
            if symbol not in defensive_target:
                sell_reasons[symbol] = f"Portfolio drawdown exceeded {abs(self.max_drawdown_stop):.0%}; switch to defensive target"
        signal = dict(signal)
        signal["target"] = defensive_target
        signal["sell_reasons"] = sell_reasons
        signal["drawdown_stop_triggered"] = True
        return signal

    def _buy(
        self,
        date: pd.Timestamp,
        symbol: str,
        notional: float,
        shares: float,
        price: float,
        portfolio: Portfolio,
        reason: str,
        before_positions: str,
        target_value: float,
    ) -> dict[str, Any]:
        costs = calculate_trade_cost(notional, "buy", self.fee_config)
        cash_out = notional + costs["total_cost"]
        if cash_out > portfolio.cash + 1e-8:
            raise RuntimeError(f"{symbol} 买入后现金会为负，已阻止成交")
        portfolio.cash -= cash_out

        old = portfolio.positions.get(symbol)
        if old is None:
            portfolio.positions[symbol] = Position(shares=shares, avg_cost=cash_out / shares)
        else:
            total_cost = old.avg_cost * old.shares + cash_out
            total_shares = old.shares + shares
            portfolio.positions[symbol] = Position(shares=total_shares, avg_cost=total_cost / total_shares)

        return self._trade_row(
            date=date,
            action="BUY",
            symbol=symbol,
            price=price,
            notional=notional,
            costs=costs,
            reason=reason,
            before_positions=before_positions,
            after_positions=portfolio.format_positions(self.etf_info),
            realized_pnl=0.0,
            target_value=target_value,
            shares=shares,
            remaining_cash=portfolio.cash,
        )

    def _sell(
        self,
        date: pd.Timestamp,
        symbol: str,
        shares: float,
        prices: pd.Series,
        portfolio: Portfolio,
        reason: str,
        before_positions: str,
    ) -> dict[str, Any]:
        price = prices.get(symbol)
        if pd.isna(price):
            return self._skip_trade(
                date,
                symbol,
                "SELL",
                "下一交易日缺少成交价格，按规则跳过该 ETF 交易",
                before_positions,
                portfolio,
            )

        pos = portfolio.positions[symbol]
        shares = min(shares, pos.shares)
        notional = shares * float(price)
        costs = calculate_trade_cost(notional, "sell", self.fee_config)
        cash_in = notional - costs["total_cost"]
        portfolio.cash += cash_in
        realized_pnl = cash_in - pos.avg_cost * shares

        remaining = pos.shares - shares
        if remaining <= 1e-10:
            del portfolio.positions[symbol]
        else:
            portfolio.positions[symbol] = Position(shares=remaining, avg_cost=pos.avg_cost)

        return self._trade_row(
            date=date,
            action="SELL",
            symbol=symbol,
            price=float(price),
            notional=notional,
            costs=costs,
            reason=reason,
            before_positions=before_positions,
            after_positions=portfolio.format_positions(self.etf_info),
            realized_pnl=realized_pnl,
            target_value=0.0,
            shares=shares,
            remaining_cash=portfolio.cash,
        )

    def _skip_trade(
        self,
        date: pd.Timestamp,
        symbol: str,
        intended_action: str,
        reason: str,
        before_positions: str,
        portfolio: Portfolio,
    ) -> dict[str, Any]:
        return self._trade_row(
            date=date,
            action=f"SKIP_{intended_action}",
            symbol=symbol,
            price=float("nan"),
            notional=0.0,
            costs={"commission": 0.0, "stamp_tax": 0.0, "slippage": 0.0, "total_cost": 0.0},
            reason=reason,
            before_positions=before_positions,
            after_positions=portfolio.format_positions(self.etf_info),
            realized_pnl=0.0,
            target_value=0.0,
            shares=0.0,
            remaining_cash=portfolio.cash,
        )

    def _trade_row(
        self,
        date: pd.Timestamp,
        action: str,
        symbol: str,
        price: float,
        notional: float,
        costs: dict[str, float],
        reason: str,
        before_positions: str,
        after_positions: str,
        realized_pnl: float,
        target_value: float,
        shares: float,
        remaining_cash: float,
    ) -> dict[str, Any]:
        name = self.etf_info.get(symbol, {}).get("name", symbol)
        return {
            "date": str(date.date()),
            "action": action,
            "symbol": symbol,
            "name": name,
            "买入ETF": f"{symbol} {name}" if action in ["BUY", "SKIP_BUY"] else "",
            "卖出ETF": f"{symbol} {name}" if action in ["SELL", "SKIP_SELL"] else "",
            "成交价格": price,
            "成交金额": float(notional),
            "理论目标金额": float(target_value),
            "实际成交份额": float(shares),
            "实际成交金额": float(notional),
            "手续费": float(costs["commission"]),
            "滑点": float(costs["slippage"]),
            "印花税": float(costs["stamp_tax"]),
            "调仓原因": reason,
            "调仓前持仓": before_positions,
            "调仓后持仓": after_positions,
            "剩余现金": float(remaining_cash),
            "realized_pnl": float(realized_pnl),
            "notional": float(notional),
        }

    def _calculate_buy_order(self, target_notional: float, price: float, cash: float) -> tuple[float, float]:
        if target_notional <= 0 or price <= 0 or cash <= 0:
            return 0.0, 0.0

        affordable_notional = affordable_buy_notional(target_notional, cash, self.fee_config)
        if not self.enable_lot_rounding:
            return affordable_notional / price, affordable_notional

        raw_shares = affordable_notional / price
        shares = np.floor(raw_shares / self.lot_size) * self.lot_size
        while shares >= self.lot_size:
            notional = float(shares * price)
            costs = calculate_trade_cost(notional, "buy", self.fee_config)
            if notional + costs["total_cost"] <= cash + 1e-8:
                return float(shares), notional
            shares -= self.lot_size
        return 0.0, 0.0

    def _attach_data_quality_flags(self, performance: dict[str, Any]) -> None:
        effective_count = len(self.market_data)
        is_complete = effective_count >= self.min_effective_etf_count
        warning = ""
        if not is_complete:
            warning = "当前有效ETF数量不足，结果仅用于流程测试，不代表策略有效性。"
        performance["effective_etf_count"] = effective_count
        performance["min_effective_etf_count"] = self.min_effective_etf_count
        performance["is_complete_backtest"] = is_complete
        performance["data_quality_warning"] = warning
        performance["warning"] = warning
        performance["test_only"] = not is_complete

    def _safe_valuation_prices(self, execute_date: pd.Timestamp, trade_prices: pd.Series) -> pd.Series:
        prices = self.valuation_close.loc[execute_date].copy()
        for symbol, value in trade_prices.items():
            if pd.notna(value):
                prices.loc[symbol] = value
        return prices

    def _save_outputs(
        self,
        equity_curve: pd.DataFrame,
        trades: pd.DataFrame,
        performance: dict[str, Any],
        output_path: Path,
    ) -> None:
        equity_curve.to_csv(output_path / "equity_curve.csv", encoding="utf-8-sig")
        trades.to_csv(output_path / "trades.csv", index=False, encoding="utf-8-sig")
        save_performance(performance, output_path / "performance.json")

        plt.figure(figsize=(11, 5))
        plt.plot(equity_curve.index, equity_curve["equity"], label="Equity")
        plt.title("ETF Rotation Equity Curve")
        plt.xlabel("Date")
        plt.ylabel("Portfolio Value")
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_path / "equity_curve.png", dpi=150)
        plt.close()

        drawdown = calculate_drawdown(equity_curve["equity"])
        plt.figure(figsize=(11, 5))
        plt.plot(drawdown.index, drawdown, label="Drawdown", color="#b23a48")
        plt.title("ETF Rotation Drawdown Curve")
        plt.xlabel("Date")
        plt.ylabel("Drawdown")
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_path / "drawdown_curve.png", dpi=150)
        plt.close()

    def _holding_names(self, symbols: list[str]) -> list[str]:
        return [f"{symbol} {self.etf_info.get(symbol, {}).get('name', symbol)}" for symbol in symbols]

    @staticmethod
    def _trade_columns() -> list[str]:
        return [
            "date",
            "action",
            "symbol",
            "name",
            "买入ETF",
            "卖出ETF",
            "成交价格",
            "成交金额",
            "理论目标金额",
            "实际成交份额",
            "实际成交金额",
            "手续费",
            "滑点",
            "印花税",
            "调仓原因",
            "调仓前持仓",
            "调仓后持仓",
            "剩余现金",
            "realized_pnl",
            "notional",
        ]
