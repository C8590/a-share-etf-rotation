from __future__ import annotations

import argparse
import json
import sys
import time as time_module
from datetime import datetime, time
from itertools import product
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from analysis.performance import summarize_equity
from analysis.reports import build_monthly_returns, build_trade_diagnostics, build_yearly_returns
from backtest.engine import BacktestEngine
from backtest.portfolio import FeeConfig
from benchmark.report import build_benchmark_report
from data.adjustment import audit_cache_metadata, build_adjustment_audit, summarize_adjustment_audit, summarize_cache_metadata_audit
from data.candidate_gate import (
    build_candidate_gate_report,
    merge_candidate_gate_into_qa_report,
    summarize_candidate_gate,
    write_candidate_gate_report,
)
from data.candidate_unblock import (
    build_candidate_unblock_plan,
    merge_candidate_unblock_into_qa_report,
    summarize_candidate_unblock_plan,
    write_candidate_unblock_plan,
)
from data.cache_refresh import (
    repair_missing_cache,
    build_refresh_plan,
    run_pilot_refresh,
    summarize_missing_cache_repair,
    summarize_pilot_refresh,
    summarize_refresh_plan,
    write_refresh_plan,
)
from data.data_governance import (
    build_data_governance_status,
    merge_data_governance_into_qa_report,
    write_data_governance_runbook,
    write_data_governance_status,
)
from data.downloader import build_data_coverage_report, load_etf_pool, update_all_data
from data.etf_007b import (
    build_007b_small_scope_report,
    merge_007b_small_scope_into_qa_report,
    summarize_007b_small_scope,
    write_007b_small_scope_report,
)
from data.etf_metrics import compute_etf_metrics, summarize_etf_metrics, write_etf_metrics_report
from data.etf_metadata import summarize_etf_metadata, update_etf_metadata
from data.index_data import summarize_index_data, update_index_data
from data.index_readiness import (
    build_007b_readiness_check,
    build_index_unlock_plan,
    merge_007b_readiness_into_qa_report,
    summarize_007b_readiness,
    write_007b_readiness_report,
)
from data.index_source_diagnostics import (
    diagnose_index_source_candidates,
    summarize_index_source_diagnostics,
    write_index_source_diagnostics_report,
)
from data.manual_review import (
    build_manual_review_list,
    merge_manual_review_into_qa_report,
    summarize_manual_review,
    write_manual_review_report,
)
from data.observation_pool import (
    build_short_history_observation_pool,
    merge_observation_pool_into_qa_report,
    summarize_observation_pool,
    write_observation_pool_report,
)
from data.quality import run_data_quality_checks, summarize_failure_summary
from data.quality_diagnosis import (
    build_quality_remediation_plan,
    merge_quality_diagnosis_into_qa_report,
    summarize_quality_diagnosis,
    write_quality_diagnosis_report,
)
from data.qa_status import (
    build_qa_status_breakdown,
    merge_qa_status_into_qa_report,
    summarize_qa_status,
    write_qa_status_report,
)
from data.schema import DATA_SCHEMA_VERSION, SCHEMA_VERSION
from data.source_preference import run_source_preference_evaluation, summarize_source_preference_audit
from data.source_diagnostics import run_source_diagnostics, summarize_source_diagnostics
from data.source_lag import (
    build_source_lag_report,
    merge_source_lag_into_qa_report,
    summarize_source_lag,
    write_source_lag_report,
)
from data.storage import load_market_data
from data.trading_calendar import audit_trading_calendar, get_trading_days, next_trading_day, summarize_trading_calendar_audit
from signal.weekly_signal import build_signal_trade_plan, ensure_current_position, generate_weekly_signal_text
from strategy.review import build_strategy_review, strategy_status
from strategy.etf_rotation import StrategyConfig, get_rebalance_dates
from strategy.factors import (
    build_factor_score_audit_from_files,
    compute_factor_score_reports,
    evaluate_factor_score_gate_from_files,
    summarize_factor_score,
    write_factor_score_audit,
    write_factor_score_gate_report,
    write_factor_score_reports,
)
from strategy.factor_readiness import (
    build_008b_readiness_check,
    merge_008b_readiness_into_qa_report,
    summarize_008b_readiness,
    write_008b_readiness_report,
)


PENDING_EXECUTE_DATE_TEXT = "下一交易日，待数据确认"
MARKET_TZ = ZoneInfo("Asia/Shanghai")


STRATEGY_CONFIGS = {
    "original": "config/strategy_original.yaml",
    "conservative": "config/strategy_conservative.yaml",
    "balanced": "config/strategy_balanced.yaml",
    "equal_weight_monthly": "config/strategy_equal_weight_monthly.yaml",
    "reduced_equal_weight_monthly": "config/strategy_reduced_equal_weight_monthly.yaml",
    "momentum_rotation_monthly": "config/strategy_momentum_rotation_monthly.yaml",
}

STRATEGY_DISPLAY_NAMES = {
    "momentum_rotation_monthly": "动态量化轮动策略",
    "reduced_equal_weight_monthly": "固定篮子基准策略 / 精选等权配置策略",
    "equal_weight_monthly": "全池等权基准",
    "balanced": "研究策略：均衡轮动",
    "conservative": "防守参考策略",
    "original": "原始策略",
}

