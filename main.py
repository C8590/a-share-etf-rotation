from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from analysis.performance import summarize_equity
from analysis.reports import build_monthly_returns, build_trade_diagnostics, build_yearly_returns
from backtest.engine import BacktestEngine
from backtest.portfolio import FeeConfig
from benchmark.report import build_benchmark_report
from data.downloader import build_data_coverage_report, load_etf_pool, update_all_data
from data.quality import run_data_quality_checks
from data.storage import load_market_data
from signal.weekly_signal import ensure_current_position, generate_weekly_signal_text
from strategy.review import build_strategy_review, strategy_status
from strategy.etf_rotation import StrategyConfig, get_rebalance_dates


STRATEGY_CONFIGS = {
    "original": "config/strategy_original.yaml",
    "conservative": "config/strategy_conservative.yaml",
    "balanced": "config/strategy_balanced.yaml",
    "equal_weight_monthly": "config/strategy_equal_weight_monthly.yaml",
    "reduced_equal_weight_monthly": "config/strategy_reduced_equal_weight_monthly.yaml",
}


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _strategy_int(raw: dict[str, Any], primary: str, fallback: str | None, default: int) -> int:
    value = raw.get(primary)
    if value is None and fallback is not None:
        value = raw.get(fallback)
    return int(default if value is None else value)


