from __future__ import annotations

import argparse
import json
import time as time_module
from datetime import datetime, time
from itertools import product
from collections import Counter
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from analysis.performance import summarize_equity
from analysis.reports import build_trade_diagnostics, build_yearly_returns
from backtest.engine import BacktestEngine
from backtest.portfolio import FeeConfig
from benchmark.report import build_benchmark_report
from data.downloader import build_data_coverage_report, load_etf_pool, update_all_data
from data.quality import run_data_quality_checks
from data.storage import load_market_data, normalize_symbol
from data.trading_calendar import (
    SignalContext,
    get_market_phase,
    get_next_trading_day,
    load_a_share_trading_calendar,
    resolve_signal_context,
)
from signal.control_data_foundation import write_v1_v2_comparison
from signal.daily_signal import build_signal_trade_plan, ensure_current_position, generate_daily_signal_text, run_modular_signal_pipeline
from risk_warning.cli import add_risk_subparser, handle_risk_command
from risk_warning.gate import apply_risk_gate
from risk_warning.learning_adapter import get_learning_risk_context
from risk_warning.scorer import calculate_next_day_risk, write_risk_outputs
from strategy.review import build_strategy_review, strategy_status
from strategy.etf_rotation import StrategyConfig, get_rebalance_dates


PENDING_EXECUTE_DATE_TEXT = "下一交易日，待数据确认"
MARKET_TZ = ZoneInfo("Asia/Shanghai")


CORE_STRATEGY_NAME = "日频右侧确认型 ETF 动量轮动策略"
SIGNAL_VERSION_V1 = "V1_LEGACY"
SIGNAL_VERSION_V2 = "V2_MODULAR"
CORE_STRATEGY_CONFIG = "config/strategy.yaml"
STRATEGY_CONFIGS = {CORE_STRATEGY_NAME: CORE_STRATEGY_CONFIG}

STRATEGY_DISPLAY_NAMES = {CORE_STRATEGY_NAME: CORE_STRATEGY_NAME}

STRATEGY_TYPE_DESCRIPTIONS = {
    CORE_STRATEGY_NAME: (
        "当前策略属于右侧确认型趋势跟随策略，不预测启动点，也不做左侧埋伏。"
        "系统通过日 K 动量、趋势形态、成交活跃度和相对强弱确认 ETF 已经走强后，再给出交易建议。"
    )
}

EXECUTION_WINDOW = "09:35 - 10:00"
EXECUTION_PRICE_RULE = "人工限价单，参考实时盘口，不自动下单；回测假设为下一交易日开盘价模拟成交。"


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
    strategy_type = "daily_confirm_momentum_rotation"
    strategy_cfg = StrategyConfig(
        strategy_type=strategy_type,
        momentum_period=_strategy_int(raw, "momentum_period", "momentum_window", 20),
        ma_period=_strategy_int(raw, "ma_period", "ma_window", 60),
        max_positions=int(raw.get("max_positions", 2)),
        sell_rank_threshold=int(raw.get("sell_rank_threshold", 4)),
        rebalance_frequency=str(raw.get("frequency", raw.get("rebalance_frequency", "daily"))),
        rebalance_timing=str(raw.get("rebalance_timing") or "month_end"),
        rebalance_day=None if raw.get("rebalance_day") is None else int(raw.get("rebalance_day")),
        rebalance_day_of_month=None if raw.get("rebalance_day_of_month") is None else int(raw.get("rebalance_day_of_month")),
        rebalance_roll=str(raw.get("rebalance_roll") or "next"),
        enable_market_filter=bool(raw.get("enable_market_filter", False)),
        market_filter_symbol=str(raw.get("market_filter_symbol", "510300")),
        market_filter_ma_window=int(raw.get("market_filter_ma_window", 200)),
        enable_cash_etf_fallback=bool(raw.get("enable_cash_etf_fallback", False)),
        cash_etf_symbol=str(raw.get("cash_etf_symbol", "511880")),
        enable_trend_filter=bool(raw.get("enable_trend_filter", True)),
        enable_min_momentum_filter=bool(raw.get("enable_min_momentum_filter", False)),
        min_momentum_threshold=_strategy_float_or_none(raw, "min_momentum_threshold"),
        max_industry_etf_weight=_strategy_float_or_none(raw, "max_industry_etf_weight"),
        selected_symbols=tuple(str(symbol).zfill(6) for symbol in (raw.get("selected_symbols") or [])),
        enable_universe_filter=bool(raw.get("enable_universe_filter", True)),
        min_trading_days=int(raw.get("min_trading_days", 120)),
        avg_amount_window=int(raw.get("avg_amount_window", 20)),
        min_avg_amount=float(raw.get("min_avg_amount", 20_000_000)),
        min_data_completeness=float(raw.get("min_data_completeness", 0.95)),
        max_stale_days=int(raw.get("max_stale_days", 7)),
        max_zero_amount_days=int(raw.get("max_zero_amount_days", 0)),
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
    latest_dates = [str(item.latest_date) for item in success if getattr(item, "latest_date", "")]
    latest_local_date = max(latest_dates) if latest_dates else "N/A"
    print("Data coverage report: output/data_coverage_report.csv")
    print(f"ETF universe total: {len(statuses)}")
    print(f"Successful ETF: {len(success)}, failed ETF: {len(failed)}")
    print(f"Latest local date: {latest_local_date}")
    if success:
        print("Success list (first 30):")
        for item in success[:30]:
            cache_text = "cache" if item.cached else "download"
            print(f"  OK  {item.symbol} {item.name}: rows={item.rows}, {item.start_date}->{item.end_date}, source={item.source}, {cache_text}, status={item.status}")
        if len(success) > 30:
            print(f"  ... {len(success) - 30} more successful/skipped ETF(s)")
    if failed:
        print("Failure list:")
        for item in failed:
            print(f"  ERR {item.symbol} {item.name}: {item.error}")

def _emit_progress(progress_callback: Any, **payload: Any) -> None:
    if progress_callback:
        progress_callback(payload)


def _count_statuses(statuses: list[Any]) -> dict[str, int]:
    cached_success_count = sum(1 for item in statuses if item.success and getattr(item, "status", "") == "cached_success")
    up_to_date_count = sum(1 for item in statuses if item.success and getattr(item, "status", "") == "up_to_date")
    cold_start_count = sum(1 for item in statuses if item.success and getattr(item, "status", "") == "cold_start")
    skipped_count = sum(1 for item in statuses if item.success and getattr(item, "status", "") in {"skipped", "cold_start_deferred"})
    return {
        "processed_count": len(statuses),
        "cached_success_count": cached_success_count,
        "up_to_date_count": up_to_date_count,
        "cold_start_count": cold_start_count,
        "skipped_count": skipped_count,
        "success_count": sum(1 for item in statuses if item.success and getattr(item, "status", "") in {"success", "cold_start"}),
        "failed_count": sum(1 for item in statuses if not item.success),
    }


def _append_timing_log(metrics: dict[str, Any], path: str | Path = "logs/update_timing.log") -> None:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": datetime.now(MARKET_TZ).isoformat(timespec="seconds"), **metrics}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def command_update_data(
    mode: str = "incremental",
    symbols: str | None = None,
    max_count: int | None = None,
    max_workers: int = 8,
    refresh: bool = False,
    target_date: str | None = None,
    debug: bool = False,
    config_path: str = "config/strategy.yaml",
    progress_callback: Any = None,
    exit_on_all_failed: bool = True,
) -> dict[str, Any]:
    if refresh:
        mode = "incremental"
    selected_symbols = {normalize_symbol(item) for item in str(symbols or "").split(",") if normalize_symbol(item)}

    total_started = time_module.perf_counter()
    stage_started = time_module.perf_counter()
    _emit_progress(progress_callback, stage="读取 ETF 池", current=0, total=0)
    etf_pool = load_etf_pool()
    load_universe_seconds = time_module.perf_counter() - stage_started

    stage_started = time_module.perf_counter()
    _emit_progress(progress_callback, stage="检查本地缓存", current=0, total=len(etf_pool))
    config = load_yaml(config_path)
    backtest_cfg, _, _ = load_strategy_settings(config_path)
    update_cfg = (config.get("data_update", {}) or {})
    daily_cfg = (update_cfg.get("daily_incremental", {}) or {})
    full_cfg = (update_cfg.get("full_refresh", {}) or {})
    if mode in {"incremental", "refresh"}:
        max_workers = int(daily_cfg.get("max_workers", max_workers))
    elif mode in {"rebuild", "full_refresh"}:
        max_workers = int(full_cfg.get("max_workers", 3))
    check_cache_seconds = time_module.perf_counter() - stage_started

    stage_started = time_module.perf_counter()
    successes, errors, statuses = update_all_data(
        etf_pool=etf_pool,
        start_date=str(backtest_cfg.get("start_date", "20190101")),
        end_date=target_date or backtest_cfg.get("end_date"),
        refresh=(mode in {"refresh", "rebuild"}),
        retry_failed_only=False,
        mode=mode,
        symbols=selected_symbols or None,
        max_count=max_count,
        max_workers=max_workers,
        debug=debug,
        progress_callback=progress_callback,
    )
    download_seconds = time_module.perf_counter() - stage_started

    stage_started = time_module.perf_counter()
    _emit_progress(progress_callback, stage="校验数据质量", current=len(statuses), total=len(etf_pool), **_count_statuses(statuses))
    quality_rows = [
        {
            "symbol": item.symbol,
            "name": item.name,
            "status": item.status,
            "rows": item.rows,
            "start_date": item.start_date,
            "end_date": item.end_date,
            "missing_count": item.missing_count,
            "duplicate_count": item.duplicate_count,
            "errors": item.failure_reason,
            "warnings": item.filter_reason,
        }
        for item in statuses
    ]
    Path("output").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(quality_rows).to_csv("output/data_quality_report.csv", index=False, encoding="utf-8-sig")
    qa_allow_formal = not any(not item.success for item in statuses)
    qa_seconds = time_module.perf_counter() - stage_started

    counts = _count_statuses(statuses)
    latest_dates = [str(item.latest_date) for item in statuses if getattr(item, "latest_date", "")]
    latest_local_date = max(latest_dates) if latest_dates else ""
    target_dates = [str(item.target_update_date) for item in statuses if getattr(item, "target_update_date", "")]
    expected_signal_date = max(target_dates) if target_dates else str(target_date or backtest_cfg.get("end_date") or latest_local_date)
    total_seconds = time_module.perf_counter() - total_started
    metrics = {
        "mode": mode,
        "load_universe_seconds": round(load_universe_seconds, 3),
        "check_cache_seconds": round(check_cache_seconds, 3),
        "download_seconds": round(download_seconds, 3),
        "qa_seconds": round(qa_seconds, 3),
        "signal_seconds": 0.0,
        "total_seconds": round(total_seconds, 3),
        "latest_data_date": latest_local_date,
        "expected_signal_date": expected_signal_date,
        **counts,
    }
    _append_timing_log(metrics)
    _emit_progress(progress_callback, stage="完成", current=len(statuses), total=len(etf_pool), latest_data_date=latest_local_date, **counts)
    print("数据更新完成:" if not errors else "数据更新未完成:")
    print_data_status(statuses)
    if errors and not successes and exit_on_all_failed:
        raise SystemExit(1)
    metrics["qa_allow_formal"] = qa_allow_formal
    return metrics


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
    text = generate_daily_signal_text(
        strategy=result["strategy"],
        equity_curve=result["equity_curve"],
        etf_info=engine.etf_info,
        signal_weekday=int(raw.get("signal_weekday", 4)),
        output_path="output/daily_signal.txt",
        current_position_path="config/current_position.yaml",
        fee_config=load_fee_config(),
        lot_size=int(raw.get("lot_size", 100)),
        enable_lot_rounding=bool(raw.get("enable_lot_rounding", True)),
        effective_etf_count=len(engine.market_data),
        min_effective_etf_count=int(raw.get("min_effective_etf_count", 5)),
        rebalance_frequency=str(raw.get("frequency", raw.get("rebalance_frequency", "daily"))),
        rebalance_timing=engine.strategy_config.rebalance_timing,
        rebalance_day=engine.strategy_config.rebalance_day,
        rebalance_day_of_month=engine.strategy_config.rebalance_day_of_month,
        rebalance_roll=engine.strategy_config.rebalance_roll,
        market_data=engine.market_data,
    )
    print("日频信号已生成: output/daily_signal.txt")
    print(text)