STRATEGY_TYPE_DESCRIPTIONS = {
    "momentum_rotation_monthly": "真正动态轮动策略：每个 signal_date 重新计算 close 动量、均线和排名，目标 ETF 可能随日期变化。",
    "reduced_equal_weight_monthly": "固定篮子等权配置基准：目标 ETF 来自配置篮子，通常不会因日期变化而变化。",
    "equal_weight_monthly": "全池等权基准：覆盖 ETF 池并按等权方式再平衡。",
    "balanced": "研究参考策略，不作为主观察策略。",
    "conservative": "防守参考策略，不作为主观察策略。",
    "original": "历史原始策略，仅保留用于对照。",
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
    strategy_type = str(raw.get("strategy_type", "rotation"))
    if config.get("strategy_name") == "momentum_rotation_monthly":
        strategy_type = "momentum_rotation_monthly"
    strategy_cfg = StrategyConfig(
        strategy_type=strategy_type,
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
    return {
        "processed_count": len(statuses),
        "skipped_count": sum(1 for item in statuses if item.success and (getattr(item, "status", "") == "skipped" or getattr(item, "cached", False))),
        "success_count": sum(1 for item in statuses if item.success and getattr(item, "status", "") != "skipped" and not getattr(item, "cached", False)),
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
    max_workers: int = 6,
    refresh: bool = False,
    config_path: str = "config/strategy.yaml",
    progress_callback: Any = None,
) -> dict[str, Any]:
    if refresh and mode == "incremental":
        mode = "refresh"
    selected_symbols = {item.strip().zfill(6) for item in str(symbols or "").split(",") if item.strip()}

    total_started = time_module.perf_counter()
    stage_started = time_module.perf_counter()
    _emit_progress(progress_callback, stage="读取 ETF 池", current=0, total=0)
    etf_pool = load_etf_pool()
    load_universe_seconds = time_module.perf_counter() - stage_started

    stage_started = time_module.perf_counter()
    _emit_progress(progress_callback, stage="检查本地缓存", current=0, total=len(etf_pool))
    backtest_cfg, _, _ = load_strategy_settings(config_path)
    check_cache_seconds = time_module.perf_counter() - stage_started

    stage_started = time_module.perf_counter()
    successes, errors, statuses = update_all_data(
        etf_pool=etf_pool,
        start_date=str(backtest_cfg.get("start_date", "20190101")),
        end_date=backtest_cfg.get("end_date"),
        refresh=(mode in {"refresh", "rebuild"}),
        retry_failed_only=False,
        mode=mode,
        symbols=selected_symbols or None,
        max_workers=max_workers,
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
        **counts,
    }
    _append_timing_log(metrics)
    _emit_progress(progress_callback, stage="完成", current=len(statuses), total=len(etf_pool), latest_data_date=latest_local_date, **counts)
    print("数据更新完成:" if not errors else "数据更新未完成:")
    print_data_status(statuses)
    if errors and not successes:
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
    gate = run_data_quality_checks(etf_pool, coverage_rows=[item.to_row() for item in statuses])
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
        market_data=engine.market_data,
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

    trading_calendar_audit = audit_trading_calendar(output_dir=output_path)
    data_statuses = build_data_coverage_report(etf_pool)
    data_gate = run_data_quality_checks(etf_pool, coverage_rows=[item.to_row() for item in data_statuses])
    cache_metadata_audit = audit_cache_metadata(etf_pool, output_dir=output_path)
    adjustment_audit = build_adjustment_audit(etf_pool, output_dir=output_path, coverage_rows=[item.to_row() for item in data_statuses])
    cache_refresh_plan = build_refresh_plan(etf_pool, output_dir=output_path)
    write_refresh_plan(cache_refresh_plan, output_path / "cache_refresh_plan.csv")
    quality_diagnosis_rows = build_quality_remediation_plan(output_dir=output_path)
    write_quality_diagnosis_report(
        quality_diagnosis_rows,
        report_path=output_path / "data_quality_diagnosis.csv",
        summary_path=output_path / "data_quality_diagnosis_summary.csv",
    )
    quality_diagnosis_summary = summarize_quality_diagnosis(quality_diagnosis_rows)
    source_lag_rows = build_source_lag_report(output_dir=output_path)
    source_lag_report_path, source_lag_summary_path = write_source_lag_report(
        source_lag_rows,
        report_path=output_path / "source_lag_report.csv",
        summary_path=output_path / "source_lag_summary.csv",
    )
    source_lag_summary = summarize_source_lag(source_lag_rows)
    candidate_gate_rows = build_candidate_gate_report(output_dir=output_path)
    write_candidate_gate_report(
        candidate_gate_rows,
        report_path=output_path / "candidate_gate.csv",
        summary_path=output_path / "candidate_gate_summary.csv",
    )
    candidate_gate_summary = summarize_candidate_gate(candidate_gate_rows)
    observation_pool_rows = build_short_history_observation_pool(output_dir=output_path)
    write_observation_pool_report(
        observation_pool_rows,
        report_path=output_path / "short_history_observation_pool.csv",
        summary_path=output_path / "short_history_observation_summary.csv",
    )
    observation_pool_summary = summarize_observation_pool(observation_pool_rows)
    manual_review_rows = build_manual_review_list(output_dir=output_path)
    write_manual_review_report(
        manual_review_rows,
        report_path=output_path / "manual_review_list.csv",
        summary_path=output_path / "manual_review_summary.csv",
    )
    manual_review_summary = summarize_manual_review(manual_review_rows)
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
        "schema_version": SCHEMA_VERSION,
        "data_schema_version": DATA_SCHEMA_VERSION,
        "data_layer": {
            "passed": data_passed,
            "effective_etf_count": data_gate.effective_etf_count,
            "latest_date": data_gate.latest_date,
            "reasons": data_gate.reasons,
            "coverage_report": "output/data_coverage_report.csv",
            "quality_report": "output/data_quality_report.csv",
            "data_quality_diagnosis_report": "output/data_quality_diagnosis.csv",
            "data_quality_diagnosis_summary_report": "output/data_quality_diagnosis_summary.csv",
            "data_quality_diagnosis": quality_diagnosis_summary,
            "short_history_count": quality_diagnosis_summary["short_history_count"],
            "stale_cache_count": quality_diagnosis_summary["stale_cache_count"],
            "severe_quality_issue_count": quality_diagnosis_summary["severe_quality_issue_count"],
            "candidate_excluded_count": quality_diagnosis_summary["candidate_excluded_count"],
            "manual_review_required_count": quality_diagnosis_summary["manual_review_required_count"],
            "refresh_needed_count": quality_diagnosis_summary["refresh_needed_count"],
            "top_blocking_reasons": quality_diagnosis_summary["top_blocking_reasons"],
            "top_examples": quality_diagnosis_summary["top_examples"],
            "short_history_observation_pool_report": "output/short_history_observation_pool.csv",
            "short_history_observation_summary_report": "output/short_history_observation_summary.csv",
            "observation_pool": observation_pool_summary,
            "total_observation_count": observation_pool_summary["total_observation_count"],
            "very_short_history_count": observation_pool_summary["very_short_history_count"],
            "low_liquidity_watch_count": observation_pool_summary["low_liquidity_watch_count"],
            "manual_review_required_count": observation_pool_summary["manual_review_required_count"],
            "estimated_eligible_within_20d_count": observation_pool_summary["estimated_eligible_within_20d_count"],
            "estimated_eligible_within_60d_count": observation_pool_summary["estimated_eligible_within_60d_count"],
            "unknown_estimate_count": observation_pool_summary["unknown_estimate_count"],
            "manual_review_list_report": "output/manual_review_list.csv",
            "manual_review_summary_report": "output/manual_review_summary.csv",
            "manual_review": manual_review_summary,
            "manual_review_count": manual_review_summary["manual_review_count"],
            "p0_manual_review_count": manual_review_summary["p0_manual_review_count"],
            "abnormal_return_review_count": manual_review_summary["abnormal_return_review_count"],
            "low_liquidity_review_count": manual_review_summary["low_liquidity_review_count"],
            "very_short_history_review_count": manual_review_summary["very_short_history_review_count"],
            "trading_calendar_report": "output/trading_calendar_audit.csv",
            "trading_calendar": summarize_trading_calendar_audit(trading_calendar_audit),
            "failure_summary_report": "output/data_failure_summary.csv",
            "failure_summary": summarize_failure_summary(data_gate.failure_summary),
            "cache_metadata_audit_report": "output/cache_metadata_audit.csv",
            "cache_metadata_audit": summarize_cache_metadata_audit(cache_metadata_audit),
            "adjustment_audit_report": "output/adjustment_audit.csv",
            "adjustment_audit": summarize_adjustment_audit(adjustment_audit),
            "cache_refresh_plan_report": "output/cache_refresh_plan.csv",
            "cache_refresh_plan": summarize_refresh_plan(cache_refresh_plan),
            "pilot_refresh_report": "output/pilot_refresh_report.csv",
            "pilot_refresh": summarize_pilot_refresh(report_path=output_path / "pilot_refresh_report.csv"),
            "missing_cache_repair_report": "output/missing_cache_repair_report.csv",
            "missing_cache_repair": summarize_missing_cache_repair(report_path=output_path / "missing_cache_repair_report.csv"),
            "source_preference_audit_report": "output/source_preference_audit.csv",
            "source_preference_audit": summarize_source_preference_audit(report_path=output_path / "source_preference_audit.csv"),
            "source_diagnostics_report": "output/source_diagnostics_report.csv",
            "source_diagnostics": summarize_source_diagnostics(report_path=output_path / "source_diagnostics_report.csv"),
            "source_lag_report": str(source_lag_report_path),
            "source_lag_summary_report": str(source_lag_summary_path),
            "source_lag": source_lag_summary,
            "source_lag_count": source_lag_summary["source_lag_count"],
            "source_lag_blocker_count": source_lag_summary["source_lag_blocker_count"],
            "source_lag_symbols": source_lag_summary["source_lag_symbols"],
            "coverage_gap_driver_symbols": source_lag_summary["coverage_gap_driver_symbols"],
            "next_source_lag_action": source_lag_summary["next_source_lag_action"],
            "etf_metadata_report": "output/etf_metadata.csv",
            "etf_metadata_coverage_report": "output/etf_metadata_coverage.csv",
            "etf_metadata": summarize_etf_metadata(
                metadata_path=output_path / "etf_metadata.csv",
                coverage_path=output_path / "etf_metadata_coverage.csv",
            ),
            "index_map_report": "output/index_map.csv",
            "index_data_coverage_report": "output/index_data_coverage.csv",
            "index_data": summarize_index_data(
                index_map_path=output_path / "index_map.csv",
                coverage_path=output_path / "index_data_coverage.csv",
            ),
            "index_source_diagnostics_report": "output/index_source_diagnostics.csv",
            "index_source_diagnostics": summarize_index_source_diagnostics(
                report_path=output_path / "index_source_diagnostics.csv",
            ),
            "etf_metrics_report": "output/etf_metrics.csv",
            "etf_metrics_coverage_report": "output/etf_metrics_coverage.csv",
            "etf_metrics": summarize_etf_metrics(
                metrics_path=output_path / "etf_metrics.csv",
                coverage_path=output_path / "etf_metrics_coverage.csv",
            ),
        },
        "strategy_layer": {
            "passed": strategy_passed,
            "checks": strategy_rows,
            "reasons": strategy_reasons,
            "factor_score_report": "output/factor_score_report.csv",
            "factor_score_detail_report": "output/factor_score_detail.csv",
            "factor_score": summarize_factor_score(
                report_path=output_path / "factor_score_report.csv",
                detail_path=output_path / "factor_score_detail.csv",
                audit_path=output_path / "factor_score_audit.csv",
                gate_path=output_path / "factor_score_gate.csv",
            ),
            "candidate_gate_report": "output/candidate_gate.csv",
            "candidate_gate_summary_report": "output/candidate_gate_summary.csv",
            "candidate_gate": candidate_gate_summary,
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

    data_governance_status = build_data_governance_status(
        output_dir=output_path,
        diagnosis=pd.DataFrame(quality_diagnosis_rows),
        candidate_gate=pd.DataFrame(candidate_gate_rows),
        observation_pool=pd.DataFrame(observation_pool_rows),
        manual_review=pd.DataFrame(manual_review_rows),
        qa_report=report,
    )
    qa_status_rows = build_qa_status_breakdown(
        output_dir=output_path,
        qa_report=report,
        data_governance_status=data_governance_status,
    )
    qa_status_breakdown_path, qa_status_summary_path = write_qa_status_report(
        qa_status_rows,
        breakdown_path=output_path / "qa_status_breakdown.csv",
        summary_path=output_path / "qa_status_summary.csv",
    )
    qa_status_summary = summarize_qa_status(qa_status_rows)
    data_governance_status["qa_status"] = qa_status_summary
    data_governance_status["governed_failures"] = qa_status_summary["governed_failure_count"]
    data_governance_status["actionable_failures"] = (
        qa_status_summary["refresh_action_count"]
        + qa_status_summary["manual_review_action_count"]
        + qa_status_summary.get("source_diagnosis_count", 0)
    )
    data_governance_status["next_refresh_action"] = (
        source_lag_summary["next_source_lag_action"]
        if source_lag_summary["source_lag_blocker_count"]
        else "run update-data only in controlled environment or diagnose source lag"
        if qa_status_summary["refresh_action_count"]
        else "no refresh action from qa_status"
    )
    data_governance_status["next_manual_review_action"] = (
        "complete P0 manual review list; do not auto-unblock"
        if qa_status_summary["manual_review_action_count"]
        else "no manual review action from qa_status"
    )
    candidate_unblock_rows = build_candidate_unblock_plan(
        output_dir=output_path,
        candidate_gate=pd.DataFrame(candidate_gate_rows),
        diagnosis=pd.DataFrame(quality_diagnosis_rows),
        observation_pool=pd.DataFrame(observation_pool_rows),
        manual_review=pd.DataFrame(manual_review_rows),
        data_governance_status=data_governance_status,
        qa_report=report,
    )
    candidate_unblock_plan_path, candidate_unblock_summary_path = write_candidate_unblock_plan(
        candidate_unblock_rows,
        report_path=output_path / "candidate_unblock_plan.csv",
        summary_path=output_path / "candidate_unblock_summary.csv",
    )
    candidate_unblock_summary = summarize_candidate_unblock_plan(candidate_unblock_rows)
    data_governance_status["candidate_unblock_status"] = candidate_unblock_summary
    data_governance_status["immediate_eligible_count"] = candidate_unblock_summary["immediate_eligible_count"]
    data_governance_status["estimated_unblockable_by_waiting_count"] = candidate_unblock_summary["estimated_unblockable_by_waiting_count"]
    data_governance_status["candidate_next_action"] = candidate_unblock_summary["next_recommended_action"]
    report["strategy_layer"]["candidate_unblock_plan_report"] = str(candidate_unblock_plan_path)
    report["strategy_layer"]["candidate_unblock_summary_report"] = str(candidate_unblock_summary_path)
    report["strategy_layer"]["candidate_unblock"] = candidate_unblock_summary
    factor_008b_rows = build_008b_readiness_check(
        output_dir=output_path,
        candidate_gate=pd.DataFrame(candidate_gate_rows),
        candidate_unblock=pd.DataFrame(candidate_unblock_rows),
        manual_review=pd.DataFrame(manual_review_rows),
        data_governance_status=data_governance_status,
        qa_report=report,
    )
    factor_008b_report_path, factor_008b_summary_path = write_008b_readiness_report(
        factor_008b_rows,
        report_path=output_path / "factor_008b_readiness.csv",
        summary_path=output_path / "factor_008b_readiness_summary.csv",
    )
    factor_008b_summary = summarize_008b_readiness(factor_008b_rows)
    factor_008b_summary["factor_008b_readiness_report"] = str(factor_008b_report_path)
    factor_008b_summary["factor_008b_readiness_summary_report"] = str(factor_008b_summary_path)
    report["strategy_layer"].setdefault("factor_score", {}).update(factor_008b_summary)
    data_governance_status["factor_008b_readiness_status"] = factor_008b_summary["readiness_status"]
    data_governance_status["factor_008b_blockers"] = factor_008b_summary["blocking_items"]
    data_governance_status["factor_008b_next_action"] = factor_008b_summary["next_recommended_action"]
    data_governance_status["allowed_to_enter_008b"] = data_governance_status["allowed_to_enter_008b"] and factor_008b_summary["allowed_to_enter_008b"]
    index_007b_rows = build_007b_readiness_check(output_dir=output_path, qa_report=report)
    index_007b_unlock_plan = build_index_unlock_plan(output_dir=output_path)
    index_007b_report_path, index_007b_unlock_path, index_007b_summary_path = write_007b_readiness_report(
        index_007b_rows,
        index_007b_unlock_plan,
        report_path=output_path / "index_007b_readiness.csv",
        unlock_plan_path=output_path / "index_007b_unlock_plan.csv",
        summary_path=output_path / "index_007b_readiness_summary.csv",
    )
    index_007b_summary = summarize_007b_readiness(index_007b_rows)
    index_007b_summary["index_007b_readiness_report"] = str(index_007b_report_path)
    index_007b_summary["index_007b_unlock_plan_report"] = str(index_007b_unlock_path)
    index_007b_summary["index_007b_readiness_summary_report"] = str(index_007b_summary_path)
    report["data_layer"]["index_007b_readiness"] = index_007b_summary
    etf_007b_rows = build_007b_small_scope_report(output_dir=output_path)
    etf_007b_report_path, etf_007b_summary_path = write_007b_small_scope_report(
        etf_007b_rows,
        report_path=output_path / "etf_007b_metrics_report.csv",
        summary_path=output_path / "etf_007b_metrics_summary.csv",
        readiness_summary=index_007b_summary,
    )
    etf_007b_summary = summarize_007b_small_scope(
        etf_007b_rows,
        report_path=etf_007b_report_path,
        readiness_summary=index_007b_summary,
    )
    etf_007b_summary["etf_007b_metrics_report"] = str(etf_007b_report_path)
    etf_007b_summary["etf_007b_metrics_summary_report"] = str(etf_007b_summary_path)
    report["data_layer"]["etf_007b_metrics"] = etf_007b_summary
    data_governance_status["index_007b_readiness_status"] = index_007b_summary["readiness_status"]
    data_governance_status["allowed_to_enter_007b_scope"] = index_007b_summary.get("allowed_to_enter_007b_scope", "blocked")
    data_governance_status["index_007b_full_scope_available"] = bool(index_007b_summary.get("full_scope_available", False))
    data_governance_status["index_007b_blockers"] = index_007b_summary["blocking_items"]
    data_governance_status["index_007b_next_action"] = index_007b_summary["next_recommended_action"]
    data_governance_status["allowed_to_enter_007b"] = bool(index_007b_summary["allowed_to_enter_007b"])
    data_governance_status["etf_007b_status"] = etf_007b_summary["status"]
    data_governance_status["etf_007b_scope"] = etf_007b_summary["scope"]
    data_governance_status["etf_007b_computable_count"] = etf_007b_summary["computed_valid_count"]
    data_governance_status["etf_007b_full_scope_available"] = bool(etf_007b_summary["full_scope_available"])
    data_governance_status["etf_007b_next_action"] = "keep ETF-GAP-007B as a research-only small-scope report; do not connect to factor_score or candidate_gate"
    write_data_governance_status(data_governance_status, path=output_path / "data_governance_status.json")
    write_data_governance_runbook(data_governance_status, path=Path("docs") / "research" / "data_governance_runbook.md")
    data_governance_summary = {
        "data_governance_runbook": "docs/research/data_governance_runbook.md",
        "data_governance_status_report": "output/data_governance_status.json",
        "allowed_to_enter_008b": data_governance_status["allowed_to_enter_008b"],
        "allowed_to_enter_007b": data_governance_status["allowed_to_enter_007b"],
        "allowed_to_enter_007b_scope": data_governance_status.get("allowed_to_enter_007b_scope", "blocked"),
        "next_recommended_action": data_governance_status["next_recommended_action"],
        "blocking_reasons": data_governance_status["blocking_reasons"],
    }
    report["data_layer"]["data_governance"] = data_governance_summary
    report["data_layer"].update(data_governance_summary)
    report["data_layer"]["qa_status"] = qa_status_summary
    report["data_layer"].update(
        {
            "qa_status_breakdown_report": str(qa_status_breakdown_path),
            "qa_status_summary_report": str(qa_status_summary_path),
            "hard_failure_count": qa_status_summary["hard_failure_count"],
            "governed_failure_count": qa_status_summary["governed_failure_count"],
            "refresh_action_count": qa_status_summary["refresh_action_count"],
            "source_diagnosis_count": qa_status_summary["source_diagnosis_count"],
            "wait_for_history_count": qa_status_summary["wait_for_history_count"],
            "manual_review_action_count": qa_status_summary["manual_review_action_count"],
            "blocks_007b": qa_status_summary["blocks_007b"],
            "blocks_008b": qa_status_summary["blocks_008b"],
            "next_recommended_action": qa_status_summary["next_recommended_action"],
        }
    )

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


def command_diagnose_data_quality() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    rows = build_quality_remediation_plan(output_dir=output_path)
    report_path, summary_path = write_quality_diagnosis_report(
        rows,
        report_path=output_path / "data_quality_diagnosis.csv",
        summary_path=output_path / "data_quality_diagnosis_summary.csv",
    )
    merge_quality_diagnosis_into_qa_report(output_path / "qa_report.json", rows=rows)
    summary = summarize_quality_diagnosis(rows)
    print(f"Data quality diagnosis report: {report_path}")
    print(f"Data quality diagnosis summary: {summary_path}")
    print(f"Diagnosed failed ETF count: {summary['total_failed']}")
    print(f"Short history: {summary['short_history_count']}")
    print(f"Stale cache: {summary['stale_cache_count']}")
    print(f"Missing cache: {summary['missing_cache_count']}")
    print(f"Abnormal return: {summary['abnormal_return_count']}")
    print(f"Low liquidity: {summary['low_liquidity_count']}")
    print(f"Refresh needed: {summary['refresh_needed_count']}")
    print(f"Manual review required: {summary['manual_review_required_count']}")
    print(f"Candidate excluded: {summary['candidate_excluded_count']}")
    return summary


def command_build_candidate_gate() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    rows = build_candidate_gate_report(output_dir=output_path)
    report_path, summary_path = write_candidate_gate_report(
        rows,
        report_path=output_path / "candidate_gate.csv",
        summary_path=output_path / "candidate_gate_summary.csv",
    )
    merge_candidate_gate_into_qa_report(output_path / "qa_report.json", rows=rows)
    summary = summarize_candidate_gate(rows)
    print(f"Candidate gate report: {report_path}")
    print(f"Candidate gate summary: {summary_path}")
    print(f"Total symbols: {summary['total_symbols']}")
    print(f"Eligible: {summary['eligible_count']}")
    print(f"Observation only: {summary['observation_only_count']}")
    print(f"Blocked: {summary['blocked_count']}")
    print(f"Blocked short history: {summary['blocked_short_history_count']}")
    print(f"Blocked manual review: {summary['blocked_manual_review_count']}")
    print(f"Blocked factor gate: {summary['blocked_factor_gate_count']}")
    print(f"Blocked no used factors: {summary['blocked_no_used_factors_count']}")
    return summary


def command_build_observation_pool() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    rows = build_short_history_observation_pool(output_dir=output_path)
    report_path, summary_path = write_observation_pool_report(
        rows,
        report_path=output_path / "short_history_observation_pool.csv",
        summary_path=output_path / "short_history_observation_summary.csv",
    )
    merge_observation_pool_into_qa_report(output_path / "qa_report.json", rows=rows)
    summary = summarize_observation_pool(rows)
    print(f"Short-history observation pool report: {report_path}")
    print(f"Short-history observation summary: {summary_path}")
    print(f"Total observation count: {summary['total_observation_count']}")
    print(f"Very short history: {summary['very_short_history_count']}")
    print(f"Low liquidity watch: {summary['low_liquidity_watch_count']}")
    print(f"Manual review required: {summary['manual_review_required_count']}")
    print(f"Estimated eligible within 20 trading days: {summary['estimated_eligible_within_20d_count']}")
    print(f"Estimated eligible within 60 trading days: {summary['estimated_eligible_within_60d_count']}")
    print(f"Unknown estimate: {summary['unknown_estimate_count']}")
    return summary


def command_build_manual_review_list() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    rows = build_manual_review_list(output_dir=output_path)
    report_path, summary_path = write_manual_review_report(
        rows,
        report_path=output_path / "manual_review_list.csv",
        summary_path=output_path / "manual_review_summary.csv",
    )
    merge_manual_review_into_qa_report(output_path / "qa_report.json", rows=rows)
    summary = summarize_manual_review(rows)
    print(f"Manual review list report: {report_path}")
    print(f"Manual review summary: {summary_path}")
    print(f"Manual review count: {summary['manual_review_count']}")
    print(f"P0 manual review: {summary['p0_manual_review_count']}")
    print(f"Abnormal return review: {summary['abnormal_return_review_count']}")
    print(f"Low liquidity review: {summary['low_liquidity_review_count']}")
    print(f"Very short history review: {summary['very_short_history_review_count']}")
    return summary


def command_summarize_data_governance() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    status = build_data_governance_status(output_dir=output_path)
    status_path = write_data_governance_status(status, path=output_path / "data_governance_status.json")
    runbook_path = write_data_governance_runbook(status, path=Path("docs") / "research" / "data_governance_runbook.md")
    merge_data_governance_into_qa_report(output_path / "qa_report.json", status=status)
    print(f"Data governance status: {status_path}")
    print(f"Data governance runbook: {runbook_path}")
    print(f"Allowed to enter 008B: {status['allowed_to_enter_008b']}")
    print(f"Allowed to enter 007B: {status['allowed_to_enter_007b']}")
    print(f"Allowed 007B scope: {status.get('allowed_to_enter_007b_scope', 'blocked')}")
    print(f"ETF 007B computable count: {status.get('etf_007b_computable_count', 0)}")
    print(f"ETF 007B full scope available: {status.get('etf_007b_full_scope_available', False)}")
    print(f"Source lag blockers: {status.get('source_lag_blocker_count', 0)}")
    print(f"Coverage gap drivers: {', '.join(status.get('coverage_gap_driver_symbols', [])) if status.get('coverage_gap_driver_symbols') else 'None'}")
    print(f"Next recommended action: {status['next_recommended_action']}")
    print(f"Blocking reasons: {status['blocking_reasons']}")
    return status


def command_summarize_qa_status() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    rows = build_qa_status_breakdown(output_dir=output_path)
    breakdown_path, summary_path = write_qa_status_report(
        rows,
        breakdown_path=output_path / "qa_status_breakdown.csv",
        summary_path=output_path / "qa_status_summary.csv",
    )
    summary = summarize_qa_status(rows)
    merge_qa_status_into_qa_report(output_path / "qa_report.json", summary=summary)
    print(f"QA status breakdown: {breakdown_path}")
    print(f"QA status summary: {summary_path}")
    print(f"Hard failure rows: {summary['hard_failure_count']}")
    print(f"Governed failure rows: {summary['governed_failure_count']}")
    print(f"Refresh action count: {summary['refresh_action_count']}")
    print(f"Source diagnosis count: {summary['source_diagnosis_count']}")
    print(f"Wait-for-history count: {summary['wait_for_history_count']}")
    print(f"Manual review action count: {summary['manual_review_action_count']}")
    print(f"Blocks 007B: {summary['blocks_007b']}")
    print(f"Blocks 008B: {summary['blocks_008b']}")
    print(f"Next recommended action: {summary['next_recommended_action']}")
    return summary


def command_build_candidate_unblock_plan() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    rows = build_candidate_unblock_plan(output_dir=output_path)
    plan_path, summary_path = write_candidate_unblock_plan(
        rows,
        report_path=output_path / "candidate_unblock_plan.csv",
        summary_path=output_path / "candidate_unblock_summary.csv",
    )
    summary = summarize_candidate_unblock_plan(rows)
    merge_candidate_unblock_into_qa_report(output_path / "qa_report.json", summary=summary)
    status_path = output_path / "data_governance_status.json"
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["candidate_unblock_status"] = summary
        status["immediate_eligible_count"] = summary["immediate_eligible_count"]
        status["estimated_unblockable_by_waiting_count"] = summary["estimated_unblockable_by_waiting_count"]
        status["candidate_next_action"] = summary["next_recommended_action"]
        write_data_governance_status(status, path=status_path)
    print(f"Candidate unblock plan: {plan_path}")
    print(f"Candidate unblock summary: {summary_path}")
    print(f"Total symbols: {summary['total_symbols']}")
    print(f"Immediate eligible: {summary['immediate_eligible_count']}")
    print(f"Wait for history: {summary['wait_for_history_count']}")
    print(f"Manual review required: {summary['manual_review_required_count']}")
    print(f"No used factors: {summary['no_used_factors_count']}")
    print(f"Factor gate blocked after primary fix: {summary['factor_gate_blocked_count']}")
    print(f"Benchmark dependency missing: {summary['benchmark_dependency_missing_count']}")
    print(f"Next recommended action: {summary['next_recommended_action']}")
    return summary


def command_check_factor_008b_readiness() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    rows = build_008b_readiness_check(output_dir=output_path)
    report_path, summary_path = write_008b_readiness_report(
        rows,
        report_path=output_path / "factor_008b_readiness.csv",
        summary_path=output_path / "factor_008b_readiness_summary.csv",
    )
    summary = summarize_008b_readiness(rows)
    summary["factor_008b_readiness_report"] = str(report_path)
    summary["factor_008b_readiness_summary_report"] = str(summary_path)
    merge_008b_readiness_into_qa_report(output_path / "qa_report.json", summary=summary)
    status_path = output_path / "data_governance_status.json"
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["factor_008b_readiness_status"] = summary["readiness_status"]
        status["factor_008b_blockers"] = summary["blocking_items"]
        status["factor_008b_next_action"] = summary["next_recommended_action"]
        status["allowed_to_enter_008b"] = bool(status.get("allowed_to_enter_008b", False)) and summary["allowed_to_enter_008b"]
        write_data_governance_status(status, path=status_path)
    print(f"Factor 008B readiness report: {report_path}")
    print(f"Factor 008B readiness summary: {summary_path}")
    print(f"Readiness status: {summary['readiness_status']}")
    print(f"Allowed to enter 008B: {summary['allowed_to_enter_008b']}")
    print(f"Blocking items: {', '.join(summary['blocking_items']) if summary['blocking_items'] else 'None'}")
    print(f"Warning items: {', '.join(summary['warning_items']) if summary['warning_items'] else 'None'}")
    print(f"Next recommended action: {summary['next_recommended_action']}")
    return summary


def command_check_index_007b_readiness() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    qa_path = output_path / "qa_report.json"
    if qa_path.exists():
        report = json.loads(qa_path.read_text(encoding="utf-8"))
        data_layer = report.setdefault("data_layer", {})
        data_layer["index_data"] = summarize_index_data(
            index_map_path=output_path / "index_map.csv",
            coverage_path=output_path / "index_data_coverage.csv",
        )
        data_layer["etf_metrics"] = summarize_etf_metrics(
            metrics_path=output_path / "etf_metrics.csv",
            coverage_path=output_path / "etf_metrics_coverage.csv",
        )
        qa_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = build_007b_readiness_check(output_dir=output_path)
    unlock_plan = build_index_unlock_plan(output_dir=output_path)
    report_path, unlock_path, summary_path = write_007b_readiness_report(
        rows,
        unlock_plan,
        report_path=output_path / "index_007b_readiness.csv",
        unlock_plan_path=output_path / "index_007b_unlock_plan.csv",
        summary_path=output_path / "index_007b_readiness_summary.csv",
    )
    summary = summarize_007b_readiness(rows)
    summary["index_007b_readiness_report"] = str(report_path)
    summary["index_007b_unlock_plan_report"] = str(unlock_path)
    summary["index_007b_readiness_summary_report"] = str(summary_path)
    merge_007b_readiness_into_qa_report(output_path / "qa_report.json", summary=summary)
    status_path = output_path / "data_governance_status.json"
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["index_007b_readiness_status"] = summary["readiness_status"]
        status["allowed_to_enter_007b_scope"] = summary.get("allowed_to_enter_007b_scope", "blocked")
        status["index_007b_full_scope_available"] = bool(summary.get("full_scope_available", False))
        status["index_007b_blockers"] = summary["blocking_items"]
        status["index_007b_next_action"] = summary["next_recommended_action"]
        status["allowed_to_enter_007b"] = bool(summary["allowed_to_enter_007b"])
        write_data_governance_status(status, path=status_path)
    print(f"Index 007B readiness report: {report_path}")
    print(f"Index 007B unlock plan: {unlock_path}")
    print(f"Index 007B readiness summary: {summary_path}")
    print(f"Readiness status: {summary['readiness_status']}")
    print(f"Allowed to enter 007B: {summary['allowed_to_enter_007b']}")
    print(f"Allowed 007B scope: {summary.get('allowed_to_enter_007b_scope', 'blocked')}")
    print(f"Usable benchmark count: {summary['usable_benchmark_count']}")
    print(f"Index cache valid count: {summary['index_cache_valid_count']}")
    print(f"Tracking error computable count: {summary['tracking_error_computable_count']}")
    print(f"Relative return computable count: {summary['relative_return_computable_count']}")
    print(f"Blocking items: {', '.join(summary['blocking_items']) if summary['blocking_items'] else 'None'}")
    print(f"Warning items: {', '.join(summary['warning_items']) if summary['warning_items'] else 'None'}")
    print(f"Next recommended action: {summary['next_recommended_action']}")
    return summary


def command_validate_etf_007b_metrics() -> dict[str, Any]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    readiness = summarize_007b_readiness(report_path=output_path / "index_007b_readiness.csv")
    rows = build_007b_small_scope_report(output_dir=output_path)
    report_path, summary_path = write_007b_small_scope_report(
        rows,
        report_path=output_path / "etf_007b_metrics_report.csv",
        summary_path=output_path / "etf_007b_metrics_summary.csv",
        readiness_summary=readiness,
    )
    summary = summarize_007b_small_scope(rows, report_path=report_path, readiness_summary=readiness)
    summary["etf_007b_metrics_report"] = str(report_path)
    summary["etf_007b_metrics_summary_report"] = str(summary_path)
    merge_007b_small_scope_into_qa_report(output_path / "qa_report.json", summary=summary)
    status_path = output_path / "data_governance_status.json"
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["etf_007b_status"] = summary["status"]
        status["etf_007b_scope"] = summary["scope"]
        status["etf_007b_computable_count"] = summary["computed_valid_count"]
        status["etf_007b_full_scope_available"] = bool(summary["full_scope_available"])
        status["etf_007b_next_action"] = "keep ETF-GAP-007B as a research-only small-scope report; do not connect to factor_score or candidate_gate"
        write_data_governance_status(status, path=status_path)
    print(f"ETF 007B metrics report: {report_path}")
    print(f"ETF 007B metrics summary: {summary_path}")
    print(f"Status: {summary['status']}")
    print(f"Scope: {summary['scope']}")
    print(f"Full scope available: {summary['full_scope_available']}")
    print(f"Computed valid: {summary['computed_valid_count']}")
    print(f"Tracking error valid: {summary['tracking_error_valid_count']}")
    print(f"Relative return valid: {summary['relative_return_valid_count']}")
    print(f"No index cache: {summary['no_index_cache_count']}")
    print(f"Missing benchmark: {summary['missing_benchmark_count']}")
    print(f"Insufficient overlap: {summary['insufficient_overlap_count']}")
    return summary


def command_plan_cache_refresh() -> list[dict[str, Any]]:
    output_path = Path("output")
    output_path.mkdir(parents=True, exist_ok=True)
    etf_pool = load_etf_pool()
    rows = build_refresh_plan(etf_pool, output_dir=output_path)
    path = write_refresh_plan(rows, output_path / "cache_refresh_plan.csv")
    summary = summarize_refresh_plan(rows)
    print(f"Cache refresh dry-run plan generated: {path}")
    print(f"Total candidates: {summary['total_candidates']}")
    print(f"Priority counts: {summary['priority_counts']}")
    print(f"Reason counts: {summary['reason_counts']}")
    print(f"Safe to auto refresh: {summary['safe_to_auto_refresh_count']}")
    print(f"Manual review required: {summary['manual_review_required_count']}")
    return rows


def command_pilot_refresh(
    pool: str | None = None,
    symbols: str | None = None,
    max_count: int = 11,
    dry_run: bool = False,
    include_manual_review: bool = False,
) -> list[dict[str, Any]]:
    if not pool and not symbols:
        raise SystemExit("pilot-refresh requires --pool core_11 or --symbols 510300,159915")
    if max_count > 11:
        raise SystemExit("pilot-refresh refuses max-count > 11")
    rows, manifest_path = run_pilot_refresh(
        pool=pool,
        symbols=symbols,
        max_count=max_count,
        dry_run=dry_run,
        include_manual_review=include_manual_review,
        command=" ".join([str(Path(sys.executable)), "main.py", *sys.argv[1:]]),
    )
    summary = summarize_pilot_refresh(rows, report_path=Path("output") / "pilot_refresh_report.csv")
    print("Pilot refresh report generated: output/pilot_refresh_report.csv")
    if manifest_path is not None:
        print(f"Backup manifest: {manifest_path}")
    print(f"Attempted: {summary['attempted_count']}")
    print(f"Refreshed OK: {summary['refreshed_ok_count']}")
    print(f"Skipped: {summary['skipped_count']}")
    print(f"Failed: {summary['failed_count']}")
    print(f"Metadata written: {summary['metadata_written_count']}")
    print(f"End-date improved: {summary['end_date_improved_count']}")
    return rows


def command_repair_missing_cache(
    symbols: str | None = None,
    max_count: int = 10,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if max_count > 10:
        raise SystemExit("repair-missing-cache refuses max-count > 10")
    rows, manifest_path = repair_missing_cache(
        symbols=symbols,
        max_count=max_count,
        dry_run=dry_run,
        command=" ".join([str(Path(sys.executable)), "main.py", *sys.argv[1:]]),
    )
    summary = summarize_missing_cache_repair(rows, report_path=Path("output") / "missing_cache_repair_report.csv")
    print("Missing cache repair report generated: output/missing_cache_repair_report.csv")
    if manifest_path is not None:
        print(f"Backup manifest: {manifest_path}")
    print(f"Attempted: {summary['attempted_count']}")
    print(f"Repaired OK: {summary['repaired_ok_count']}")
    print(f"Download failed: {summary['download_failed_count']}")
    print(f"Still missing cache: {summary['still_missing_cache_count']}")
    print(f"Metadata written: {summary['metadata_written_count']}")
    print(f"Quality failed after repair: {summary['quality_failed_after_repair_count']}")
    return rows


def command_eval_source_preference(
    pool: str | None = None,
    symbols: str | None = None,
    max_count: int = 20,
) -> list[dict[str, Any]]:
    if max_count > 20:
        raise SystemExit("eval-source-preference refuses max-count > 20")
    rows, audit_path, run_dir = run_source_preference_evaluation(pool=pool, symbols=symbols, max_count=max_count)
    summary = summarize_source_preference_audit(rows, report_path=audit_path)
    print(f"Source preference audit generated: {audit_path}")
    print(f"Temporary evaluation directory: {run_dir}")
    print(f"Total symbols: {summary['total_symbols']}")
    print(f"Total candidates: {summary['total_candidates']}")
    print(f"Sina success: {summary['sina_success_count']}")
    print(f"EM qfq success: {summary['em_qfq_success_count']}")
    print(f"EM qfq safe to promote: {summary['em_qfq_safe_to_promote_count']}")
    print(f"Manual review required: {summary['manual_review_required_count']}")
    print(f"Preferred candidate counts: {summary['preferred_candidate_counts']}")
    return rows


def command_diagnose_source(
    symbols: str | None = None,
    max_count: int = 5,
    timeout: float = 8.0,
    retries: int = 1,
) -> list[dict[str, Any]]:
    if not symbols and max_count > 5:
        raise SystemExit("diagnose-source default mode refuses max-count > 5 without explicit --symbols")
    rows, report_path = run_source_diagnostics(symbols=symbols, max_count=max_count, timeout=timeout, retries=retries)
    summary = summarize_source_diagnostics(rows, report_path=report_path)
    print(f"Source diagnostics report generated: {report_path}")
    print(f"Total symbols: {summary['total_symbols']}")
    print(f"Total checks: {summary['total_checks']}")
    print(f"Sina success: {summary['sina_success_count']}")
    print(f"EM qfq success: {summary['em_qfq_success_count']}")
    print(f"EM none success: {summary['em_none_success_count']}")
    print(f"Proxy errors: {summary['proxy_error_count']}")
    print(f"Timeouts: {summary['timeout_count']}")
    print(f"Suggested action: {summary['suggested_action']}")
    return rows


def command_diagnose_source_lag() -> dict[str, Any]:
    output_path = Path("output")
    rows = build_source_lag_report(output_dir=output_path)
    report_path, summary_path = write_source_lag_report(
        rows,
        report_path=output_path / "source_lag_report.csv",
        summary_path=output_path / "source_lag_summary.csv",
    )
    summary = summarize_source_lag(rows)
    merge_source_lag_into_qa_report(output_path / "qa_report.json", summary=summary)
    print(f"Source lag report generated: {report_path}")
    print(f"Source lag summary generated: {summary_path}")
    print(f"Source lag symbols: {summary['source_lag_count']}")
    print(f"Source lag blockers: {summary['source_lag_blocker_count']}")
    print(f"Coverage gap drivers: {', '.join(summary['coverage_gap_driver_symbols']) if summary['coverage_gap_driver_symbols'] else 'None'}")
    print(f"Next source lag action: {summary['next_source_lag_action']}")
    return summary


def command_update_etf_metadata(
    source: str = "akshare",
    max_count: int | None = None,
    dry_run: bool = False,
) -> Any:
    try:
        frame, metadata_path, coverage_path = update_etf_metadata(source=source, max_count=max_count, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"update-etf-metadata failed: {exc}") from exc
    summary = summarize_etf_metadata(metadata_path=metadata_path, coverage_path=coverage_path)
    print(f"ETF metadata generated: {metadata_path}")
    print(f"ETF metadata coverage generated: {coverage_path}")
    print(f"Total ETFs: {summary['total_etfs']}")
    print(f"Missing required fields: {summary['missing_required_fields']}")
    print(f"Low coverage fields: {summary['low_coverage_fields'][:20]}")
    print(f"Metadata source: {summary['metadata_source']}")
    if dry_run:
        print("Dry run: wrote preview files only; formal etf_metadata.csv was not updated.")
    return frame


def command_update_index_data(
    max_count: int = 50,
    symbols: str | None = None,
    dry_run: bool = False,
) -> Any:
    try:
        index_map, coverage_rows, map_path, coverage_path = update_index_data(max_count=max_count, symbols=symbols, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"update-index-data failed: {exc}") from exc
    summary = summarize_index_data(index_map_path=map_path, coverage_path=coverage_path)
    usable_codes = sorted(
        {
            str(row.get("tracking_index_code", ""))
            for row in coverage_rows
            if str(row.get("usable_as_benchmark", "")).lower() in {"true", "1", "yes"}
        }
    )
    print(f"Index map generated: {map_path}")
    print(f"Index data coverage generated: {coverage_path}")
    print(f"Total index mappings: {summary['total_index_mappings']}")
    print(f"Index cache written: {summary.get('index_cache_written_count', 0)}")
    print(f"Usable benchmark mappings: {summary['usable_benchmark_count']}")
    print(f"Manual review required: {summary['manual_review_required_count']}")
    print(f"Fetch success: {summary['fetch_success_count']}")
    print(f"Fetch failed: {summary['fetch_failed_count']}")
    print(f"CSIndex usable successes: {summary.get('csindex_success_count', 0)}")
    print(f"EastMoney failures observed: {summary.get('eastmoney_failure_count', 0)}")
    print(f"Schema invalid: {summary.get('schema_invalid_count', 0)}")
    print(f"Usable benchmark index codes: {', '.join(usable_codes) if usable_codes else 'None'}")
    if coverage_rows:
        failures = [row for row in coverage_rows if not str(row.get("fetch_success", "")).lower() in {"true", "1", "yes"}]
        for row in failures[:10]:
            print(f"  ERR {row.get('tracking_index_code')} {row.get('tracking_index_name')}: {row.get('failure_reason')}")
    if dry_run:
        print("Dry run: wrote preview files only; formal index cache was not updated.")
    return index_map


def command_compute_etf_metrics(
    max_count: int | None = 50,
    symbols: str | None = None,
    min_overlap_days: int = 60,
    dry_run: bool = False,
) -> Any:
    try:
        metrics, coverage = compute_etf_metrics(
            max_count=max_count,
            symbols=symbols,
            min_overlap_days=min_overlap_days,
        )
        suffix = "_preview" if dry_run else ""
        metrics_path, coverage_path = write_etf_metrics_report(
            metrics,
            coverage,
            metrics_path=Path("output") / f"etf_metrics{suffix}.csv",
            coverage_path=Path("output") / f"etf_metrics_coverage{suffix}.csv",
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"compute-etf-metrics failed: {exc}") from exc
    summary = summarize_etf_metrics(metrics_path=metrics_path, coverage_path=coverage_path)
    print(f"ETF metrics generated: {metrics_path}")
    print(f"ETF metrics coverage generated: {coverage_path}")
    print(f"Total ETFs: {summary['total_etfs']}")
    print(f"Metrics computable: {summary['metrics_computable_count']}")
    print(f"Tracking error computable: {summary['tracking_error_computable_count']}")
    print(f"Relative return computable: {summary['relative_return_computable_count']}")
    print(f"Discount/premium available: {summary['discount_premium_available_count']}")
    print(f"No index cache: {summary['no_index_cache_count']}")
    print(f"Missing benchmark: {summary['missing_benchmark_count']}")
    print(f"Insufficient overlap: {summary['insufficient_overlap_count']}")
    if dry_run:
        print("Dry run: wrote preview files only; formal etf_metrics.csv was not updated.")
    return metrics


def command_compute_factor_score(
    config_path: str = "config/factor_score.yaml",
    max_count: int | None = 50,
    symbols: str | None = None,
    dry_run: bool = False,
) -> Any:
    try:
        report, detail = compute_factor_score_reports(config_path=config_path, symbols=symbols, max_count=max_count)
        suffix = "_preview" if dry_run else ""
        report_path, detail_path = write_factor_score_reports(
            report,
            detail,
            report_path=Path("output") / f"factor_score_report{suffix}.csv",
            detail_path=Path("output") / f"factor_score_detail{suffix}.csv",
        )
        audit = build_factor_score_audit_from_files(report_path=report_path, detail_path=detail_path)
        audit_path = write_factor_score_audit(audit, audit_path=Path("output") / f"factor_score_audit{suffix}.csv")
        gate = evaluate_factor_score_gate_from_files(
            config_path=config_path,
            report_path=report_path,
            detail_path=detail_path,
            audit_path=audit_path,
        )
        gate_path = write_factor_score_gate_report(gate, gate_path=Path("output") / f"factor_score_gate{suffix}.csv")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"compute-factor-score failed: {exc}") from exc
    summary = summarize_factor_score(report_path=report_path, detail_path=detail_path, audit_path=audit_path, gate_path=gate_path, config_path=config_path)
    print(f"Factor score report generated: {report_path}")
    print(f"Factor score detail generated: {detail_path}")
    print(f"Factor score audit generated: {audit_path}")
    print(f"Factor score gate generated: {gate_path}")
    print(f"Total symbols: {summary['total_symbols']}")
    print(f"Score computable: {summary['score_computable_count']}")
    print(f"Unable to score: {summary['unable_to_score_count']}")
    print(f"Computable ratio: {summary['computable_ratio']}")
    print(f"Audit status: {summary['audit_status']}")
    print(f"Gate status: {summary['gate_status']}")
    print(f"Enabled factors: {summary['enabled_factor_count']}")
    print(f"Used factor counts: {summary['used_factor_counts']}")
    print(f"Skipped factor counts: {summary['skipped_factor_counts']}")
    if dry_run:
        print("Dry run: wrote preview files only; formal factor score reports were not updated.")
    return report


def command_diagnose_index_source(
    index_codes: str | None = None,
    max_count: int = 10,
) -> list[dict[str, Any]]:
    if max_count > 10:
        raise SystemExit("diagnose-index-source refuses max-count > 10")
    rows = diagnose_index_source_candidates(index_codes=index_codes, max_count=max_count)
    report_path = write_index_source_diagnostics_report(rows)
    summary = summarize_index_source_diagnostics(rows, report_path=report_path)
    families = sorted({str(row.get("source_family", "")) for row in rows if row.get("source_family")})
    apis = sorted({str(row.get("api_name", "")) for row in rows if row.get("api_name")})
    codes = sorted({str(row.get("index_code", "")) for row in rows if row.get("index_code")})
    print(f"Index source diagnostics report generated: {report_path}")
    print(f"Index codes checked: {', '.join(codes) if codes else 'None'}")
    print(f"API candidates: {', '.join(apis) if apis else 'None'}")
    print(f"Source families: {', '.join(families) if families else 'None'}")
    print(f"Total API candidates: {summary['total_api_candidates']}")
    print(f"Call success: {summary['success_count']}")
    print(f"Usable source candidates: {summary['usable_source_count']}")
    print(f"EastMoney failures: {summary['eastmoney_failure_count']}")
    print(f"Proxy errors: {summary['proxy_error_count']}")
    print(f"Timeouts: {summary['timeout_count']}")
    print(f"Suggested action: {summary['suggested_action']}")
    return rows


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


def _a_share_trade_calendar(start: str = "20100101", end: str = "20301231") -> pd.DatetimeIndex:
    return get_trading_days(start_date=pd.Timestamp(start), end_date=pd.Timestamp(end))


def _next_a_share_trade_date(signal_date: pd.Timestamp) -> pd.Timestamp:
    return next_trading_day(pd.Timestamp(signal_date).normalize())


def _execution_status(execution_date: pd.Timestamp, now: datetime | None = None) -> str:
    current = now or datetime.now(MARKET_TZ)
    execution_day = pd.Timestamp(execution_date).date()
    current_day = current.date()
    if current_day < execution_day:
        return "等待执行"
    if current_day > execution_day:
        return "信号已过期，请重新生成"
    if current.time() < time(9, 35):
        return "等待开盘确认"
    if time(9, 35) <= current.time() <= time(10, 0):
        return "建议执行窗口"
    return "今日执行窗口已过"


def _resolve_effective_signal_date(dates: pd.DatetimeIndex, requested_signal_date: str | None) -> tuple[pd.Timestamp, str, str]:
    all_dates = pd.DatetimeIndex(sorted(pd.to_datetime(dates).unique()))
    if all_dates.empty:
        raise ValueError("No market dates are available")
    if not requested_signal_date:
        return pd.NaT, "", ""

    requested = pd.Timestamp(requested_signal_date).normalize()
    latest = pd.Timestamp(all_dates[-1]).normalize()
    if requested > latest:
        raise ValueError(f"requested signal date {requested.date()} is later than latest data date {latest.date()}")
    usable = all_dates[all_dates <= requested]
    if usable.empty:
        raise ValueError(f"requested signal date {requested.date()} is before available market data")
    effective = pd.Timestamp(usable[-1])
    if effective >= latest:
        return effective, str(requested.date()), PENDING_EXECUTE_DATE_TEXT
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
            if pd.isna(value):
                clean[key] = None
            elif hasattr(value, "item"):
                clean[key] = value.item()
            else:
                clean[key] = value
        records.append(clean)
    return records


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


def _latest_strategy_signal(
    config_path: str,
    strategy_name: str,
    output_path: str | Path,
    requested_signal_date: str | None = None,
    observation_cash: float | None = None,
    use_cache: bool = False,
    etf_pool: list[dict[str, str]] | None = None,
    market_data: dict[str, pd.DataFrame] | None = None,
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
    manual_signal_date, requested_date_text, execute_date_text = _resolve_effective_signal_date(
        result["strategy"].close.index,
        requested_signal_date,
    )
    signal_date_source = "manual" if requested_signal_date else "auto"
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
        signal_date, execute_date_text = _latest_signal_date_with_pending_execute(result["strategy"].close.index, signal_dates)
    else:
        signal_date = manual_signal_date
    execution_date = pd.Timestamp(execute_date_text)
    generated_at = datetime.now(MARKET_TZ).isoformat(timespec="seconds")
    execution_status = _execution_status(execution_date)
    text = generate_weekly_signal_text(
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
        rebalance_frequency=str(raw.get("frequency", raw.get("rebalance_frequency", "weekly"))),
        rebalance_timing=engine.strategy_config.rebalance_timing,
        rebalance_day=engine.strategy_config.rebalance_day,
        rebalance_day_of_month=engine.strategy_config.rebalance_day_of_month,
        rebalance_roll=engine.strategy_config.rebalance_roll,
        signal_date=signal_date,
        observation_cash=observation_cash,
        market_data=engine.market_data,
    )
    plan = build_signal_trade_plan(
        strategy=result["strategy"],
        etf_info=engine.etf_info,
        signal_date=signal_date,
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
        "is_dynamic_rotation": strategy_name == "momentum_rotation_monthly",
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
        "execution_date": execute_date_text,
        "generated_at": generated_at,
        "data_latest_date": str(result["strategy"].close.index.max().date()),
        "execution_status": execution_status,
        "execution_window": EXECUTION_WINDOW,
        "execution_price_rule": EXECUTION_PRICE_RULE,
        "signal_date_source": signal_date_source,
        "signal_date": str(signal_date.date()),
        "latest_data_date": str(result["strategy"].close.index.max().date()),
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
        "buy_plan": json.dumps(plan["buy_plan"], ensure_ascii=False),
        "intraday_execution_plan": json.dumps(plan["intraday_execution_plan"], ensure_ascii=False),
        "skipped_buy_plan": json.dumps(plan["skipped_buy_plan"], ensure_ascii=False),
        "sell_plan": json.dumps(plan["sell_plan"], ensure_ascii=False),
        "hold_plan": json.dumps(plan["hold_plan"], ensure_ascii=False),
        "rank_table": json.dumps(_rank_table_records(plan["ranks"], target), ensure_ascii=False),
        "rank_table_summary": _rank_table_summary(plan["ranks"], target),
        "no_action_reason": no_action_reason,
        "position_configured": bool(current_position.get("position_configured")),
        "current_empty": bool(current_position.get("current_empty")),
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
    latest_dates = [row["latest_data_date"] for row in rows if row.get("latest_data_date")]
    signal_date = signal_dates[0] if signal_dates else "UNKNOWN"
    latest_data_date = max(latest_dates) if latest_dates else "UNKNOWN"
    current_position = ensure_current_position("config/current_position.yaml")
    allowed = _load_small_observation_status()
    main_rule = next((row.get("rebalance_rule") for row in rows if row.get("strategy_name") == "reduced_equal_weight_monthly"), "UNKNOWN")
    observation_cash = next(
        (row.get("observation_cash") for row in rows if row.get("strategy_name") == "reduced_equal_weight_monthly"),
        current_position.get("cash", 0),
    )
    lines = [
        "策略对照观察信号",
        "=" * 40,
        "总览",
        f"- signal_date / 信号日: {signal_date}",
        f"- data_latest_date / 数据最新日期: {latest_data_date}",
        f"- execution_date / 执行日: {next((row.get('execution_date') for row in rows if row.get('execution_date')), 'UNKNOWN')}",
        f"- generated_at / 生成时间: {next((row.get('generated_at') for row in rows if row.get('generated_at')), 'UNKNOWN')}",
        f"- execution_status / 执行状态: {next((row.get('execution_status') for row in rows if row.get('execution_status')), 'UNKNOWN')}",
        f"- 本次观察资金: {float(observation_cash):.2f} 元",
        f"- 当前真实现金: {float(current_position.get('cash', 0)):.2f} 元",
        f"- 当前真实持仓: {_current_position_overview(current_position)}",
        "- 稳健基准策略: reduced_equal_weight_monthly",
        "- 动态轮动策略: momentum_rotation_monthly",
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


def _main_ranking_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    main = next((row for row in rows if row.get("strategy_name") == "momentum_rotation_monthly"), rows[0] if rows else {})
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


def command_compare_signal(signal_date: str | None = None, cash: float | None = None, strategy: str | None = None, use_cache: bool = False) -> pd.DataFrame:
    signal_started = time_module.perf_counter()
    compare_items = [
        ("momentum_rotation_monthly", "config/strategy_momentum_rotation_monthly.yaml"),
        ("reduced_equal_weight_monthly", "config/strategy_reduced_equal_weight_monthly.yaml"),
        ("equal_weight_monthly", "config/strategy_equal_weight_monthly.yaml"),
        ("balanced", "config/strategy_balanced.yaml"),
        ("conservative", "config/strategy_conservative.yaml"),
    ]
    if strategy:
        if strategy not in STRATEGY_CONFIGS:
            raise ValueError(f"Unsupported strategy: {strategy}")
        compare_items = [(strategy, STRATEGY_CONFIGS[strategy])]
    shared_etf_pool: list[dict[str, str]] | None = None
    shared_market_data: dict[str, pd.DataFrame] | None = None
    if use_cache:
        shared_etf_pool = load_etf_pool()
        recent_rows = max(_signal_recent_rows(config_path) for _, config_path in compare_items)
        shared_market_data = load_market_data(
            [item["symbol"] for item in shared_etf_pool],
            allow_partial=True,
            etf_info={item["symbol"]: item for item in shared_etf_pool},
            recent_rows=recent_rows,
        )
    sections = []
    rows = []
    for strategy_name, config_path in compare_items:
        text, summary = _latest_strategy_signal(
            config_path,
            strategy_name,
            f"output/{strategy_name}_signal.txt",
            requested_signal_date=signal_date,
            observation_cash=cash,
            use_cache=use_cache,
            etf_pool=shared_etf_pool,
            market_data=shared_market_data,
        )
        sections.append(
            "\n\n"
            f"===== {summary['strategy_display_name']} / {strategy_name} ({summary['strategy_status']}) =====\n"
            f"是否动态轮动策略：{'是' if summary['is_dynamic_rotation'] else '否'}\n"
            f"策略定位：{summary['strategy_type_description']}\n"
            "【信号信息】\n"
            f"- 你选择的信号日: {summary['requested_signal_date'] or '自动使用最新可用数据'}\n"
            f"- 实际计算信号日: {summary['effective_signal_date']}\n"
            f"- 执行日: {summary['execution_date']}\n"
            f"- 当前状态: {summary['execution_status']}\n"
            f"- 生成时间: {summary['generated_at']}\n"
            f"- 数据最新日期: {summary['data_latest_date']}\n"
            f"- 建议执行时间: {summary['execution_window']}\n"
            f"- 价格规则: {summary['execution_price_rule']}\n"
            f"调仓规则：{summary['rebalance_rule']}\n"
            f"排名表摘要：{summary['rank_table_summary']}\n"
            f"{text}"
        )
        rows.append(summary)
    ranking = _main_ranking_frame(rows)
    top_lines = []
    if not ranking.empty:
        top_lines = [
            "",
            "【过滤后完整 ETF 池排名 Top 20】",
            _ranking_text(ranking, max_rows=20),
        ]
    combined = _compare_signal_overview(rows) + "\n".join(top_lines) + "\n".join(sections)
    Path("output/compare_signal.txt").write_text(combined, encoding="utf-8")
    result = pd.DataFrame(rows)
    result.to_csv("output/strategy_compare_signal.csv", index=False, encoding="utf-8-sig")
    ranking.to_csv("output/compare_signal.csv", index=False, encoding="utf-8-sig")
    ranking.to_csv("output/compare_signal_rankings.csv", index=False, encoding="utf-8-sig")
    signal_seconds = time_module.perf_counter() - signal_started
    if use_cache:
        try:
            coverage = pd.read_csv("output/data_coverage_report.csv", dtype={"symbol": str}).fillna("")
            success_count = int(coverage["success"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()) if "success" in coverage.columns else 0
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
    print("策略对照信号已生成: output/compare_signal.txt, output/strategy_compare_signal.csv")
    print("ETF 排名已生成: output/compare_signal.csv, output/compare_signal_rankings.csv")
    for _, row in result.iterrows():
        def short(value: Any, limit: int = 160) -> str:
            text = str(value)
            return text if len(text) <= limit else text[:limit] + f"...(+{len(text) - limit} chars)"

        print(
            " | ".join(
                [
                    f"strategy={row.get('strategy_name', '')}",
                    f"signal_date={row.get('effective_signal_date', '')}",
                    f"latest_data={row.get('latest_data_date', '')}",
                    f"target={short(row.get('target_symbols', ''))}",
                    f"buy={short(row.get('suggested_buy', ''))}",
                    f"sell={short(row.get('suggested_sell', ''))}",
                ]
            )
        )
    return result


def _strategy_config_from_observation(name: str) -> str:
    mapping = {
        "original": "config/strategy_original.yaml",
        "balanced": "config/strategy_balanced.yaml",
        "conservative": "config/strategy_conservative.yaml",
        "equal_weight_monthly": "config/strategy_equal_weight_monthly.yaml",
        "reduced_equal_weight_monthly": "config/strategy_reduced_equal_weight_monthly.yaml",
        "momentum_rotation_monthly": "config/strategy_momentum_rotation_monthly.yaml",
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
    command_update_data(mode="refresh" if refresh else "incremental", refresh=refresh, config_path=config_path)
    command_backtest(config_path=config_path)
    command_signal(config_path=config_path)
    print("全部流程完成: 数据、回测、周信号均已更新")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股ETF低频轮动系统")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update-data", help="更新 ETF 历史数据")
    update_parser.add_argument("--mode", choices=["incremental", "refresh", "rebuild"], default="incremental", help="Data update mode")
    update_parser.add_argument("--symbols", default=None, help="Comma-separated ETF symbols for refresh mode")
    update_parser.add_argument("--max-workers", type=int, default=6, help="Concurrent ETF download workers")
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
    subparsers.add_parser("diagnose-data-quality", help="diagnose existing ETF data-quality failures without refreshing cache")
    subparsers.add_parser("build-candidate-gate", help="build candidate eligibility gate without changing strategy outputs")
    subparsers.add_parser("build-observation-pool", help="build short-history ETF observation pool without refreshing cache")
    subparsers.add_parser("build-manual-review-list", help="build manual review list without refreshing cache or clearing blocks")
    subparsers.add_parser("summarize-data-governance", help="summarize data governance status without refreshing cache")
    subparsers.add_parser("summarize-qa-status", help="summarize QA failure actionability without refreshing cache")
    subparsers.add_parser("build-candidate-unblock-plan", help="build candidate unblock plan without changing candidate eligibility")
    subparsers.add_parser("check-factor-008b-readiness", help="check ETF-GAP-008B readiness without generating candidates")
    subparsers.add_parser("check-index-007b-readiness", help="check ETF-GAP-007B index readiness without entering 007B")
    subparsers.add_parser("validate-etf-007b-metrics", help="build small-scope ETF-GAP-007B metric validation report without refreshing cache")
    subparsers.add_parser("plan-cache-refresh", help="生成 legacy cache 安全刷新 dry-run 计划")
    pilot_parser = subparsers.add_parser("pilot-refresh", help="小范围 pilot refresh，默认只允许 core_11 或显式 symbols")
    pilot_parser.add_argument("--pool", choices=["core_11"], default=None)
    pilot_parser.add_argument("--symbols", default=None, help="Comma-separated ETF symbols, max 11")
    pilot_parser.add_argument("--max-count", type=int, default=11)
    pilot_parser.add_argument("--dry-run", action="store_true")
    pilot_parser.add_argument("--include-manual-review", action="store_true")
    repair_missing_parser = subparsers.add_parser("repair-missing-cache", help="targeted repair for P0_missing_cache ETF caches")
    repair_missing_parser.add_argument("--symbols", default=None, help="Comma-separated ETF symbols, max 10")
    repair_missing_parser.add_argument("--max-count", type=int, default=10)
    repair_missing_parser.add_argument("--dry-run", action="store_true")
    source_eval_parser = subparsers.add_parser("eval-source-preference", help="evaluate Sina vs EM qfq/none sources without writing formal cache")
    source_eval_parser.add_argument("--pool", choices=["core_11"], default=None)
    source_eval_parser.add_argument("--symbols", default=None, help="Comma-separated ETF symbols, max 20")
    source_eval_parser.add_argument("--max-count", type=int, default=20)
    diagnose_source_parser = subparsers.add_parser("diagnose-source", help="diagnose Sina and EastMoney source connectivity without writing formal cache")
    diagnose_source_parser.add_argument("--symbols", default=None, help="Comma-separated ETF symbols, default max 5")
    diagnose_source_parser.add_argument("--max-count", type=int, default=5)
    diagnose_source_parser.add_argument("--timeout", type=float, default=8.0)
    diagnose_source_parser.add_argument("--retries", type=int, default=1)
    subparsers.add_parser("diagnose-source-lag", help="diagnose single-symbol source lag blockers without refreshing cache")
    metadata_parser = subparsers.add_parser("update-etf-metadata", help="build ETF metadata reports without changing price cache")
    metadata_parser.add_argument("--source", choices=["akshare"], default="akshare")
    metadata_parser.add_argument("--max-count", type=int, default=None)
    metadata_parser.add_argument("--dry-run", action="store_true")
    index_parser = subparsers.add_parser("update-index-data", help="build ETF index benchmark map and index history coverage without changing ETF cache")
    index_parser.add_argument("--max-count", type=int, default=50)
    index_parser.add_argument("--symbols", default=None, help="Comma-separated ETF symbols")
    index_parser.add_argument("--dry-run", action="store_true")
    metrics_parser = subparsers.add_parser("compute-etf-metrics", help="build guarded ETF metric reports without refreshing ETF or index cache")
    metrics_parser.add_argument("--max-count", type=int, default=50)
    metrics_parser.add_argument("--symbols", default=None, help="Comma-separated ETF symbols")
    metrics_parser.add_argument("--min-overlap-days", type=int, default=60)
    metrics_parser.add_argument("--dry-run", action="store_true")
    factor_parser = subparsers.add_parser("compute-factor-score", help="build configurable factor score reports without changing strategy outputs")
    factor_parser.add_argument("--config", default="config/factor_score.yaml")
    factor_parser.add_argument("--max-count", type=int, default=50)
    factor_parser.add_argument("--symbols", default=None, help="Comma-separated ETF symbols")
    factor_parser.add_argument("--dry-run", action="store_true")
    index_diag_parser = subparsers.add_parser("diagnose-index-source", help="diagnose index history source candidates without writing index cache")
    index_diag_parser.add_argument("--index-codes", default=None, help="Comma-separated index codes, max 10")
    index_diag_parser.add_argument("--max-count", type=int, default=10)
    compare_parser = subparsers.add_parser("compare-signal", help="生成策略对照信号")
    compare_parser.add_argument("--signal-date", default=None, help="Manual requested signal date, YYYY-MM-DD")
    compare_parser.add_argument("--cash", type=float, default=None, help="Observation cash amount for signal sizing")
    compare_parser.add_argument("--strategy", choices=sorted(STRATEGY_CONFIGS), default=None, help="Only generate one strategy")
    generate_parser = subparsers.add_parser("generate-signal", help="生成基于过滤后完整 ETF 池的信号排名")
    generate_parser.add_argument("--signal-date", default=None, help="Manual requested signal date, YYYY-MM-DD")
    generate_parser.add_argument("--cash", type=float, default=None, help="Observation cash amount for signal sizing")
    generate_parser.add_argument("--strategy", choices=sorted(STRATEGY_CONFIGS), default=None, help="Only generate one strategy")
    generate_parser.add_argument("--use-cache", action="store_true", help="Use recent data windows and cached indicators")
    subparsers.add_parser("observation-report", help="生成实盘观察报告")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "update-data":
        command_update_data(mode=args.mode, symbols=args.symbols, max_workers=args.max_workers, refresh=args.refresh, config_path=args.config)
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
    elif args.command == "diagnose-data-quality":
        command_diagnose_data_quality()
    elif args.command == "build-candidate-gate":
        command_build_candidate_gate()
    elif args.command == "build-observation-pool":
        command_build_observation_pool()
    elif args.command == "build-manual-review-list":
        command_build_manual_review_list()
    elif args.command == "summarize-data-governance":
        command_summarize_data_governance()
    elif args.command == "summarize-qa-status":
        command_summarize_qa_status()
    elif args.command == "build-candidate-unblock-plan":
        command_build_candidate_unblock_plan()
    elif args.command == "check-factor-008b-readiness":
        command_check_factor_008b_readiness()
    elif args.command == "check-index-007b-readiness":
        command_check_index_007b_readiness()
    elif args.command == "validate-etf-007b-metrics":
        command_validate_etf_007b_metrics()
    elif args.command == "plan-cache-refresh":
        command_plan_cache_refresh()
    elif args.command == "pilot-refresh":
        command_pilot_refresh(
            pool=args.pool,
            symbols=args.symbols,
            max_count=args.max_count,
            dry_run=args.dry_run,
            include_manual_review=args.include_manual_review,
        )
    elif args.command == "repair-missing-cache":
        command_repair_missing_cache(symbols=args.symbols, max_count=args.max_count, dry_run=args.dry_run)
    elif args.command == "eval-source-preference":
        command_eval_source_preference(pool=args.pool, symbols=args.symbols, max_count=args.max_count)
    elif args.command == "diagnose-source":
        command_diagnose_source(symbols=args.symbols, max_count=args.max_count, timeout=args.timeout, retries=args.retries)
    elif args.command == "diagnose-source-lag":
        command_diagnose_source_lag()
    elif args.command == "update-etf-metadata":
        command_update_etf_metadata(source=args.source, max_count=args.max_count, dry_run=args.dry_run)
    elif args.command == "update-index-data":
        command_update_index_data(max_count=args.max_count, symbols=args.symbols, dry_run=args.dry_run)
    elif args.command == "compute-etf-metrics":
        command_compute_etf_metrics(
            max_count=args.max_count,
            symbols=args.symbols,
            min_overlap_days=args.min_overlap_days,
            dry_run=args.dry_run,
        )
    elif args.command == "compute-factor-score":
        command_compute_factor_score(
            config_path=args.config,
            max_count=args.max_count,
            symbols=args.symbols,
            dry_run=args.dry_run,
        )
    elif args.command == "diagnose-index-source":
        command_diagnose_index_source(index_codes=args.index_codes, max_count=args.max_count)
    elif args.command == "compare-signal":
        command_compare_signal(signal_date=args.signal_date, cash=args.cash, strategy=args.strategy)
    elif args.command == "generate-signal":
        command_compare_signal(signal_date=args.signal_date, cash=args.cash, strategy=args.strategy, use_cache=args.use_cache)
    elif args.command == "observation-report":
        command_observation_report()


if __name__ == "__main__":
    main()