def _strategy_float_or_none(raw: dict[str, Any], key: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    return float(value)


def load_strategy_settings(config_path: str | Path = "config/strategy.yaml") -> tuple[dict[str, Any], dict[str, Any], StrategyConfig]:
    config = load_yaml(config_path)
    backtest_cfg = config.get("backtest", {})
    raw = config.get("strategy", {})
    strategy_cfg = StrategyConfig(
        strategy_type=str(raw.get("strategy_type", "rotation")),
        momentum_period=_strategy_int(raw, "momentum_period", "momentum_window", 20),
        ma_period=_strategy_int(raw, "ma_period", "ma_window", 60),
        max_positions=int(raw.get("max_positions", 2)),
        sell_rank_threshold=int(raw.get("sell_rank_threshold", 4)),
        rebalance_frequency=str(raw.get("frequency", raw.get("rebalance_frequency", "weekly"))),
        rebalance_timing=str(raw.get("rebalance_timing") or "month_end"),
        rebalance_day=None if raw.get("rebalance_day") is None else int(raw.get("rebalance_day")),
        rebalance_day_of_month=None if raw.get("rebalance_day_of_month") is None else int(raw.get("rebalance_day_of_month")),
        rebalance_roll=str(raw.get("rebalance_roll") or "next"),
        enable_market_filter=bool(raw.get("enable_market_filter", False)),
        market_filter_symbol=str(raw.get("market_filter_symbol", "510300")),
        market_filter_ma_window=int(raw.get("market_filter_ma_window", 200)),
        enable_cash_etf_fallback=bool(raw.get("enable_cash_etf_fallback", False)),
        cash_etf_symbol=str(raw.get("cash_etf_symbol", "511880")),
        min_momentum_threshold=_strategy_float_or_none(raw, "min_momentum_threshold"),
        max_industry_etf_weight=_strategy_float_or_none(raw, "max_industry_etf_weight"),
        selected_symbols=tuple(str(symbol).zfill(6) for symbol in (raw.get("selected_symbols") or [])),
    )
    return backtest_cfg, raw, strategy_cfg


def load_fee_config(config_path: str | Path | None = None) -> FeeConfig:
    config: dict[str, Any] = {}
    if config_path is not None:
        strategy_file = load_yaml(config_path)
        config = strategy_file.get("fee_config", {}) or {}
    if not config:
        config = load_yaml("config/fee.yaml").get("fee", {})
    return FeeConfig(
        commission_rate=float(config.get("commission_rate", 0.00005)),
        min_commission=float(config.get("min_commission", 0.1)),
        stamp_tax_rate=float(config.get("stamp_tax_rate", 0.0)),
        slippage_rate=float(config.get("slippage_rate", 0.00005)),
    )


def build_engine(
    config_path: str | Path = "config/strategy.yaml",
    strategy_config: StrategyConfig | None = None,
    raw_strategy_cfg: dict[str, Any] | None = None,
    market_data: dict[str, pd.DataFrame] | None = None,
    etf_pool: list[dict[str, str]] | None = None,
) -> BacktestEngine:
    etf_pool = etf_pool or load_etf_pool()
    symbols = [item["symbol"] for item in etf_pool]
    market_data = market_data or load_market_data(symbols, allow_partial=True, etf_info={item["symbol"]: item for item in etf_pool})
    backtest_cfg, loaded_raw, loaded_strategy = load_strategy_settings(config_path)
    raw = raw_strategy_cfg or loaded_raw
    strategy = strategy_config or loaded_strategy
    fee_cfg = load_fee_config(config_path)

    return BacktestEngine(
        market_data=market_data,
        etf_pool=etf_pool,
        strategy_config=strategy,
        fee_config=fee_cfg,
        initial_cash=float(backtest_cfg.get("initial_cash", 10000)),
        execution_price=str(raw.get("execution_price", "open")),
        signal_weekday=int(raw.get("signal_weekday", 4)),
        lot_size=int(raw.get("lot_size", 100)),
        enable_lot_rounding=bool(raw.get("enable_lot_rounding", True)),
        min_effective_etf_count=int(raw.get("min_effective_etf_count", 5)),
        max_drawdown_stop=_strategy_float_or_none(raw, "max_drawdown_stop"),
    )


def _load_market_context() -> tuple[list[dict[str, str]], dict[str, pd.DataFrame]]:
    etf_pool = load_etf_pool()
    market_data = load_market_data(
        [item["symbol"] for item in etf_pool],
        allow_partial=True,
        etf_info={item["symbol"]: item for item in etf_pool},
    )
    return etf_pool, market_data


def _slice_market_data(
    market_data: dict[str, pd.DataFrame],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    sliced = {}
    for symbol, df in market_data.items():
        part = df.loc[(df.index >= start_ts) & (df.index <= end_ts)].copy()
        if len(part) >= 2:
            sliced[symbol] = part
    return sliced


def _market_date_bounds(market_data: dict[str, pd.DataFrame]) -> tuple[pd.Timestamp, pd.Timestamp]:
    starts = [df.index.min() for df in market_data.values() if not df.empty]
    ends = [df.index.max() for df in market_data.values() if not df.empty]
    return min(starts), max(ends)


def _run_strategy_on_range(
    config_path: str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    etf_pool: list[dict[str, str]],
    market_data: dict[str, pd.DataFrame],
    strategy_config: StrategyConfig | None = None,
    raw_strategy_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sliced = _slice_market_data(market_data, start, end)
    if not sliced:
        raise ValueError(f"No market data in range {start} to {end}")
    engine = build_engine(
        config_path=config_path,
        strategy_config=strategy_config,
        raw_strategy_cfg=raw_strategy_cfg,
        market_data=sliced,
        etf_pool=etf_pool,
    )
    return engine.run(output_dir="output", save_outputs=False)


def _run_buy_hold_on_range(
    market_data: dict[str, pd.DataFrame],
    symbol: str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    initial_cash: float = 10000,
) -> tuple[dict[str, float], int]:
    sliced = _slice_market_data({symbol: market_data[symbol]}, start, end)
    if symbol not in sliced:
        return summarize_equity(pd.Series(dtype=float)), 0
    price = sliced[symbol]["close"].dropna()
    equity = (initial_cash / price.iloc[0]) * price
    return summarize_equity(equity), 0


def _metric_row(prefix: str, perf: dict[str, Any]) -> dict[str, Any]:
    return {
        f"total_return_{prefix}": perf["total_return"],
        f"annual_return_{prefix}": perf["annual_return"],
        f"max_drawdown_{prefix}": perf["max_drawdown"],
        f"sharpe_{prefix}": perf.get("sharpe_ratio", perf.get("sharpe", 0.0)),
        f"calmar_{prefix}": perf.get("calmar_ratio", perf.get("calmar", 0.0)),
    }


def print_data_status(statuses: list[Any]) -> None:
    success = [item for item in statuses if item.success]
    failed = [item for item in statuses if not item.success]
    print("Data coverage report: output/data_coverage_report.csv")
    print(f"Successful ETF: {len(success)}, failed ETF: {len(failed)}")
    if success:
        print("Success list:")
        for item in success:
            cache_text = "cache" if item.cached else "download"
            print(f"  OK  {item.symbol} {item.name}: rows={item.rows}, {item.start_date}->{item.end_date}, source={item.source}, {cache_text}, status={item.status}")
    if failed:
        print("Failure list:")
        for item in failed:
            print(f"  ERR {item.symbol} {item.name}: {item.error}")


def command_update_data(refresh: bool = False, config_path: str = "config/strategy.yaml") -> None:
    etf_pool = load_etf_pool()
    backtest_cfg, _, _ = load_strategy_settings(config_path)
    successes, errors, statuses = update_all_data(
        etf_pool=etf_pool,
        start_date=str(backtest_cfg.get("start_date", "20190101")),
        end_date=backtest_cfg.get("end_date"),
        refresh=refresh,
        retry_failed_only=False,
    )
    print("数据更新完成:" if not errors else "数据更新未完成:")
    print_data_status(statuses)
    if errors:
        raise SystemExit(1)


def command_retry_failed_data(config_path: str = "config/strategy.yaml") -> None:
    etf_pool = load_etf_pool()
    backtest_cfg, _, _ = load_strategy_settings(config_path)
    successes, errors, statuses = update_all_data(
        etf_pool=etf_pool,
        start_date=str(backtest_cfg.get("start_date", "20190101")),
        end_date=backtest_cfg.get("end_date"),
        refresh=True,
        retry_failed_only=True,
    )
    print("失败数据重试完成:")
    print_data_status(statuses)
    if errors and not successes:
        raise SystemExit(1)


def command_data_report() -> list[Any]:
    statuses = build_data_coverage_report(load_etf_pool())
    print_data_status(statuses)
    return statuses


def command_qa_data() -> Any:
    etf_pool = load_etf_pool()
    statuses = build_data_coverage_report(etf_pool)
    gate = run_data_quality_checks(etf_pool)
    print_data_status(statuses)
    print("Data QA:")
    print(f"  effective_etf_count: {gate.effective_etf_count}")
    print(f"  latest_date: {gate.latest_date}")
    print(f"  allow_formal: {gate.allow_formal}")
    if gate.reasons:
        print("  blocking_reasons:")
        for reason in gate.reasons:
            print(f"  - {reason}")
    else:
        print("  status: passed")
    return gate


def command_backtest(config_path: str = "config/strategy.yaml", save_outputs: bool = True) -> dict[str, Any]:
    engine = build_engine(config_path=config_path)
    result = engine.run(output_dir="output", save_outputs=save_outputs)
    gate = run_data_quality_checks(load_etf_pool())
    if not gate.allow_formal:
        warning = "test_only: data gate failed; " + "; ".join(gate.reasons)
        result["performance"]["is_complete_backtest"] = False
        result["performance"]["test_only"] = True
        result["performance"]["warning"] = warning
        result["performance"]["data_quality_warning"] = warning
        if save_outputs:
            from backtest.metrics import save_performance

            save_performance(result["performance"], Path("output") / "performance.json")
    perf = result["performance"]
    print("回测完成:")
    if perf.get("data_quality_warning"):
        print(f"  数据质量提示: {perf['data_quality_warning']}")
    print(f"  使用配置: {config_path}")
    print(f"  有效 ETF 数量: {perf['effective_etf_count']}/{perf['min_effective_etf_count']}")
    print(f"  总收益率: {perf['total_return']:.2%}")
    print(f"  年化收益率: {perf['annual_return']:.2%}")
    print(f"  最大回撤: {perf['max_drawdown']:.2%}")
    print(f"  夏普比率: {perf['sharpe_ratio']:.2f}")
    print(f"  Calmar比率: {perf['calmar_ratio']:.2f}")
    print(f"  交易次数: {perf['trade_count']}")
    print("  输出目录: output/")
    return result


def command_signal(config_path: str = "config/strategy.yaml") -> None:
    engine = build_engine(config_path=config_path)
    result = engine.run(output_dir="output")
    _, raw, _ = load_strategy_settings(config_path)
    text = generate_weekly_signal_text(
        strategy=result["strategy"],
        equity_curve=result["equity_curve"],
        etf_info=engine.etf_info,
        signal_weekday=int(raw.get("signal_weekday", 4)),
        output_path="output/weekly_signal.txt",
        current_position_path="config/current_position.yaml",
        fee_config=load_fee_config(),
        lot_size=int(raw.get("lot_size", 100)),
        enable_lot_rounding=bool(raw.get("enable_lot_rounding", True)),
        effective_etf_count=len(engine.market_data),
        min_effective_etf_count=int(raw.get("min_effective_etf_count", 5)),
        rebalance_frequency=str(raw.get("frequency", raw.get("rebalance_frequency", "weekly"))),
        rebalance_timing=engine.strategy_config.rebalance_timing,
        rebalance_day=engine.strategy_config.rebalance_day,
        rebalance_day_of_month=engine.strategy_config.rebalance_day_of_month,
        rebalance_roll=engine.strategy_config.rebalance_roll,
    )
    print("周信号已生成: output/weekly_signal.txt")
    print(text)


def command_benchmark(config_path: str = "config/strategy.yaml") -> pd.DataFrame:
    result = command_backtest(config_path=config_path, save_outputs=True)
    engine = build_engine(config_path=config_path)
    extra: dict[str, pd.Series] = {}
    for strategy_name in ["balanced", "conservative", "equal_weight_monthly", "reduced_equal_weight_monthly"]:
        try:
            cfg = STRATEGY_CONFIGS[strategy_name]
            strategy_result = command_backtest(config_path=cfg, save_outputs=False)
            extra[strategy_name] = strategy_result["equity_curve"]["equity"]
        except Exception as exc:  # noqa: BLE001
            print(f"Benchmark skipped {strategy_name}: {exc}")
    report = build_benchmark_report(
        close=engine.close,
        strategy_equity=result["equity_curve"]["equity"],
        initial_cash=engine.initial_cash,
        output_dir="output",
        extra_benchmarks=extra,
    )
    print("基准对比已生成: output/benchmark_report.csv, output/benchmark_report.json")
    print(report[["benchmark", "annual_return", "max_drawdown", "calmar_ratio", "sharpe_ratio"]].to_string(index=False))
    return report


def _experiment_strategy(
    base: StrategyConfig,
    momentum: int,
    ma_window: int,
    max_positions: int,
    sell_rank: int,
    frequency: str,
) -> StrategyConfig:
    return StrategyConfig(
        strategy_type=base.strategy_type,
        momentum_period=momentum,
        ma_period=ma_window,
        max_positions=max_positions,
        sell_rank_threshold=sell_rank,
        rebalance_frequency=frequency,
        rebalance_timing=base.rebalance_timing,
        rebalance_day=base.rebalance_day,
        rebalance_day_of_month=base.rebalance_day_of_month,
        rebalance_roll=base.rebalance_roll,
        enable_market_filter=base.enable_market_filter,
        market_filter_symbol=base.market_filter_symbol,
        market_filter_ma_window=base.market_filter_ma_window,
        enable_cash_etf_fallback=base.enable_cash_etf_fallback,
        cash_etf_symbol=base.cash_etf_symbol,
        min_momentum_threshold=base.min_momentum_threshold,
        max_industry_etf_weight=base.max_industry_etf_weight,
    )


def command_experiment(config_path: str = "config/strategy.yaml") -> pd.DataFrame:
    etf_pool = load_etf_pool()
    market_data = load_market_data(
        [item["symbol"] for item in etf_pool],
        allow_partial=True,
        etf_info={item["symbol"]: item for item in etf_pool},
    )
    _, raw, base_strategy = load_strategy_settings(config_path)
    rows = []
    combos = list(product([20, 40, 60], [60, 120, 200], [1, 2], [3, 4, 5], ["weekly", "biweekly", "monthly"]))
    total = len(combos)
    for idx, (momentum, ma_window, max_positions, sell_rank, frequency) in enumerate(combos, start=1):
        if idx == 1 or idx % 20 == 0 or idx == total:
            print(f"参数实验进度: {idx}/{total}")
        strategy = _experiment_strategy(base_strategy, momentum, ma_window, max_positions, sell_rank, frequency)
        engine = build_engine(
            config_path=config_path,
            strategy_config=strategy,
            raw_strategy_cfg=raw,
            market_data=market_data,
            etf_pool=etf_pool,
        )
        result = engine.run(output_dir="output", save_outputs=False)
        perf = result["performance"]
        rows.append(
            {
                "momentum_window": momentum,
                "ma_window": ma_window,
                "max_positions": max_positions,
                "sell_rank_threshold": sell_rank,
                "rebalance_frequency": frequency,
                "total_return": perf["total_return"],
                "annual_return": perf["annual_return"],
                "max_drawdown": perf["max_drawdown"],
                "sharpe": perf["sharpe_ratio"],
                "calmar": perf["calmar_ratio"],
                "trade_count": perf["trade_count"],
                "yearly_turnover": perf["annual_turnover"],
            }
        )

    result_df = pd.DataFrame(rows)
    result_df = result_df.sort_values(
        by=["max_drawdown", "calmar", "sharpe", "annual_return"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)
    result_df.to_csv("output/experiment_results.csv", index=False, encoding="utf-8-sig")
    print("参数实验已生成: output/experiment_results.csv")
    print(result_df.head(10).to_string(index=False))
    return result_df


def command_analyze(config_path: str = "config/strategy.yaml") -> None:
    result = command_backtest(config_path=config_path, save_outputs=True)
    engine = build_engine(config_path=config_path)
    build_benchmark_report(engine.close, result["equity_curve"]["equity"], engine.initial_cash, "output")
    build_yearly_returns(result["equity_curve"], result["trades"], "output")
    build_monthly_returns(result["equity_curve"], "output")
    diagnostics, summary = build_trade_diagnostics(result["trades"], engine.close, "output")
    command_experiment(config_path=config_path)
    print("完整分析已生成:")
    print("  output/performance.json")
    print("  output/benchmark_report.csv")
    print("  output/yearly_returns.csv")
    print("  output/monthly_returns.csv")
    print("  output/monthly_returns_heatmap.png")
    print("  output/trade_diagnostics.csv")
    print("  output/experiment_results.csv")
    if summary:
        print("交易诊断摘要:")
        print(f"  诊断交易数: {len(diagnostics)}")
        print(f"  收益贡献前列: {list(summary['profit_contributors'].items())[:3]}")
        print(f"  亏损拖累前列: {list(summary['loss_contributors'].items())[:3]}")


def _oos_windows(actual_start: pd.Timestamp, actual_end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    raw_windows = [
        ("2015-01-01", "2020-12-31", "2021-01-01", "2022-12-31"),
        ("2016-01-01", "2021-12-31", "2022-01-01", "2023-12-31"),
        ("2017-01-01", "2022-12-31", "2023-01-01", "2024-12-31"),
        ("2018-01-01", "2023-12-31", "2024-01-01", actual_end),
    ]
    windows = []
    for train_start, train_end, test_start, test_end in raw_windows:
        ts = max(pd.Timestamp(train_start), actual_start)
        te = min(pd.Timestamp(train_end), actual_end)
        vs = max(pd.Timestamp(test_start), actual_start)
        ve = min(pd.Timestamp(test_end), actual_end)
        if ts <= te and vs <= ve:
            windows.append((ts, te, vs, ve))
    return windows


def command_oos_test() -> pd.DataFrame:
    etf_pool, market_data = _load_market_context()
    actual_start, actual_end = _market_date_bounds(market_data)
    rows: list[dict[str, Any]] = []
    for train_start, train_end, test_start, test_end in _oos_windows(actual_start, actual_end):
        print(f"OOS window train {train_start.date()}->{train_end.date()}, test {test_start.date()}->{test_end.date()}")
        for strategy_name, config_path in STRATEGY_CONFIGS.items():
            try:
                train_result = _run_strategy_on_range(config_path, train_start, train_end, etf_pool, market_data)
                test_result = _run_strategy_on_range(config_path, test_start, test_end, etf_pool, market_data)
                row = {
                    "train_start": str(train_start.date()),
                    "train_end": str(train_end.date()),
                    "test_start": str(test_start.date()),
                    "test_end": str(test_end.date()),
                    "strategy_name": strategy_name,
                    **_metric_row("train", train_result["performance"]),
                    **_metric_row("test", test_result["performance"]),
                    "test_trade_count": test_result["performance"]["trade_count"],
                }
                rows.append(row)
            except Exception as exc:  # noqa: BLE001 - keep the report complete
                rows.append(
                    {
                        "train_start": str(train_start.date()),
                        "train_end": str(train_end.date()),
                        "test_start": str(test_start.date()),
                        "test_end": str(test_end.date()),
                        "strategy_name": strategy_name,
                        "error": str(exc),
                    }
                )

        for strategy_name, symbol in [("buy_hold_510300", "510300"), ("cash_etf_511880", "511880")]:
            train_stats, _ = _run_buy_hold_on_range(market_data, symbol, train_start, train_end)
            test_stats, test_trades = _run_buy_hold_on_range(market_data, symbol, test_start, test_end)
            rows.append(
                {
                    "train_start": str(train_start.date()),
                    "train_end": str(train_end.date()),
                    "test_start": str(test_start.date()),
                    "test_end": str(test_end.date()),
                    "strategy_name": strategy_name,
                    **_metric_row("train", train_stats),
                    **_metric_row("test", test_stats),
                    "test_trade_count": test_trades,
                }
            )

    result = pd.DataFrame(rows)
    result.to_csv("output/oos_results.csv", index=False, encoding="utf-8-sig")
    print("样本外验证已生成: output/oos_results.csv")
    print(result[["test_start", "test_end", "strategy_name", "annual_return_test", "max_drawdown_test", "calmar_test"]].to_string(index=False))
    return result


def _rank_experiment_rows(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    max_dd = f"{prefix}max_drawdown" if prefix else "max_drawdown"
    calmar = f"{prefix}calmar" if prefix else "calmar"
    sharpe = f"{prefix}sharpe" if prefix else "sharpe"
    annual = f"{prefix}annual_return" if prefix else "annual_return"
    trades = f"{prefix}trade_count" if prefix else "trade_count"
    return df.sort_values(
        by=[max_dd, calmar, sharpe, annual, trades],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)


def _parameter_pool() -> list[tuple[int, int, int, int, str]]:
    return list(product([20, 40, 60], [60, 120, 200], [1, 2], [3, 4, 5], ["weekly", "biweekly", "monthly"]))


def _evaluate_parameter_pool(
    etf_pool: list[dict[str, str]],
    market_data: dict[str, pd.DataFrame],
    train_start: pd.Timestamp,
    train_end: pd.Timestamp,
    raw: dict[str, Any],
    base_strategy: StrategyConfig,
) -> pd.DataFrame:
    rows = []
    combos = _parameter_pool()
    for momentum, ma_window, max_positions, sell_rank, frequency in combos:
        strategy = _experiment_strategy(base_strategy, momentum, ma_window, max_positions, sell_rank, frequency)
        try:
            result = _run_strategy_on_range(
                "config/strategy.yaml",
                train_start,
                train_end,
                etf_pool,
                market_data,
                strategy_config=strategy,
                raw_strategy_cfg=raw,
            )
            perf = result["performance"]
            rows.append(
                {
                    "momentum_window": momentum,
                    "ma_window": ma_window,
                    "max_positions": max_positions,
                    "sell_rank_threshold": sell_rank,
                    "rebalance_frequency": frequency,
                    "annual_return": perf["annual_return"],
                    "max_drawdown": perf["max_drawdown"],
                    "sharpe": perf["sharpe_ratio"],
                    "calmar": perf["calmar_ratio"],
                    "trade_count": perf["trade_count"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(
                {
                    "momentum_window": momentum,
                    "ma_window": ma_window,
                    "max_positions": max_positions,
                    "sell_rank_threshold": sell_rank,
                    "rebalance_frequency": frequency,
                    "error": str(exc),
                }
            )
    df = pd.DataFrame(rows)
    df = df.dropna(subset=["max_drawdown", "calmar", "sharpe", "annual_return"])
    return _rank_experiment_rows(df)


def command_walk_forward() -> pd.DataFrame:
    etf_pool, market_data = _load_market_context()
    actual_start, actual_end = _market_date_bounds(market_data)
    _, raw, base_strategy = load_strategy_settings("config/strategy.yaml")
    rows = []
    windows = _oos_windows(actual_start, actual_end)
    for idx, (train_start, train_end, test_start, test_end) in enumerate(windows, start=1):
        print(f"Walk-forward window {idx}/{len(windows)}: train {train_start.date()}->{train_end.date()}, test {test_start.date()}->{test_end.date()}")
        ranked = _evaluate_parameter_pool(etf_pool, market_data, train_start, train_end, raw, base_strategy)
        if ranked.empty:
            continue
        chosen = ranked.iloc[0]
        strategy = _experiment_strategy(
            base_strategy,
            int(chosen["momentum_window"]),
            int(chosen["ma_window"]),
            int(chosen["max_positions"]),
            int(chosen["sell_rank_threshold"]),
            str(chosen["rebalance_frequency"]),
        )
        test_result = _run_strategy_on_range(
            "config/strategy.yaml",
            test_start,
            test_end,
            etf_pool,
            market_data,
            strategy_config=strategy,
            raw_strategy_cfg=raw,
        )
        test_perf = test_result["performance"]
        rows.append(
            {
                "train_start": str(train_start.date()),
                "train_end": str(train_end.date()),
                "test_start": str(test_start.date()),
                "test_end": str(test_end.date()),
                "selected_momentum_window": int(chosen["momentum_window"]),
                "selected_ma_window": int(chosen["ma_window"]),
                "selected_max_positions": int(chosen["max_positions"]),
                "selected_sell_rank_threshold": int(chosen["sell_rank_threshold"]),
                "selected_rebalance_frequency": str(chosen["rebalance_frequency"]),
                "train_annual_return": float(chosen["annual_return"]),
                "train_max_drawdown": float(chosen["max_drawdown"]),
                "train_sharpe": float(chosen["sharpe"]),
                "train_calmar": float(chosen["calmar"]),
                "test_total_return": test_perf["total_return"],
                "test_annual_return": test_perf["annual_return"],
                "test_max_drawdown": test_perf["max_drawdown"],
                "test_sharpe": test_perf["sharpe_ratio"],
                "test_calmar": test_perf["calmar_ratio"],
                "test_trade_count": test_perf["trade_count"],
            }
        )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            by=["test_max_drawdown", "test_calmar", "test_sharpe", "test_annual_return", "test_trade_count"],
            ascending=[False, False, False, False, True],
        ).reset_index(drop=True)
    result.to_csv("output/walk_forward_results.csv", index=False, encoding="utf-8-sig")
    print("Walk-forward 分析已生成: output/walk_forward_results.csv")
    if not result.empty:
        param_cols = [
            "selected_momentum_window",
            "selected_ma_window",
            "selected_max_positions",
            "selected_sell_rank_threshold",
            "selected_rebalance_frequency",
        ]
        stability = "stable" if all(result[col].nunique(dropna=True) <= 1 for col in param_cols) else "unstable"
        result["parameter_stability"] = stability
        result.to_csv("output/walk_forward_results.csv", index=False, encoding="utf-8-sig")
        print(result.to_string(index=False))
    return result


def _ensure_output_file(path: str, builder: Any) -> None:
    if not Path(path).exists():
        builder()


def _strategy_qa_rows(etf_pool: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    reasons: list[str] = []
    for strategy_name, config_path in STRATEGY_CONFIGS.items():
        exists = Path(config_path).exists()
        row: dict[str, Any] = {
            "strategy_name": strategy_name,
            "config_path": config_path,
            "config_exists": exists,
            "target_positions_ok": False,
            "minimal_backtest_ok": False,
            "future_leakage_check": "not_checked",
            "fee_slippage_lot_check": "not_checked",
            "strategy_status": strategy_status(strategy_name),
        }
        if not exists:
            reasons.append(f"missing strategy config: {config_path}")
            rows.append(row)
            continue
        try:
            engine = build_engine(config_path=config_path)
            dates = list(engine.close.index)
            signal_dates = get_rebalance_dates(
                engine.close.index,
                engine.strategy_config.rebalance_frequency,
                engine.signal_weekday,
                rebalance_timing=engine.strategy_config.rebalance_timing,
                rebalance_day=engine.strategy_config.rebalance_day,
                rebalance_day_of_month=engine.strategy_config.rebalance_day_of_month,
                rebalance_roll=engine.strategy_config.rebalance_roll,
            )
            signal_date = signal_dates[-2] if len(signal_dates) >= 2 else signal_dates[-1]
            execute_idx = dates.index(signal_date) + 1
            execute_date = dates[execute_idx] if execute_idx < len(dates) else None
            target = engine.strategy.generate_target_positions(signal_date, execute_date, [], strategy_name=strategy_name)
            row["target_positions_ok"] = isinstance(target.get("target_positions"), list)
            row["future_leakage_check"] = "passed" if execute_date is not None and pd.Timestamp(execute_date) > pd.Timestamp(signal_date) and engine.execution_price == "open" else "failed"
            row["fee_slippage_lot_check"] = (
                "passed"
                if engine.fee_config.commission_rate > 0
                and engine.fee_config.slippage_rate > 0
                and engine.lot_size == 100
                and engine.enable_lot_rounding
                else "failed"
            )
            result = engine.run(output_dir="output", save_outputs=False)
            row["minimal_backtest_ok"] = not result["equity_curve"].empty
            if row["future_leakage_check"] == "failed":
                reasons.append(f"{strategy_name} failed signal_date/execute_date separation check")
            if row["fee_slippage_lot_check"] == "failed":
                reasons.append(f"{strategy_name} fee/slippage/lot settings are incomplete")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"{strategy_name} QA failed: {exc}")
            row["error"] = str(exc)
        rows.append(row)
    return rows, reasons


def command_qa_check() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    etf_pool = load_etf_pool()

    data_statuses = build_data_coverage_report(etf_pool)
    data_gate = run_data_quality_checks(etf_pool)
    data_passed = data_gate.allow_formal

    strategy_rows, strategy_reasons = _strategy_qa_rows(etf_pool)
    strategy_passed = not strategy_reasons

    output_builders = {
        "output/performance.json": lambda: command_backtest("config/strategy_equal_weight_monthly.yaml"),
        "output/benchmark_report.csv": command_benchmark,
        "output/oos_results.csv": command_oos_test,
        "output/walk_forward_results.csv": command_walk_forward,
        "output/compare_signal.txt": command_compare_signal,
    }
    output_reasons: list[str] = []
    for path, builder in output_builders.items():
        try:
            _ensure_output_file(path, builder)
        except Exception as exc:  # noqa: BLE001
            output_reasons.append(f"failed to generate {path}: {exc}")
    output_passed = all(Path(path).exists() for path in output_builders) and not output_reasons

    review = build_strategy_review(output_dir=output_path)
    recommended = review[review["strategy_status"] == "recommended_for_observation"]["strategy_name"].tolist()
    rejected = review[review["strategy_status"].isin(["rejected", "research_only"])]["strategy_name"].tolist()
    defensive = review[review["strategy_status"] == "defensive_only"]["strategy_name"].tolist()

    blocking_reasons = list(data_gate.reasons) + strategy_reasons + output_reasons
    allow_observation = data_passed and strategy_passed and output_passed
    report = {
        "data_layer": {
            "passed": data_passed,
            "effective_etf_count": data_gate.effective_etf_count,
            "latest_date": data_gate.latest_date,
            "reasons": data_gate.reasons,
            "coverage_report": "output/data_coverage_report.csv",
            "quality_report": "output/data_quality_report.csv",
        },
        "strategy_layer": {
            "passed": strategy_passed,
            "checks": strategy_rows,
            "reasons": strategy_reasons,
        },
        "output_layer": {
            "passed": output_passed,
            "reasons": output_reasons,
            "required_files": list(output_builders),
        },
        "allow_small_observation": allow_observation,
        "blocking_reasons": blocking_reasons,
        "recommended_for_observation": recommended,
        "not_recommended": rejected,
        "defensive_only": defensive,
        "risk_note": "Research output only; no automatic trading or broker API execution is enabled.",
    }

    import json

    with (output_path / "qa_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    lines = [
        "QA CHECK REPORT",
        "=" * 40,
        f"Data layer: {'PASSED' if data_passed else 'FAILED'}",
        f"Strategy layer: {'PASSED' if strategy_passed else 'FAILED'}",
        f"Output layer: {'PASSED' if output_passed else 'FAILED'}",
        f"Allow 1000-3000 CNY small observation: {'YES' if allow_observation else 'NO'}",
        "",
        "Blocking reasons:",
    ]
    lines.extend([f"- {reason}" for reason in blocking_reasons] or ["- None"])
    lines.extend(
        [
            "",
            "Recommended observation strategies:",
            "- " + ", ".join(recommended) if recommended else "- None",
            "Not recommended strategies:",
            "- " + ", ".join(rejected) if rejected else "- None",
            "Defensive-only strategies:",
            "- " + ", ".join(defensive) if defensive else "- None",
            "",
            "Risk note:",
            "- Research output only. Do not use as an investment recommendation, automatic trading signal, or broker execution instruction.",
        ]
    )
    (output_path / "qa_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("QA report generated: output/qa_report.txt, output/qa_report.json")
    print("\n".join(lines))
    if not allow_observation:
        raise SystemExit(1)
    return report


def _rebalance_rule_text(strategy_config: StrategyConfig) -> str:
    if strategy_config.rebalance_frequency != "monthly":
        return strategy_config.rebalance_frequency
    if strategy_config.rebalance_timing == "day_of_month":
        roll_text = {
            "next": "非交易日 next（顺延到下一个交易日）",
            "previous": "非交易日 previous（提前到上一个交易日）",
            "nearest": "非交易日 nearest（选择最近交易日，同距离优先 previous）",
        }.get(strategy_config.rebalance_roll, f"非交易日 {strategy_config.rebalance_roll}")
        return f"monthly / day_of_month / 每月 {strategy_config.rebalance_day_of_month} 号 / {roll_text}"
    if strategy_config.rebalance_timing == "nth_trading_day":
        return f"monthly / nth_trading_day({strategy_config.rebalance_day})"
    return f"monthly / {strategy_config.rebalance_timing}"


def _resolve_effective_signal_date(dates: pd.DatetimeIndex, requested_signal_date: str | None) -> tuple[pd.Timestamp, str, str]:
    all_dates = pd.DatetimeIndex(sorted(pd.to_datetime(dates).unique()))
    if all_dates.empty:
        raise ValueError("No market dates are available")
    if not requested_signal_date:
        return pd.NaT, "", ""

    requested = pd.Timestamp(requested_signal_date).normalize()
    usable = all_dates[all_dates <= requested]
    if usable.empty:
        raise ValueError(f"requested signal date {requested.date()} is before available market data")
    effective = pd.Timestamp(usable[-1])
    date_list = list(all_dates)
    idx = date_list.index(effective)
    execute_date = date_list[idx + 1] if idx + 1 < len(date_list) else None
    return effective, str(requested.date()), str(execute_date.date()) if execute_date is not None else ""


def _latest_strategy_signal(
    config_path: str,
    strategy_name: str,
    output_path: str | Path,
    requested_signal_date: str | None = None,
) -> tuple[str, dict[str, Any]]:
    engine = build_engine(config_path=config_path)
    result = engine.run(output_dir="output", save_outputs=False)
    _, raw, _ = load_strategy_settings(config_path)
    manual_signal_date, requested_date_text, execute_date_text = _resolve_effective_signal_date(
        result["strategy"].close.index,
        requested_signal_date,
    )
    text = generate_weekly_signal_text(
        strategy=result["strategy"],
        equity_curve=result["equity_curve"],
        etf_info=engine.etf_info,
        signal_weekday=int(raw.get("signal_weekday", 4)),
        output_path=output_path,
        current_position_path="config/current_position.yaml",
        fee_config=load_fee_config(),
        lot_size=int(raw.get("lot_size", 100)),
        enable_lot_rounding=bool(raw.get("enable_lot_rounding", True)),
        effective_etf_count=len(engine.market_data),
        min_effective_etf_count=int(raw.get("min_effective_etf_count", 5)),
        rebalance_frequency=str(raw.get("frequency", raw.get("rebalance_frequency", "weekly"))),
        rebalance_timing=engine.strategy_config.rebalance_timing,
        rebalance_day=engine.strategy_config.rebalance_day,
        rebalance_day_of_month=engine.strategy_config.rebalance_day_of_month,
        rebalance_roll=engine.strategy_config.rebalance_roll,
        signal_date=None if pd.isna(manual_signal_date) else manual_signal_date,
    )
    if pd.isna(manual_signal_date):
        signal_dates = get_rebalance_dates(
            result["strategy"].close.index,
            str(raw.get("frequency", raw.get("rebalance_frequency", "weekly"))),
            int(raw.get("signal_weekday", 4)),
            rebalance_timing=engine.strategy_config.rebalance_timing,
            rebalance_day=engine.strategy_config.rebalance_day,
            rebalance_day_of_month=engine.strategy_config.rebalance_day_of_month,
            rebalance_roll=engine.strategy_config.rebalance_roll,
        )
        signal_date = signal_dates[-1]
        date_list = list(result["strategy"].close.index)
        idx = date_list.index(signal_date)
        execute_date_text = str(date_list[idx + 1].date()) if idx + 1 < len(date_list) else ""
    else:
        signal_date = manual_signal_date
    current_position = ensure_current_position("config/current_position.yaml")
    current_holdings = [
        str(symbol).zfill(6)
        for symbol, item in (current_position.get("positions", {}) or {}).items()
        if float(item.get("shares", 0)) > 0
    ]
    signal = result["strategy"].generate_target(signal_date, current_holdings)
    target = list(signal["target"])
    sells = [symbol for symbol in current_holdings if symbol not in target]
    buys = [symbol for symbol in target if symbol not in current_holdings]
    buy_lines = []
    skipped_buy_lines = []
    sell_lines = []
    estimated_cash = ""
    for line in text.splitlines():
        if line.startswith("- ") and ("预计买入" in line or "预计成交金额" in line):
            buy_lines.append(line[2:])
        if line.startswith("- 跳过ETF "):
            skipped_buy_lines.append(line[2:])
        if line.startswith("- ") and ("全部卖出" in line):
            sell_lines.append(line[2:])
        if line.startswith("预计剩余现金:"):
            estimated_cash = line.replace("预计剩余现金:", "").strip()
    summary = {
        "strategy_name": strategy_name,
        "strategy_status": strategy_status(strategy_name),
        "config_path": config_path,
        "rebalance_frequency": engine.strategy_config.rebalance_frequency,
        "rebalance_timing": engine.strategy_config.rebalance_timing,
        "rebalance_day": engine.strategy_config.rebalance_day,
        "rebalance_day_of_month": engine.strategy_config.rebalance_day_of_month,
        "rebalance_roll": engine.strategy_config.rebalance_roll,
        "rebalance_rule": _rebalance_rule_text(engine.strategy_config),
        "requested_signal_date": requested_date_text,
        "effective_signal_date": str(signal_date.date()),
        "execute_date": execute_date_text,
        "signal_date": str(signal_date.date()),
        "latest_data_date": str(result["strategy"].close.index.max().date()),
        "current_cash": float(current_position.get("cash", 0)),
        "current_positions": ",".join(current_holdings) if current_holdings else "空仓",
        "target_symbols": ",".join(target) if target else "空仓",
        "suggested_sell": ",".join(sells) if sells else "无",
        "suggested_buy": ",".join(buys) if buys else "无",
        "buy_share_advice": " | ".join(buy_lines) if buy_lines else "无",
        "skipped_buy_advice": " | ".join(skipped_buy_lines) if skipped_buy_lines else "无",
        "sell_advice": " | ".join(sell_lines) if sell_lines else "无",
        "estimated_remaining_cash": estimated_cash,
        "operation_reason": "详见 compare_signal.txt 中对应策略段落。",
        "risk_note": "仅用于人工观察，不构成投资建议。",
    }
    return text, summary


def _load_small_observation_status() -> str:
    qa_path = Path("output/qa_report.json")
    if qa_path.exists():
        try:
            report = json.loads(qa_path.read_text(encoding="utf-8"))
            allowed = bool(report.get("allow_small_observation"))
            return "YES" if allowed else "NO"
        except Exception as exc:  # noqa: BLE001
            return f"UNKNOWN (qa_report.json 读取失败: {exc})"
    review = build_strategy_review(output_dir="output")
    main_row = review[review["strategy_name"] == "reduced_equal_weight_monthly"]
    if not main_row.empty and str(main_row.iloc[0]["strategy_status"]) == "recommended_for_observation":
        return "UNKNOWN (未找到 qa_report.json；主观察策略评估为 recommended_for_observation)"
    return "NO (未找到 qa_report.json，且主观察策略未通过推荐评估)"


def _current_position_overview(current_position: dict[str, Any]) -> str:
    positions = current_position.get("positions", {}) or {}
    holdings = [
        f"{str(symbol).zfill(6)}:{float(item.get('shares', 0)):.0f}份"
        for symbol, item in positions.items()
        if float(item.get("shares", 0)) > 0
    ]
    return "，".join(holdings) if holdings else "空仓"


def _compare_signal_overview(rows: list[dict[str, Any]]) -> str:
    signal_dates = [row["signal_date"] for row in rows if row.get("signal_date")]
    latest_dates = [row["latest_data_date"] for row in rows if row.get("latest_data_date")]
    signal_date = signal_dates[0] if signal_dates else "UNKNOWN"
    latest_data_date = max(latest_dates) if latest_dates else "UNKNOWN"
    current_position = ensure_current_position("config/current_position.yaml")
    allowed = _load_small_observation_status()
    main_rule = next((row.get("rebalance_rule") for row in rows if row.get("strategy_name") == "reduced_equal_weight_monthly"), "UNKNOWN")
    lines = [
        "四策略对照观察信号",
        "=" * 40,
        "总览",
        f"- 当前信号日期: {signal_date}",
        f"- 数据最新日期: {latest_data_date}",
        f"- 当前真实现金: {float(current_position.get('cash', 0)):.2f} 元",
        f"- 当前真实持仓: {_current_position_overview(current_position)}",
        "- 主观察策略: reduced_equal_weight_monthly",
        f"- 调仓规则：{main_rule}",
        f"- 是否允许小额观察: {allowed}",
    ]
    if signal_date != latest_data_date:
        lines.extend(
            [
                "",
                "当前信号日期不是最新交易日，请确认数据是否已更新或当前是否非调仓日。",
            ]
        )
    lines.extend(
        [
            "",
            "安全边界:",
            "- 本工具不自动下单，不连接券商，不替代人工判断。",
            "- 小资金观察建议仍为 1000-3000 元。",
            "- balanced 仅为 research_only，不作为推荐策略。",
            "- conservative 仅为 defensive_only，不作为主策略。",
        ]
    )
    return "\n".join(lines)


def command_compare_signal(signal_date: str | None = None) -> pd.DataFrame:
    compare_items = [
        ("reduced_equal_weight_monthly", "config/strategy_reduced_equal_weight_monthly.yaml"),
        ("equal_weight_monthly", "config/strategy_equal_weight_monthly.yaml"),
        ("balanced", "config/strategy_balanced.yaml"),
        ("conservative", "config/strategy_conservative.yaml"),
    ]
    sections = []
    rows = []
    for strategy_name, config_path in compare_items:
        text, summary = _latest_strategy_signal(
            config_path,
            strategy_name,
            f"output/{strategy_name}_signal.txt",
            requested_signal_date=signal_date,
        )
        sections.append(f"\n\n===== {strategy_name} ({summary['strategy_status']}) =====\n调仓规则：{summary['rebalance_rule']}\n{text}")
        rows.append(summary)
    combined = _compare_signal_overview(rows) + "\n".join(sections)
    Path("output/compare_signal.txt").write_text(combined, encoding="utf-8")
    result = pd.DataFrame(rows)
    result.to_csv("output/compare_signal.csv", index=False, encoding="utf-8-sig")
    print("四策略对照信号已生成: output/compare_signal.txt, output/compare_signal.csv")
    print(result.to_string(index=False))
    return result


def _strategy_config_from_observation(name: str) -> str:
    mapping = {
        "original": "config/strategy_original.yaml",
        "balanced": "config/strategy_balanced.yaml",
        "conservative": "config/strategy_conservative.yaml",
        "equal_weight_monthly": "config/strategy_equal_weight_monthly.yaml",
        "reduced_equal_weight_monthly": "config/strategy_reduced_equal_weight_monthly.yaml",
    }
    return mapping.get(name, "config/strategy_balanced.yaml")


def command_observation_report() -> str:
    observation_path = Path("config/live_observation.yaml")
    if not observation_path.exists():
        observation_path.write_text(
            yaml.safe_dump(
                {
                    "start_date": None,
                    "capital_observed": 1000,
                    "strategy_to_follow": "balanced",
                    "notes": "",
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    observation = load_yaml(observation_path)
    strategy_name = str(observation.get("strategy_to_follow", "balanced"))
    config_path = _strategy_config_from_observation(strategy_name)
    engine = build_engine(config_path=config_path)
    result = engine.run(output_dir="output", save_outputs=False)
    _, raw, _ = load_strategy_settings(config_path)
    signal_dates = get_rebalance_dates(
        result["strategy"].close.index,
        str(raw.get("frequency", raw.get("rebalance_frequency", "weekly"))),
        int(raw.get("signal_weekday", 4)),
        rebalance_timing=engine.strategy_config.rebalance_timing,
        rebalance_day=engine.strategy_config.rebalance_day,
        rebalance_day_of_month=engine.strategy_config.rebalance_day_of_month,
        rebalance_roll=engine.strategy_config.rebalance_roll,
    )
    signal_date = signal_dates[-1]
    current_position = ensure_current_position("config/current_position.yaml")
    positions = current_position.get("positions", {}) or {}
    cash = float(current_position.get("cash", 0))
    current_holdings = [str(symbol).zfill(6) for symbol, item in positions.items() if float(item.get("shares", 0)) > 0]
    signal = result["strategy"].generate_target(signal_date, current_holdings)
    target = list(signal["target"])
    latest_prices = result["strategy"].close.loc[signal_date]

    values: dict[str, float] = {}
    total_assets = cash
    for symbol, item in positions.items():
        symbol = str(symbol).zfill(6)
        price = latest_prices.get(symbol)
        shares = float(item.get("shares", 0))
        value = 0.0 if pd.isna(price) else shares * float(price)
        values[symbol] = value
        total_assets += value

    target_weight = 1 / len(target) if target else 0.0
    deviation_lines = []
    for symbol in sorted(set(current_holdings) | set(target)):
        actual_weight = values.get(symbol, 0.0) / total_assets if total_assets > 0 else 0.0
        expected_weight = target_weight if symbol in target else 0.0
        deviation_lines.append(
            f"- {symbol} {engine.etf_info.get(symbol, {}).get('name', symbol)}: 当前 {actual_weight:.1%}, 目标 {expected_weight:.1%}, 偏差 {actual_weight - expected_weight:+.1%}"
        )

    need_rebalance = set(current_holdings) != set(target)
    previous_note = "未找到上一份 compare_signal.csv，无法比较。"
    compare_path = Path("output/compare_signal.csv")
    if compare_path.exists():
        try:
            previous = pd.read_csv(compare_path)
            row = previous[previous["strategy_name"] == strategy_name]
            if not row.empty:
                previous_target = str(row.iloc[0].get("target_symbols", ""))
                previous_note = "未发生明显变化。" if previous_target == (",".join(target) if target else "空仓") else f"目标从 {previous_target} 变为 {','.join(target) if target else '空仓'}。"
        except Exception as exc:  # noqa: BLE001
            previous_note = f"读取上一份 compare_signal.csv 失败: {exc}"

    lines = [
        "实盘观察报告",
        "=" * 32,
        f"当前观察策略: {strategy_name}",
        f"配置文件: {config_path}",
        f"观察开始日期: {observation.get('start_date')}",
        f"观察资金: {observation.get('capital_observed', 1000)}",
        f"当前信号日期: {signal_date.date()}",
        "",
        f"当前现金: {cash:.2f} 元",
        f"当前总资产估算: {total_assets:.2f} 元",
        "当前真实持仓:",
    ]
    if current_holdings:
        for symbol in current_holdings:
            item = positions.get(symbol, {})
            lines.append(f"- {symbol} {item.get('name', engine.etf_info.get(symbol, {}).get('name', symbol))}: {float(item.get('shares', 0)):.0f} 份，估算市值 {values.get(symbol, 0.0):.2f} 元")
    else:
        lines.append("- 空仓")
    lines.extend(["", "策略目标持仓:", f"- {','.join(target) if target else '空仓'}", "", "仓位偏差:"])
    lines.extend(deviation_lines if deviation_lines else ["- 无持仓偏差"])
    lines.extend(
        [
            "",
            f"是否需要调仓: {'是' if need_rebalance else '否'}",
            f"过去一次信号到现在是否发生明显变化: {previous_note}",
            "",
            "风险提示: 本报告只用于人工观察，不构成投资建议，不自动下单。",
        ]
    )
    text = "\n".join(lines) + "\n"
    Path("output/observation_report.txt").write_text(text, encoding="utf-8")
    print("观察报告已生成: output/observation_report.txt")
    print(text)
    return text


def command_run_all(refresh: bool = False, config_path: str = "config/strategy.yaml") -> None:
    command_update_data(refresh=refresh, config_path=config_path)
    command_backtest(config_path=config_path)
    command_signal(config_path=config_path)
    print("全部流程完成: 数据、回测、周信号均已更新")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股ETF低频轮动系统")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update-data", help="更新 ETF 历史数据")
    update_parser.add_argument("--refresh", action="store_true", help="忽略已有缓存，强制重新下载")
    update_parser.add_argument("--config", default="config/strategy.yaml")

    retry_parser = subparsers.add_parser("retry-failed-data", help="只重试覆盖报告中失败或缺失的 ETF")
    retry_parser.add_argument("--config", default="config/strategy.yaml")

    subparsers.add_parser("data-report", help="扫描本地缓存并生成数据覆盖报告")
    subparsers.add_parser("qa-data", help="运行数据质量检查和数据闸门")

    backtest_parser = subparsers.add_parser("backtest", help="运行回测")
    backtest_parser.add_argument("--config", default="config/strategy.yaml")

    signal_parser = subparsers.add_parser("signal", help="生成最新周信号")
    signal_parser.add_argument("--config", default="config/strategy.yaml")

    run_all_parser = subparsers.add_parser("run-all", help="更新数据、回测并生成周信号")
    run_all_parser.add_argument("--refresh", action="store_true", help="run-all 时强制刷新数据")
    run_all_parser.add_argument("--config", default="config/strategy.yaml")

    benchmark_parser = subparsers.add_parser("benchmark", help="生成基准对比")
    benchmark_parser.add_argument("--config", default="config/strategy.yaml")

    experiment_parser = subparsers.add_parser("experiment", help="运行参数对比实验")
    experiment_parser.add_argument("--config", default="config/strategy.yaml")

    analyze_parser = subparsers.add_parser("analyze", help="生成完整诊断分析")
    analyze_parser.add_argument("--config", default="config/strategy.yaml")
    subparsers.add_parser("oos-test", help="运行样本外验证")
    subparsers.add_parser("walk-forward", help="运行 walk-forward 参数稳定性分析")
    subparsers.add_parser("qa-check", help="运行数据层、策略层和输出层总质量检查")
    compare_parser = subparsers.add_parser("compare-signal", help="生成三策略对照信号")
    compare_parser.add_argument("--signal-date", default=None, help="Manual requested signal date, YYYY-MM-DD")
    subparsers.add_parser("observation-report", help="生成实盘观察报告")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "update-data":
        command_update_data(refresh=args.refresh, config_path=args.config)
    elif args.command == "retry-failed-data":
        command_retry_failed_data(config_path=args.config)
    elif args.command == "data-report":
        command_data_report()
    elif args.command == "qa-data":
        command_qa_data()
    elif args.command == "backtest":
        command_backtest(config_path=args.config)
    elif args.command == "signal":
        command_signal(config_path=args.config)
    elif args.command == "run-all":
        command_run_all(refresh=args.refresh, config_path=args.config)
    elif args.command == "benchmark":
        command_benchmark(config_path=args.config)
    elif args.command == "experiment":
        command_experiment(config_path=args.config)
    elif args.command == "analyze":
        command_analyze(config_path=args.config)
    elif args.command == "oos-test":
        command_oos_test()
    elif args.command == "walk-forward":
        command_walk_forward()
    elif args.command == "qa-check":
        command_qa_check()
    elif args.command == "compare-signal":
        command_compare_signal(signal_date=args.signal_date)
    elif args.command == "observation-report":
        command_observation_report()


if __name__ == "__main__":
    main()