def command_benchmark(config_path: str = "config/strategy.yaml") -> pd.DataFrame:
    result = command_backtest(config_path=config_path, save_outputs=True)
    engine = build_engine(config_path=config_path)
    report = build_benchmark_report(
        close=engine.close,
        strategy_equity=result["equity_curve"]["equity"],
        initial_cash=engine.initial_cash,
        output_dir="output",
        extra_benchmarks={},
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
        enable_min_momentum_filter=base.enable_min_momentum_filter,
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
    combos = list(product([20, 40, 60], [60, 120, 200], [1, 2], [3, 4, 5], ["daily"]))
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
    diagnostics, summary = build_trade_diagnostics(result["trades"], engine.close, "output")
    command_experiment(config_path=config_path)
    print("完整分析已生成:")
    print("  output/performance.json")
    print("  output/benchmark_report.csv")
    print("  output/yearly_returns.csv")
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
    return list(product([20, 40, 60], [60, 120, 200], [1, 2], [3, 4, 5], ["daily"]))


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
            engine = _build_signal_engine(config_path=config_path, use_cache=True)
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
            row["minimal_backtest_ok"] = True
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

    coverage_path = output_path / "data_coverage_report.csv"
    if not coverage_path.exists():
        build_data_coverage_report(etf_pool)
    coverage = pd.read_csv(coverage_path, dtype={"symbol": str}).fillna("") if coverage_path.exists() else pd.DataFrame()
    if not coverage.empty and "success" in coverage.columns:
        success_mask = coverage["success"].astype(str).str.lower().isin(["true", "1", "yes", "是"])
        effective_etf_count = int(success_mask.sum())
        latest_date = str(coverage.loc[success_mask, "latest_date"].max()) if "latest_date" in coverage.columns and success_mask.any() else ""
    else:
        effective_etf_count = len(etf_pool)
        latest_date = ""
    data_reasons = [] if effective_etf_count >= 5 else ["有效 ETF 数量不足，今日不建议买入"]
    data_passed = not data_reasons

    strategy_rows, strategy_reasons = _strategy_qa_rows(etf_pool)
    strategy_passed = not strategy_reasons

    output_builders = {
        "output/compare_signal.txt": command_compare_signal,
        "output/compare_signal.csv": command_compare_signal,
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
    rejected: list[str] = []
    defensive: list[str] = []

    blocking_reasons = data_reasons + strategy_reasons + output_reasons
    allow_observation = data_passed and strategy_passed and output_passed
    report = {
        "data_layer": {
            "passed": data_passed,
            "effective_etf_count": effective_etf_count,
            "latest_date": latest_date,
            "reasons": data_reasons,
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
        "risk_note": "仅用于人工观察，不自动下单，不连接券商，不构成投资建议。",
    }

    import json

    with (output_path / "qa_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    lines = [
        "质量检查报告",
        "=" * 40,
        f"数据层: {'通过' if data_passed else '未通过'}",
        f"策略层: {'通过' if strategy_passed else '未通过'}",
        f"输出层: {'通过' if output_passed else '未通过'}",
        f"是否允许小额观察: {'是' if allow_observation else '否'}",
        "",
        "需要处理的问题:",
    ]
    lines.extend([f"- {reason}" for reason in blocking_reasons] or ["- 无"])
    lines.extend(
        [
            "",
            "当前唯一策略:",
            "- " + ", ".join(recommended) if recommended else "- 无",
            "",
            "风险提示:",
            "- 仅用于人工观察，不自动下单，不连接券商，不构成投资建议。",
        ]
    )
    (output_path / "qa_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("QA report generated: output/qa_report.txt, output/qa_report.json")
    print("\n".join(lines))
    if not allow_observation:
        raise SystemExit(1)
    return report


def _rebalance_rule_text(strategy_config: StrategyConfig) -> str:
    return "日频更新，按最新完整日线生成下一交易日执行计划"


def _a_share_trade_calendar(start: str = "20100101", end: str = "20301231") -> pd.DatetimeIndex:
    return load_a_share_trading_calendar(start=start, end=end)


def _next_a_share_trade_date(signal_date: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(get_next_trading_day(pd.Timestamp(signal_date).date())).normalize()


def _execution_status(execution_date: pd.Timestamp, now: datetime | None = None) -> str:
    current = now or datetime.now(MARKET_TZ)
    calendar = _a_share_trade_calendar(
        start=(pd.Timestamp(current.date()) - pd.Timedelta(days=30)).strftime("%Y%m%d"),
        end=(pd.Timestamp(current.date()) + pd.Timedelta(days=60)).strftime("%Y%m%d"),
    )
    phase = get_market_phase(current, calendar)
    execution_day = pd.Timestamp(execution_date).date()
    current_day = current.date()
    if current_day < execution_day:
        return "今日已收盘，可生成下一交易日执行计划" if phase == "已收盘" else "等待执行"
    if current_day > execution_day:
        return "信号已过期，请重新生成"
    if current.time() < time(9, 35):
        return "等待开盘确认"
    if time(9, 35) <= current.time() <= time(10, 0):
        return "建议执行窗口"
    if phase == "已收盘":
        return "今日已收盘，可生成下一交易日执行计划"
    return "执行窗口已结束，请重新生成下一交易日计划"


def _resolve_effective_signal_date(dates: pd.DatetimeIndex, requested_signal_date: str | None) -> tuple[pd.Timestamp, str, str]:
    all_dates = pd.DatetimeIndex(sorted(pd.to_datetime(dates).unique()))
    if all_dates.empty:
        raise ValueError("No market dates are available")
    if not requested_signal_date:
        return pd.NaT, "", ""

    requested = pd.Timestamp(requested_signal_date).normalize()
    latest = pd.Timestamp(all_dates[-1]).normalize()
    if requested > latest:
        raise ValueError(
            f"用户选择 {requested.date()}，但本地日线数据只更新到 {latest.date()}，无法生成 {requested.date()} 信号。"
            f"请先刷新行情，或改为复盘 {latest.date()}。"
        )
    if requested not in all_dates:
        raise ValueError(
            f"用户选择 {requested.date()}，但本地没有这一天的完整日线数据，系统不能自动回退到更早日期计算信号。"
            "请刷新行情，或改选有本地日线的交易日做复盘。"
        )
    effective = requested
    execute_date = _next_a_share_trade_date(effective)
    execute_text = str(execute_date.date())
    return effective, str(requested.date()), execute_text


def _latest_signal_date_with_pending_execute(
    market_dates: pd.DatetimeIndex,
    signal_dates: list[pd.Timestamp],
) -> tuple[pd.Timestamp, str]:
    all_dates = pd.DatetimeIndex(sorted(pd.to_datetime(market_dates).unique()))
    if all_dates.empty:
        raise ValueError("No market dates are available")
    latest_data_date = pd.Timestamp(all_dates[-1]).normalize()
    signal_date = latest_data_date
    if signal_date not in all_dates:
        usable = all_dates[all_dates <= signal_date]
        if usable.empty:
            raise ValueError(f"signal date {signal_date.date()} is before available market data")
        signal_date = pd.Timestamp(usable[-1]).normalize()
    execute_date = _next_a_share_trade_date(signal_date)
    execute_text = str(execute_date.date())
    return signal_date, execute_text


def _market_data_cutoff_date(market_data: dict[str, pd.DataFrame]) -> pd.Timestamp:
    latest_dates = [pd.Timestamp(df.index.max()).normalize() for df in market_data.values() if not df.empty]
    if not latest_dates:
        raise ValueError("No market dates are available")
    counts = Counter(latest_dates)
    max_count = max(counts.values())
    return max(day for day, count in counts.items() if count == max_count)


def _rank_table_records(ranks: pd.DataFrame, target: list[str]) -> list[dict[str, Any]]:
    if ranks.empty:
        return []
    result = ranks.copy()
    if "selected" not in result.columns:
        result["selected"] = result["symbol"].isin(target)
    keep_cols = [
        "symbol",
        "name",
        "exchange",
        "asset_class",
        "category",
        "tracking_index",
        "theme",
        "sector",
        "latest_date",
        "close",
        "momentum",
        "momentum_20",
        "momentum_60",
        "momentum_120",
        "volatility_20",
        "max_drawdown_60",
        "score",
        "ma",
        "above_ma",
        "rank",
        "selected",
        "eligible",
        "final_signal",
        "avg_amount_20",
        "listed_days",
        "data_completeness",
        "filter_reason",
        "selection_reason",
    ]
    for col in keep_cols:
        if col not in result.columns:
            result[col] = None
    records: list[dict[str, Any]] = []
    for row in result[keep_cols].to_dict("records"):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, bool):
                clean[key] = "是" if value else "否"
            elif pd.isna(value):
                clean[key] = None
            elif hasattr(value, "item"):
                clean[key] = value.item()
            else:
                clean[key] = value
        records.append(clean)
    return records


def _json_cn_safe(value: Any) -> Any:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, dict):
        key_map = {
            "warning": "提示",
            "validation_warning": "校验提示",
            "risk_trigger_warning": "触发价说明",
            "price_basis_warning": "价格口径提示",
        }
        return {key_map.get(str(key), key): _json_cn_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_cn_safe(item) for item in value]
    return value


def _rank_table_summary(ranks: pd.DataFrame, target: list[str], max_rows: int = 10) -> str:
    if ranks.empty:
        return "无排名数据"
    result = ranks.copy()
    if "selected" not in result.columns:
        result["selected"] = result["symbol"].isin(target)
    items: list[str] = []
    for _, row in result.head(max_rows).iterrows():
        rank = row.get("rank")
        rank_text = "N/A" if pd.isna(rank) else str(int(rank))
        selected_text = "入选" if bool(row.get("selected", False)) else "未入选"
        reason = str(row.get("selection_reason") or "")
        reason_text = f"，{reason}" if reason else ""
        items.append(f"{rank_text}. {row.get('symbol')} {row.get('name')} {selected_text}{reason_text}")
    if len(result) > max_rows:
        items.append(f"... 共 {len(result)} 只 ETF")
    return " | ".join(items)


def _target_weights_text(target: list[str], weight: float) -> str:
    if not target:
        return ""
    return " | ".join(f"{symbol}:{weight:.4f}" for symbol in target)


def _signal_recent_rows(config_path: str, buffer: int = 80) -> int:
    _, raw, strategy = load_strategy_settings(config_path)
    windows = [
        20,
        60,
        120,
        strategy.momentum_period,
        strategy.ma_period,
        strategy.market_filter_ma_window if strategy.enable_market_filter else 0,
        strategy.min_trading_days,
        strategy.avg_amount_window,
        int(raw.get("min_effective_etf_count", 5)),
    ]
    return max(windows) + buffer


def _build_signal_engine(
    config_path: str,
    use_cache: bool,
    etf_pool: list[dict[str, str]] | None = None,
    market_data: dict[str, pd.DataFrame] | None = None,
) -> BacktestEngine:
    if not use_cache:
        return build_engine(config_path=config_path)
    etf_pool = etf_pool or load_etf_pool()
    if market_data is None:
        recent_rows = _signal_recent_rows(config_path)
        market_data = load_market_data(
            [item["symbol"] for item in etf_pool],
            allow_partial=True,
            etf_info={item["symbol"]: item for item in etf_pool},
            recent_rows=recent_rows,
        )
    return build_engine(config_path=config_path, market_data=market_data, etf_pool=etf_pool)


def _normalize_signal_version(value: str | None) -> str:
    text = str(value or SIGNAL_VERSION_V2).strip().upper()
    return SIGNAL_VERSION_V1 if text == SIGNAL_VERSION_V1 else SIGNAL_VERSION_V2


def _v2_selected_symbols(modular_pipeline: dict[str, Any]) -> list[str]:
    rows = modular_pipeline.get("pre_selection", [])
    result: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        selected = str(row.get("selected", "")).strip().lower() in {"true", "1", "yes", "y", "是", "selected", "入选"}
        symbol = normalize_symbol(row.get("symbol", ""))
        if selected and symbol:
            result.append(symbol)
    return result


def _v2_actionable_buy_symbols(modular_pipeline: dict[str, Any], selected_symbols: list[str]) -> list[str]:
    selected = set(selected_symbols)
    result: list[str] = []
    for row in modular_pipeline.get("entry", []):
        symbol = normalize_symbol(row.get("symbol", ""))
        action = str(row.get("buy_action", ""))
        actionable = ("买入" in action or "涔板叆" in action or "buy" in action.lower()) and "禁止" not in action and "forbid" not in action.lower()
        if symbol in selected and actionable:
            result.append(symbol)
    return result


def _v2_entry_actions(modular_pipeline: dict[str, Any]) -> str:
    selected = set(_v2_selected_symbols(modular_pipeline))
    actions = [
        f"{normalize_symbol(row.get('symbol', ''))}:{row.get('buy_action', '')}"
        for row in modular_pipeline.get("entry", [])
        if normalize_symbol(row.get("symbol", "")) in selected and str(row.get("buy_action", "")).strip()
    ]
    return " | ".join(actions) if actions else "无"


def _v2_buy_plan(modular_pipeline: dict[str, Any], selected_symbols: list[str]) -> list[dict[str, Any]]:
    selected = set(selected_symbols)
    rows: list[dict[str, Any]] = []
    for row in modular_pipeline.get("entry", []):
        symbol = normalize_symbol(row.get("symbol", ""))
        if symbol not in selected:
            continue
        rows.append(
            {
                "ETF代码": symbol,
                "ETF名称": row.get("name", ""),
                "交易动作": row.get("buy_action", ""),
                "参考买入价": row.get("buy_price", ""),
                "建议仓位": row.get("position_size", ""),
                "信号置信度": row.get("confidence", ""),
                "买入原因": row.get("entry_reason", ""),
                "信号来源": "V2 entry_signal.csv",
            }
        )
    return rows


def _v2_sell_plan(modular_pipeline: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ETF代码": normalize_symbol(row.get("symbol", "")),
            "ETF名称": row.get("name", ""),
            "退出动作": row.get("sell_action", ""),
            "参考卖出价": row.get("sell_price", ""),
            "减仓比例": row.get("reduce_ratio", ""),
            "冷却天数": row.get("cool_down_days", ""),
            "退出原因": row.get("exit_reason", ""),
            "信号来源": "V2 exit_signal.csv",
        }
        for row in modular_pipeline.get("exit", [])
    ]


def _v2_rank_records(modular_pipeline: dict[str, Any], max_rows: int = 80) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in modular_pipeline.get("pre_selection", [])[:max_rows]:
        selected = str(row.get("selected", "")).strip().lower() in {"true", "1", "yes", "y", "是", "selected", "入选"}
        records.append(
            {
                "symbol": normalize_symbol(row.get("symbol", "")),
                "name": row.get("name", ""),
                "sector": row.get("sector", ""),
                "latest_date": row.get("trade_date", ""),
                "score": row.get("score", ""),
                "rank": row.get("rank", ""),
                "selected": selected,
                "final_signal": "selected" if selected else "filtered_out",
                "selection_reason": row.get("reason", ""),
            }
        )
    return records


def _apply_signal_version_summary(summary: dict[str, Any], modular_pipeline: dict[str, Any], signal_version: str) -> None:
    v1_selected = str(summary.get("target_symbols", "") or "")
    v1_symbols = [item for item in v1_selected.split(",") if item and item not in {"空仓", "无"}]
    v2_symbols = _v2_selected_symbols(modular_pipeline)
    v2_buy_symbols = _v2_actionable_buy_symbols(modular_pipeline, v2_symbols)
    summary_fields = dict(modular_pipeline.get("summary_fields", {}))
    fallback_reason = str(summary_fields.get("fallback_reason", "无") or "无")
    base_v2_reason = str(summary_fields.get("v2_reason", "") or "")

    if fallback_reason != "无":
        comparison = f"V2 降级：{fallback_reason}"
    elif set(v1_symbols) == set(v2_symbols):
        comparison = "正常重合：V1 与 V2 最终 ETF 相同，当前页面按 signal_version 字段决定展示来源。"
    else:
        comparison = "V1/V2 不同：当前页面最终展示字段按 signal_version 接管。"

    summary.update(summary_fields)
    summary.update(
        {
            "signal_version": signal_version,
            "v1_selected_etfs": v1_selected,
            "v2_selected_etfs": ",".join(v2_symbols),
            "v2_market_state": summary_fields.get("v2_market_state", summary_fields.get("modular_market_state", "")),
            "v2_selected_sectors": summary_fields.get("v2_selected_sectors", summary_fields.get("modular_selected_sectors", "")),
            "v2_entry_actions": _v2_entry_actions(modular_pipeline),
            "v2_reason": f"{base_v2_reason} | {comparison}" if base_v2_reason else comparison,
            "v1_v2_comparison": comparison,
            "final_signal_source": signal_version,
        }
    )

    if signal_version == SIGNAL_VERSION_V2:
        summary.update(
            {
                "target_symbols": ",".join(v2_symbols) if v2_symbols else "空仓",
                "suggested_buy": ",".join(v2_buy_symbols) if v2_buy_symbols else "无",
                "suggested_sell": ",".join([normalize_symbol(row.get("symbol", "")) for row in modular_pipeline.get("exit", []) if normalize_symbol(row.get("symbol", ""))]) or "无",
                "buy_plan": json.dumps(_v2_buy_plan(modular_pipeline, v2_symbols), ensure_ascii=False),
                "sell_plan": json.dumps(_v2_sell_plan(modular_pipeline), ensure_ascii=False),
                "rank_table": json.dumps(_v2_rank_records(modular_pipeline), ensure_ascii=False),
                "rank_table_summary": "V2 pre_selection_result.csv 接管排名与候选池展示。",
                "buy_share_advice": summary.get("v2_entry_actions", "无"),
                "sell_advice": summary.get("modular_exit_actions", "无"),
                "operation_reason": summary.get("v2_reason", ""),
                "no_action_reason": fallback_reason if fallback_reason != "无" else summary.get("v2_reason", ""),
            }
        )


def _apply_v2_signal_summary(summary: dict[str, Any], modular_pipeline: dict[str, Any]) -> None:
    v2_symbols = _v2_selected_symbols(modular_pipeline)
    v2_buy_symbols = _v2_actionable_buy_symbols(modular_pipeline, v2_symbols)
    summary_fields = dict(modular_pipeline.get("summary_fields", {}))
    fallback_reason = str(summary_fields.get("fallback_reason", "无") or "无")
    v2_reason = str(summary_fields.get("v2_reason", "") or "")

    summary.update(summary_fields)
    summary.update(
        {
            "signal_version": SIGNAL_VERSION_V2,
            "final_signal_source": SIGNAL_VERSION_V2,
            "v2_selected_etfs": ",".join(v2_symbols),
            "v2_market_state": summary_fields.get("v2_market_state", summary_fields.get("modular_market_state", "")),
            "v2_selected_sectors": summary_fields.get("v2_selected_sectors", summary_fields.get("modular_selected_sectors", "")),
            "v2_entry_actions": _v2_entry_actions(modular_pipeline),
            "v2_reason": v2_reason,
            "fallback_reason": fallback_reason,
            "target_symbols": ",".join(v2_symbols) if v2_symbols else "空仓",
            "suggested_buy": ",".join(v2_buy_symbols) if v2_buy_symbols else "无",
            "suggested_sell": ",".join(
                normalize_symbol(row.get("symbol", ""))
                for row in modular_pipeline.get("exit", [])
                if normalize_symbol(row.get("symbol", ""))
            )
            or "无",
            "buy_plan": json.dumps(_v2_buy_plan(modular_pipeline, v2_symbols), ensure_ascii=False),
            "sell_plan": json.dumps(_v2_sell_plan(modular_pipeline), ensure_ascii=False),
            "rank_table": json.dumps(_v2_rank_records(modular_pipeline), ensure_ascii=False),
            "rank_table_summary": "V2 pre_selection_result.csv 接管排名与候选池展示。",
            "buy_share_advice": summary_fields.get("v2_entry_actions", summary_fields.get("modular_buy_actions", "无")),
            "sell_advice": summary_fields.get("modular_exit_actions", "无"),
            "operation_reason": v2_reason,
            "no_action_reason": fallback_reason if fallback_reason != "无" else v2_reason,
        }
    )


def _latest_strategy_signal(
    config_path: str,
    strategy_name: str,
    output_path: str | Path,
    requested_signal_date: str | None = None,
    observation_cash: float | None = None,
    use_cache: bool = False,
    signal_mode: str | None = None,
    etf_pool: list[dict[str, str]] | None = None,
    market_data: dict[str, pd.DataFrame] | None = None,
    signal_version: str | None = None,
) -> tuple[str, dict[str, Any]]:
    engine = _build_signal_engine(config_path, use_cache=use_cache, etf_pool=etf_pool, market_data=market_data)
    result = (
        {
            "strategy": engine.strategy,
            "equity_curve": pd.DataFrame(index=engine.close.index),
        }
        if use_cache
        else engine.run(output_dir="output", save_outputs=False)
    )
    _, raw, _ = load_strategy_settings(config_path)
    fee_config = load_fee_config(config_path)
    data_cutoff = _market_data_cutoff_date(engine.market_data)
    mode = signal_mode or ("manual_selected_date" if requested_signal_date else "auto_latest_available")
    if requested_signal_date and mode != "manual_selected_date":
        mode = "manual_selected_date"
    context: SignalContext = resolve_signal_context(
        selected_signal_date=requested_signal_date,
        mode=mode,
        now=datetime.now(MARKET_TZ),
        data_cutoff_date=data_cutoff.date(),
        trading_calendar=_a_share_trade_calendar(
            start=(data_cutoff - pd.Timedelta(days=30)).strftime("%Y%m%d"),
            end=(data_cutoff + pd.Timedelta(days=90)).strftime("%Y%m%d"),
        ),
        use_realtime_close_patch=any(
            not df.empty
            and str(df.get("source", pd.Series(dtype=str)).tail(1).iloc[0] if "source" in df.columns else "").endswith("_realtime_close_patch")
            for df in engine.market_data.values()
        ),
    )
    signal_date_source = {
        "manual_selected_date": "manual",
        "auto_latest_available": "auto",
        "latest_after_refresh": "latest_after_refresh",
    }.get(mode, "auto")
    signal_date = pd.Timestamp(context.actual_signal_date)
    requested_date_text = str(context.selected_signal_date) if context.selected_signal_date else ""
    execute_date_text = str(context.execution_date)
    execution_date = pd.Timestamp(context.execution_date)
    if signal_date not in result["strategy"].close.index:
        raise ValueError(
            f"用户选择 {signal_date.date()}，但本地没有这一天的完整日线数据，系统不能自动回退到更早日期计算信号。"
        )
    generated_at = datetime.now(MARKET_TZ).isoformat(timespec="seconds")
    execution_status = context.status_message or _execution_status(execution_date)
    text = generate_daily_signal_text(
        strategy=result["strategy"],
        equity_curve=result["equity_curve"],
        etf_info=engine.etf_info,
        signal_weekday=int(raw.get("signal_weekday", 4)),
        output_path=output_path,
        current_position_path="config/current_position.yaml",
        fee_config=fee_config,
        lot_size=int(raw.get("lot_size", 100)),
        enable_lot_rounding=bool(raw.get("enable_lot_rounding", True)),
        effective_etf_count=len(engine.market_data),
        min_effective_etf_count=int(raw.get("min_effective_etf_count", 5)),
        rebalance_frequency=str(raw.get("frequency", raw.get("rebalance_frequency", "daily"))),
        rebalance_timing=engine.strategy_config.rebalance_timing,
        rebalance_day=engine.strategy_config.rebalance_day,
        rebalance_day_of_month=engine.strategy_config.rebalance_day_of_month,
        rebalance_roll=engine.strategy_config.rebalance_roll,
        signal_date=signal_date,
        execution_date=execution_date,
        observation_cash=observation_cash,
        market_data=engine.market_data,
    )
    plan = build_signal_trade_plan(
        strategy=result["strategy"],
        etf_info=engine.etf_info,
        signal_date=signal_date,
        execution_date=execution_date,
        current_position_path="config/current_position.yaml",
        fee_config=fee_config,
        lot_size=int(raw.get("lot_size", 100)),
        enable_lot_rounding=bool(raw.get("enable_lot_rounding", True)),
        observation_cash=observation_cash,
        market_data=engine.market_data,
    )
    current_position = plan["current_position"]
    cash_for_signal = float(plan["cash"])
    positions = current_position.get("positions", {}) or {}
    current_holdings = [str(symbol).zfill(6) for symbol, item in positions.items() if float(item.get("shares", 0)) > 0]
    target = list(plan["target"])
    ranks = plan["ranks"].copy()
    if ranks.empty:
        rankable_count = 0
        excluded_count = len(engine.etf_pool)
        exclude_reason_dist: dict[str, int] = {"no_rank_data": excluded_count}
    else:
        latest_match = ranks.get("latest_date", pd.Series("", index=ranks.index)).astype(str).eq(str(signal_date.date()))
        eligible = ranks.get("eligible", ranks.get("filter_passed", pd.Series(False, index=ranks.index))).astype(bool)
        rankable_mask = latest_match & eligible
        rankable_count = int(rankable_mask.sum())
        excluded_count = max(len(engine.etf_pool) - rankable_count, 0)
        reasons = ranks.loc[~rankable_mask].get("filter_reason", pd.Series("", index=ranks.index)).astype(str).replace("", "not_expected_signal_date")
        exclude_reason_dist = {str(key): int(value) for key, value in reasons.value_counts().to_dict().items()}
    target_amounts = [f"{item['ETF代码']}:{float(item['目标金额']):.2f}" for item in plan["target_plan"]]
    buys = [item["ETF代码"] for item in plan["buy_plan"]]
    sells = [item["ETF代码"] for item in plan["sell_plan"]]
    holds = [item["ETF代码"] for item in plan["hold_plan"]]
    buy_lines = [
        f"{item['ETF代码']} {item['ETF名称']}: {item.get('交易动作', '买入')}，建议买入 {item['建议买入份额']:.0f} 份，今日建议买入金额 {item.get('今日建议买入金额', item['预计买入金额']):.2f} 元，三档买入价 {item.get('第一买入价', 0):.3f}/{item.get('第二买入价', 0):.3f}/{item.get('第三买入价', 0):.3f}。买入原因：{item.get('买入原因', item.get('reason', ''))}。执行说明：{item.get('执行说明', item['实际成交说明'])}"
        for item in plan["buy_plan"]
    ]
    skipped_buy_lines = [
        f"{item['ETF代码']} {item['ETF名称']}: {item['资金不足时的提示']}；一手所需资金 {item['一手所需资金'] if item['一手所需资金'] is not None else 'N/A'}；当前可用现金 {item['当前可用现金']:.2f} 元"
        for item in plan["skipped_buy_plan"]
    ]
    sell_lines = [
        f"{item['ETF代码']} {item['ETF名称']}: 建议卖出 {item['建议卖出份额']:.0f} 份，卖出原因：{item['卖出原因']}。实际成交说明：{item['实际成交说明']}"
        for item in plan["sell_plan"]
    ]
    hold_lines = [
        f"{item['ETF代码']} {item['ETF名称']}: 当前份额 {item['当前份额']:.0f} 份，目标金额 {item['目标金额']:.2f} 元，{item['是否需要补仓 / 减仓 / 不操作']}。原因：{item['原因']}"
        for item in plan["hold_plan"]
    ]
    estimated_cash = f"{float(plan['estimated_cash']):.2f} 元"
    no_action_reason = " | ".join(plan["no_action_reasons"]) if plan["no_action_reasons"] else "无"
    summary = {
        "strategy_name": strategy_name,
        "strategy_display_name": STRATEGY_DISPLAY_NAMES.get(strategy_name, strategy_name),
        "strategy_type_description": STRATEGY_TYPE_DESCRIPTIONS.get(strategy_name, ""),
        "is_dynamic_rotation": "是",
        "strategy_status": strategy_status(strategy_name),
        "config_path": config_path,
        "rebalance_frequency": engine.strategy_config.rebalance_frequency,
        "rebalance_timing": engine.strategy_config.rebalance_timing,
        "rebalance_day": engine.strategy_config.rebalance_day,
        "rebalance_day_of_month": engine.strategy_config.rebalance_day_of_month,
        "rebalance_roll": engine.strategy_config.rebalance_roll,
        "rebalance_rule": _rebalance_rule_text(engine.strategy_config),
        "requested_signal_date": requested_date_text,
        "selected_signal_date": requested_date_text,
        "effective_signal_date": str(signal_date.date()),
        "actual_signal_date": str(signal_date.date()),
        "data_cutoff_date": str(context.data_cutoff_date) if context.data_cutoff_date else "",
        "execute_date": execute_date_text,
        "execution_date": execute_date_text,
        "generated_at": generated_at,
        "data_latest_date": str(context.data_cutoff_date) if context.data_cutoff_date else str(result["strategy"].close.index.max().date()),
        "execution_status": execution_status,
        "execution_window": EXECUTION_WINDOW,
        "execution_price_rule": EXECUTION_PRICE_RULE,
        "signal_date_source": signal_date_source,
        "market_phase": context.market_phase,
        "data_mode": context.data_mode,
        "use_realtime_close_patch": "是" if context.use_realtime_close_patch else "否",
        "signal_date": str(signal_date.date()),
        "latest_data_date": str(context.data_cutoff_date) if context.data_cutoff_date else str(result["strategy"].close.index.max().date()),
        "observation_cash": cash_for_signal,
        "current_cash": float(current_position.get("cash", 0)),
        "current_holdings": ",".join(current_holdings) if current_holdings else ("空仓" if current_position.get("current_empty") else "未填写"),
        "current_positions": ",".join(current_holdings) if current_holdings else ("空仓" if current_position.get("current_empty") else "未填写"),
        "target_symbols": ",".join(target) if target else "空仓",
        "target_weights": _target_weights_text(target, float(plan["target_weight"])),
        "target_amounts": " | ".join(target_amounts) if target_amounts else "无",
        "suggested_sell": ",".join(sells) if sells else "无",
        "suggested_buy": ",".join(buys) if buys else "无",
        "continue_hold": ",".join(holds) if holds else "无",
        "buy_share_advice": " | ".join(buy_lines) if buy_lines else "无",
        "skipped_buy_advice": " | ".join(skipped_buy_lines) if skipped_buy_lines else "无",
        "sell_advice": " | ".join(sell_lines) if sell_lines else "无",
        "hold_advice": " | ".join(hold_lines) if hold_lines else "无",
        "buy_plan": json.dumps(_json_cn_safe(plan["buy_plan"]), ensure_ascii=False),
        "intraday_execution_plan": json.dumps(_json_cn_safe(plan["intraday_execution_plan"]), ensure_ascii=False),
        "skipped_buy_plan": json.dumps(_json_cn_safe(plan["skipped_buy_plan"]), ensure_ascii=False),
        "sell_plan": json.dumps(_json_cn_safe(plan["sell_plan"]), ensure_ascii=False),
        "sell_execution_plan": json.dumps(_json_cn_safe(plan.get("sell_execution_plan", [])), ensure_ascii=False),
        "hold_plan": json.dumps(_json_cn_safe(plan["hold_plan"]), ensure_ascii=False),
        "rank_table": json.dumps(_rank_table_records(plan["ranks"], target), ensure_ascii=False),
        "rank_table_summary": _rank_table_summary(plan["ranks"], target),
        "base_etf_count": len(engine.etf_pool),
        "today_available_etf_count": rankable_count,
        "today_excluded_etf_count": excluded_count,
        "ranked_etf_count": rankable_count,
        "excluded_reason_distribution": json.dumps(exclude_reason_dist, ensure_ascii=False),
        "no_action_reason": no_action_reason,
        "position_configured": "是" if current_position.get("position_configured") else "否",
        "current_empty": "是" if current_position.get("current_empty") else "否",
        "estimated_remaining_cash": estimated_cash,
        "operation_reason": "详见 compare_signal.txt 中的日频动量信号说明。",
        "risk_note": "仅用于人工观察，不构成投资建议。",
    }
    v1_summary = dict(summary)
    modular_pipeline = run_modular_signal_pipeline(
        etf_pool=engine.etf_pool,
        market_data=engine.market_data,
        holdings=[dict(item) for item in current_position.get("holdings", [])],
        output_dir="output",
        signal_date=signal_date,
        risk_date=execution_date,
        current_position_path="config/current_position.yaml",
    )
    _apply_v2_signal_summary(summary, modular_pipeline)
    comparison_row = write_v1_v2_comparison(v1_summary, summary, modular_pipeline, output_dir="output")
    summary.update(
        {
            "v1_v2_no_buy_reason": comparison_row.get("v2_no_buy_reason", ""),
            "v1_v2_difference_reason": comparison_row.get("difference_reason", ""),
            "v2_actual_buy_etfs": comparison_row.get("v2_actual_buy_etfs", "无"),
        }
    )
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
    return "NO (未找到 qa_report.json，请先运行质量检查)"


def _current_position_overview(current_position: dict[str, Any]) -> str:
    if current_position.get("current_empty"):
        return "空仓"
    if not current_position.get("position_configured"):
        return "未填写"
    positions = current_position.get("positions", {}) or {}
    holdings = [
        f"{str(symbol).zfill(6)}:{float(item.get('shares', 0)):.0f}份"
        for symbol, item in positions.items()
        if float(item.get("shares", 0)) > 0
    ]
    return "，".join(holdings) if holdings else "空仓"


def _compare_signal_overview(rows: list[dict[str, Any]]) -> str:
    signal_dates = [row["signal_date"] for row in rows if row.get("signal_date")]
    latest_dates = [row.get("data_cutoff_date") or row["latest_data_date"] for row in rows if row.get("data_cutoff_date") or row.get("latest_data_date")]
    signal_date = signal_dates[0] if signal_dates else "UNKNOWN"
    latest_data_date = max(latest_dates) if latest_dates else "UNKNOWN"
    current_position = ensure_current_position("config/current_position.yaml")
    allowed = _load_small_observation_status()
    main_rule = next((row.get("rebalance_rule") for row in rows), "日频更新")
    observation_cash = next((row.get("observation_cash") for row in rows), current_position.get("cash", 0))
    lines = [
        CORE_STRATEGY_NAME,
        "=" * 40,
        "总览",
        f"- 信号日: {signal_date}",
        f"- 数据最新日期: {latest_data_date}",
        f"- 执行日: {next((row.get('execution_date') for row in rows if row.get('execution_date')), 'UNKNOWN')}",
        f"- 当前市场阶段: {next((row.get('market_phase') for row in rows if row.get('market_phase')), 'UNKNOWN')}",
        f"- 当前数据模式: {next((row.get('data_mode') for row in rows if row.get('data_mode')), 'UNKNOWN')}",
        f"- 生成时间: {next((row.get('generated_at') for row in rows if row.get('generated_at')), 'UNKNOWN')}",
        f"- 执行状态: {next((row.get('execution_status') for row in rows if row.get('execution_status')), 'UNKNOWN')}",
        f"- 本次观察资金: {float(observation_cash):.2f} 元",
        f"- 当前真实现金: {float(current_position.get('cash', 0)):.2f} 元",
        f"- 当前真实持仓: {_current_position_overview(current_position)}",
        "- 策略定位: 右侧确认型趋势跟随，不预测启动点，不做左侧埋伏",
        f"- 调仓规则：{main_rule}",
        f"- 是否允许小额观察: {allowed}",
    ]
    if signal_date != latest_data_date:
        lines.extend(
            [
                "",
                "当前信号日期不是最新交易日，请确认数据是否已更新。",
            ]
        )
    lines.extend(
        [
            "",
            "安全边界:",
            "- 本工具不自动下单，不连接券商，不替代人工判断。",
            "- 小资金观察建议仍为 1000-3000 元。",
            "- 数据不足或趋势偏弱时，今日不建议买入。",
        ]
    )
    return "\n".join(lines)


def _main_ranking_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    main = rows[0] if rows else {}
    records = json.loads(str(main.get("rank_table") or "[]"))
    if not isinstance(records, list):
        records = []
    frame = pd.DataFrame(records)
    requested = [
        "symbol",
        "name",
        "exchange",
        "asset_class",
        "category",
        "tracking_index",
        "latest_date",
        "momentum_20",
        "momentum_60",
        "momentum_120",
        "volatility_20",
        "max_drawdown_60",
        "score",
        "rank",
        "final_signal",
    ]
    for col in requested:
        if col not in frame.columns:
            frame[col] = None
    return frame[requested]


TXT_COLUMN_NAME_MAP = {
    "rank": "排名",
    "symbol": "ETF代码",
    "name": "ETF名称",
    "exchange": "交易所",
    "asset_class": "资产类别",
    "category": "细分类别",
    "tracking_index": "跟踪指数",
    "latest_date": "最新数据日期",
    "momentum_20": "20日动量",
    "momentum_60": "60日动量",
    "momentum_120": "120日动量",
    "volatility_20": "20日波动率",
    "max_drawdown_60": "60日最大回撤",
    "score": "综合得分",
    "final_signal": "最终信号",
}


def _ranking_text(frame: pd.DataFrame, max_rows: int = 20) -> str:
    if frame.empty:
        return "无排名数据"
    view = frame.head(max_rows).copy()
    if "final_signal" in view.columns:
        view["final_signal"] = view["final_signal"].replace(
            {
                "selected": "入选",
                "eligible_not_selected": "通过过滤未入选",
                "filtered_out": "未通过过滤",
                "watch": "观察",
            }
        )
    view = view.rename(columns=TXT_COLUMN_NAME_MAP)
    return view.to_string(index=False)


def command_compare_signal(
    signal_date: str | None = None,
    cash: float | None = None,
    strategy: str | None = None,
    use_cache: bool = True,
    signal_mode: str | None = None,
) -> pd.DataFrame:
    signal_started = time_module.perf_counter()
    if strategy and strategy not in STRATEGY_CONFIGS:
        raise ValueError("?????????????? ETF ??????")

    shared_etf_pool = load_etf_pool() if use_cache else None
    shared_market_data = None
    if use_cache and shared_etf_pool is not None:
        recent_rows = _signal_recent_rows(CORE_STRATEGY_CONFIG)
        shared_market_data = load_market_data(
            [item["symbol"] for item in shared_etf_pool],
            allow_partial=True,
            etf_info={item["symbol"]: item for item in shared_etf_pool},
            recent_rows=recent_rows,
        )

    text, summary = _latest_strategy_signal(
        CORE_STRATEGY_CONFIG,
        CORE_STRATEGY_NAME,
        "output/daily_signal.txt",
        requested_signal_date=signal_date,
        observation_cash=cash,
        use_cache=use_cache,
        signal_mode=signal_mode,
        etf_pool=shared_etf_pool,
        market_data=shared_market_data,
    )
    risk_date = str(summary.get("execution_date") or summary.get("execute_date") or summary.get("effective_signal_date") or signal_date or "")
    risk_gate = calculate_next_day_risk(risk_date or None)
    write_risk_outputs(risk_gate, output_dir="output")
    get_learning_risk_context(risk_gate.risk_date, gate=risk_gate)
    summary = apply_risk_gate(summary, risk_gate)
    rows = [summary]
    ranking = _main_ranking_frame(rows)
    top_lines = [
        "",
        "【次日风险预警】",
        f"- 当前风险等级：{risk_gate.risk_level}，风险分数：{risk_gate.risk_score}",
        f"- 买入冻结：{'是' if risk_gate.freeze_entry else '否'}；权益仓位上限：{risk_gate.equity_cap_override:.0%}",
        f"- 受影响方向：{'、'.join(risk_gate.affected_sectors) if risk_gate.affected_sectors else '无'}",
        f"- 原因：{risk_gate.explain}",
    ]
    if not ranking.empty:
        top_lines.extend([
            "",
            "【过滤后 ETF 池动量排名 Top 20】",
            _ranking_text(ranking, max_rows=20),
        ])
    combined = _compare_signal_overview(rows) + "\n".join(top_lines) + "\n\n" + text
    Path("output/compare_signal.txt").write_text(combined, encoding="utf-8")

    result = pd.DataFrame(rows)
    result.to_csv("output/compare_signal.csv", index=False, encoding="utf-8-sig")

    signal_seconds = time_module.perf_counter() - signal_started
    if use_cache:
        try:
            coverage = pd.read_csv("output/data_coverage_report.csv", dtype={"symbol": str}).fillna("")
            success_count = int(coverage["success"].astype(str).str.lower().isin(["true", "1", "yes", "是"]).sum()) if "success" in coverage.columns else 0
            failed_count = int(len(coverage) - success_count)
        except Exception:
            coverage = pd.DataFrame()
            success_count = 0
            failed_count = 0
        _append_timing_log(
            {
                "load_universe_seconds": 0.0,
                "check_cache_seconds": 0.0,
                "download_seconds": 0.0,
                "qa_seconds": 0.0,
                "signal_seconds": round(signal_seconds, 3),
                "total_seconds": round(signal_seconds, 3),
                "processed_count": int(len(coverage)),
                "skipped_count": 0,
                "success_count": success_count,
                "failed_count": failed_count,
            }
        )
    print("日频右侧确认型 ETF 动量轮动策略信号已生成: output/compare_signal.txt, output/compare_signal.csv")
    for _, row in result.iterrows():
        print(
            " | ".join(
                [
                    f"信号日={row.get('effective_signal_date', '')}",
                    f"最新数据={row.get('latest_data_date', '')}",
                    f"目标={row.get('target_symbols', '')}",
                    f"买入={row.get('suggested_buy', '')}",
                    f"卖出={row.get('suggested_sell', '')}",
                ]
            )
        )
    return result

def _strategy_config_from_observation(name: str) -> str:
    return CORE_STRATEGY_CONFIG


def command_observation_report() -> str:
    observation_path = Path("config/live_observation.yaml")
    if not observation_path.exists():
        observation_path.write_text(
            yaml.safe_dump(
                {
                    "start_date": None,
                    "capital_observed": 1000,
                    "strategy_to_follow": CORE_STRATEGY_NAME,
                    "notes": "",
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    observation = load_yaml(observation_path)
    strategy_name = CORE_STRATEGY_NAME
    config_path = _strategy_config_from_observation(strategy_name)
    engine = build_engine(config_path=config_path)
    result = engine.run(output_dir="output", save_outputs=False)
    _, raw, _ = load_strategy_settings(config_path)
    signal_dates = get_rebalance_dates(
        result["strategy"].close.index,
        str(raw.get("frequency", raw.get("rebalance_frequency", "daily"))),
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
    command_update_data(mode="refresh" if refresh else "incremental", refresh=refresh, config_path=config_path)
    command_backtest(config_path=config_path)
    command_signal(config_path=config_path)
    print("全部流程完成: 数据、回测、日频信号均已更新")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股ETF低频轮动系统")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update-data", help="更新 ETF 历史数据")
    update_parser.add_argument("--mode", choices=["incremental", "refresh", "repair_missing", "rebuild", "full_refresh"], default="incremental", help="Data update mode")
    update_parser.add_argument("--incremental", action="store_true", help="日常增量刷新（默认）")
    update_parser.add_argument("--repair-missing", action="store_true", help="只修复缺失、异常或落后的行情缓存")
    update_parser.add_argument("--full-refresh", action="store_true", help="全量重建历史行情缓存，耗时可能数小时")
    update_parser.add_argument("--target-date", default=None, help="Target signal market date, YYYY-MM-DD")
    update_parser.add_argument("--symbols", default=None, help="Comma-separated ETF symbols for refresh mode")
    update_parser.add_argument("--max-count", type=int, default=None, help="Only update the first N ETFs after filtering")
    update_parser.add_argument("--max-workers", type=int, default=8, help="Concurrent ETF download workers")
    update_parser.add_argument("--debug", action="store_true", help="Print full exception stack traces during data update")
    update_parser.add_argument("--refresh", action="store_true", help="兼容旧参数：映射为日常增量刷新，不触发全量重建")
    update_parser.add_argument("--config", default="config/strategy.yaml")

    retry_parser = subparsers.add_parser("retry-failed-data", help="只重试覆盖报告中失败或缺失的 ETF")
    retry_parser.add_argument("--config", default="config/strategy.yaml")

    subparsers.add_parser("data-report", help="扫描本地缓存并生成数据覆盖报告")
    subparsers.add_parser("qa-data", help="运行数据质量检查和数据闸门")

    backtest_parser = subparsers.add_parser("backtest", help="运行回测")
    backtest_parser.add_argument("--config", default="config/strategy.yaml")

    signal_parser = subparsers.add_parser("signal", help="生成最新日频信号")
    signal_parser.add_argument("--config", default="config/strategy.yaml")

    run_all_parser = subparsers.add_parser("run-all", help="更新数据、回测并生成日频信号")
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
    compare_parser = subparsers.add_parser("compare-signal", help="生成日频动量轮动信号")
    compare_parser.add_argument("--signal-date", default=None, help="Manual requested signal date, YYYY-MM-DD")
    compare_parser.add_argument("--cash", type=float, default=None, help="Observation cash amount for signal sizing")
    compare_parser.add_argument("--signal-mode", choices=["manual_selected_date", "auto_latest_available", "latest_after_refresh"], default=None)
    generate_parser = subparsers.add_parser("generate-signal", help="生成基于过滤后完整 ETF 池的信号排名")
    generate_parser.add_argument("--signal-date", default=None, help="Manual requested signal date, YYYY-MM-DD")
    generate_parser.add_argument("--cash", type=float, default=None, help="Observation cash amount for signal sizing")
    generate_parser.add_argument("--use-cache", action="store_true", help="Use recent data windows and cached indicators")
    generate_parser.add_argument("--signal-mode", choices=["manual_selected_date", "auto_latest_available", "latest_after_refresh"], default=None)
    subparsers.add_parser("observation-report", help="生成实盘观察报告")
    add_risk_subparser(subparsers)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "risk":
        handle_risk_command(args)
    elif args.command == "update-data":
        update_mode = args.mode
        if args.full_refresh:
            update_mode = "full_refresh"
        elif args.repair_missing:
            update_mode = "repair_missing"
        elif args.incremental or args.refresh:
            update_mode = "incremental"
        command_update_data(
            mode=update_mode,
            symbols=args.symbols,
            max_count=args.max_count,
            max_workers=args.max_workers,
            refresh=args.refresh,
            target_date=args.target_date,
            debug=args.debug,
            config_path=args.config,
        )
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
        command_compare_signal(signal_date=args.signal_date, cash=args.cash, signal_mode=args.signal_mode)
    elif args.command == "generate-signal":
        command_compare_signal(signal_date=args.signal_date, cash=args.cash, use_cache=args.use_cache, signal_mode=args.signal_mode)
    elif args.command == "observation-report":
        command_observation_report()


if __name__ == "__main__":
    main()
