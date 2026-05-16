from __future__ import annotations

import os
import json
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

from data.universe import UNIVERSE_META_PATH, build_universe_stage_counts
from signal.weekly_signal import EMPTY_POSITION_REASON, NO_POSITION_INPUT_REASON, ensure_current_position
from signal.trade_policy import normalize_error_message
from ui.components import localize_columns, show_dataframe_or_empty, status_badge
from ui.signal_parser import (
    STRATEGY_ORDER,
    DashboardData,
    buy_symbols,
    current_symbols,
    format_symbol_list,
    hold_symbols,
    load_dashboard_data,
    parse_buy_table,
    parse_intraday_execution_table,
    parse_hold_table,
    parse_rank_table,
    parse_sell_table,
    parse_skip_table,
    parse_target_table,
    portfolio_changed,
    rebalance_rule_label,
    sell_symbols,
    status_label,
    strategy_label,
    strategy_row,
    target_symbols,
)


PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = Path(sys.executable)
OUTPUT_DIR = PROJECT_ROOT / "output"
CURRENT_POSITION = PROJECT_ROOT / "config" / "current_position.yaml"
README = PROJECT_ROOT / "README.md"

CommandArgs = str | list[str]

TECHNICAL_ERROR_HINTS = (
    "NotFoundError",
    "removeChild",
    "JavaScript",
    "static/js",
    "Traceback",
)

PENDING_EXECUTE_DATE_MARKERS = ("下一交易日", "待数据确认", "待下一交易日确认")


def _card_value(value: Any) -> str:
    if value in ("", None):
        return "N/A"
    try:
        if pd.isna(value):
            return "N/A"
    except TypeError:
        pass
    return str(value)


def _compact_quality_status(overview: dict[str, Any]) -> str:
    return _card_value(overview.get("trade_usage_level") or overview.get("risk_status"))


def _quality_report(overview: dict[str, Any]) -> dict[str, Any]:
    report = overview.get("quality_report")
    return report if isinstance(report, dict) else {}


def _display_list(items: Any) -> list[str]:
    if not items:
        return ["无"]
    if isinstance(items, list):
        return [str(item) for item in items if str(item).strip()] or ["无"]
    return [str(items)]


def _business_error_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame()
    rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        raw_error = str(row.get("failure_reason") or row.get("filter_reason") or row.get("errors") or "")
        normalized = normalize_error_message(raw_error)
        symbol = str(row.get("symbol", "")).zfill(6)
        name = row.get("name", "")
        data_status = normalized["错误类型"]
        if str(row.get("success", "")).lower() in {"true", "1", "yes", "是"} and normalized["错误类型"] == "未知错误":
            data_status = "历史行情缺失"
        rows.append(
            {
                "ETF代码": symbol,
                "ETF名称": name,
                "数据状态": data_status,
                "前端说明": normalized["前端说明"],
                "处理动作": normalized["处理动作"],
            }
        )
        details.append(
            {
                "ETF代码": symbol,
                "ETF名称": name,
                "技术详情": normalized["技术详情"],
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(details)


def render_compact_metric_grid(items: list[tuple[str, Any]], class_name: str = "compact-metric-grid") -> None:
    cards = []
    for label, value in items:
        cards.append(
            "<div class=\"compact-metric-card\">"
            f"<div class=\"compact-metric-label\">{escape(str(label))}</div>"
            f"<div class=\"compact-metric-value\">{escape(_card_value(value))}</div>"
            "</div>"
        )
    st.markdown(f"<div class=\"{class_name}\">{''.join(cards)}</div>", unsafe_allow_html=True)


def _command_parts(command: CommandArgs) -> list[str]:
    return [command] if isinstance(command, str) else command


def run_project_command(command: CommandArgs) -> dict[str, object]:
    args = [str(PYTHON_EXE), "main.py", *_command_parts(command)]
    result = subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": " ".join(args),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run_commands(commands: list[CommandArgs]) -> list[dict[str, object]]:
    logs = []
    for command in commands:
        item = run_project_command(command)
        logs.append(item)
        if int(item["returncode"]) != 0:
            break
    return logs


def append_logs(logs: list[dict[str, object]]) -> None:
    st.session_state.setdefault("command_logs", [])
    st.session_state["command_logs"].extend(logs)


def append_run_event(stage: str, message: str = "") -> None:
    st.session_state.setdefault("run_logs", [])
    st.session_state["run_logs"].append(
        {
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "阶段": stage,
            "说明": message,
        }
    )


def _fmt_duration(seconds: float | int | None) -> str:
    total = int(seconds or 0)
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


def _universe_meta_text() -> str:
    if not UNIVERSE_META_PATH.exists():
        return "ETF 池缓存未生成"
    try:
        meta = json.loads(UNIVERSE_META_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"ETF 池缓存读取失败：{exc}"
    updated_at = meta.get("updated_at", "未知")
    count = meta.get("count", "未知")
    return f"ETF 池最近更新时间：{updated_at}，数量：{count}"


def _progress_markdown(state: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"当前阶段：{state.get('stage', '准备中')}",
            f"进度：{state.get('current', 0)} / {state.get('total', 0)}",
            f"当前 ETF：{state.get('symbol', '')} {state.get('name', '')}".strip(),
            f"本地最新日期：{state.get('local_latest_date', 'N/A') or 'N/A'}",
            f"目标更新日期：{state.get('target_date', 'N/A') or 'N/A'}",
            f"成功：{state.get('success_count', 0)}",
            f"跳过：{state.get('skipped_count', 0)}",
            f"失败：{state.get('failed_count', 0)}",
            f"提示：{state.get('status', '')}" if state.get("status") else "",
            f"已耗时：{_fmt_duration(state.get('elapsed_seconds', 0))}",
            f"预计剩余：{_fmt_duration(state.get('eta_seconds', 0))}",
        ]
    )


def run_update_and_generate_with_progress(signal_date: str | None, observation_cash: float, mode: str = "refresh") -> dict[str, Any]:
    from main import command_compare_signal, command_update_data

    state: dict[str, Any] = {"stage": "初始化", "current": 0, "total": 8}
    started = time.perf_counter()
    progress = st.progress(0)
    detail = st.empty()
    timing_box = st.empty()
    last_stage = {"value": ""}

    def render(payload: dict[str, Any]) -> None:
        state.update(payload)
        state["elapsed_seconds"] = time.perf_counter() - started
        total = int(state.get("total") or 0)
        current = int(state.get("current") or 0)
        progress.progress(0 if total <= 0 else min(current / total, 1.0))
        detail.code(_progress_markdown(state), language="text")
        stage = str(state.get("stage", ""))
        if stage and stage != last_stage["value"]:
            last_stage["value"] = stage
            append_run_event(stage, _progress_markdown(state).replace("\n", " | "))

    with st.status("刷新行情并生成最新信号", expanded=True) as status:
        render({"stage": "初始化", "current": 1, "total": 8})
        render({"stage": "读取 ETF 池", "current": 2, "total": 8})
        render({"stage": "检查本地缓存", "current": 3, "total": 8})
        update_metrics = command_update_data(mode=mode, max_workers=6, progress_callback=render)
        try:
            _load_local_market_dates.clear()
        except Exception:
            pass

        signal_file = OUTPUT_DIR / "compare_signal.csv"
        strategy_file = OUTPUT_DIR / "strategy_compare_signal.csv"
        data_changed = int(update_metrics.get("success_count", 0) or 0) > 0
        cash_matches = False
        if strategy_file.exists():
            try:
                previous = pd.read_csv(strategy_file)
                cash_values = pd.to_numeric(previous["observation_cash"], errors="coerce").dropna() if "observation_cash" in previous.columns else pd.Series(dtype=float)
                cash_matches = bool(not cash_values.empty and abs(float(cash_values.iloc[0]) - float(observation_cash)) < 0.01)
            except Exception:
                cash_matches = False
        can_reuse_signal = not data_changed and cash_matches and signal_file.exists() and (OUTPUT_DIR / "compare_signal.txt").exists()

        if can_reuse_signal:
            render({"stage": "运行策略", "current": 6, "total": 8, "eta_seconds": 0, "status": "本地数据已是最新，复用已有信号"})
            append_run_event("复用已有信号", "本地数据已是最新，观察资金未变化，直接复用 compare_signal 结果。")
            result = pd.read_csv(strategy_file) if strategy_file.exists() else pd.DataFrame()
            signal_seconds = 0.0
        else:
            render({"stage": "运行策略", "current": 6, "total": 8, "eta_seconds": 0})
            signal_started = time.perf_counter()
            result = command_compare_signal(signal_date=signal_date, cash=observation_cash, use_cache=True)
            signal_seconds = time.perf_counter() - signal_started

        render({"stage": "生成 compare_signal.csv / compare_signal.txt", "current": 7, "total": 8, "eta_seconds": 0})
        signal_mtime = datetime.fromtimestamp(signal_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S") if signal_file.exists() else "未生成"
        total_seconds = time.perf_counter() - started
        update_metrics = dict(update_metrics)
        update_metrics["signal_seconds"] = round(signal_seconds, 3)
        update_metrics["total_seconds"] = round(total_seconds, 3)
        update_metrics["signal_file_updated_at"] = signal_mtime
        timing_log = PROJECT_ROOT / "logs" / "update_timing.log"
        timing_log.parent.mkdir(parents=True, exist_ok=True)
        with timing_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": datetime.now().isoformat(timespec="seconds"), **update_metrics}, ensure_ascii=False, sort_keys=True) + "\n")
        timing_box.json(update_metrics)
        render({"stage": "刷新页面结果", "current": 8, "total": 8, "eta_seconds": 0})
        status.update(label="刷新完成", state="complete", expanded=True)

    st.success(
        f"更新完成｜总耗时 {_fmt_duration(total_seconds)}｜ETF 总数 {update_metrics.get('processed_count', 0)}｜"
        f"成功更新 {update_metrics.get('success_count', 0)}｜跳过 {update_metrics.get('skipped_count', 0)}｜"
        f"失败 {update_metrics.get('failed_count', 0)}｜最新数据日期 {update_metrics.get('latest_data_date', 'N/A')}｜"
        f"信号文件更新时间 {signal_mtime}"
    )
    append_logs(
        [
            {
                "command": "in-process update-data --refresh + generate-signal --use-cache",
                "returncode": 0,
                "stdout": f"rows={len(result)} metrics={update_metrics}",
                "stderr": "",
            }
        ]
    )
    return update_metrics


def open_local_path(path: Path) -> None:
    if not path.exists():
        st.error("路径不存在。请在高级诊断信息中检查配置。")
        return
    os.startfile(str(path))  # type: ignore[attr-defined]


def _parse_date(value: object) -> date | None:
    if value in ("", None, "N/A"):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _date_text(value: object) -> str:
    if value in ("", None, "N/A"):
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return str(value).strip()


def _is_pending_execute_date(value: object) -> bool:
    text = _date_text(value)
    return any(marker in text for marker in PENDING_EXECUTE_DATE_MARKERS)


@st.cache_data(ttl=600, show_spinner=False)
def _load_local_market_dates(project_root: Path) -> set[date]:
    dates: set[date] = set()
    cache_dir = project_root / "data" / "cache"
    if not cache_dir.exists():
        return dates
    for path in cache_dir.glob("*.csv"):
        try:
            frame = pd.read_csv(path, usecols=["date"], encoding="utf-8-sig")
        except Exception:
            continue
        parsed = pd.to_datetime(frame["date"], errors="coerce").dropna()
        dates.update(item.date() for item in parsed)
    return dates


def _format_cn_date(value: object) -> str:
    parsed = _parse_date(value)
    return parsed.strftime("%Y/%m/%d") if parsed else _card_value(value)


def _next_weekday(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def expected_execution_info(overview: dict[str, Any], project_root: Path) -> dict[str, str]:
    signal_day = _parse_date(overview.get("effective_signal_date"))
    latest_day = _parse_date(overview.get("latest_data_date"))
    if not signal_day:
        return {"date": "待确认", "reason": "信号日缺失，无法推断下一个交易日", "source": "missing"}

    market_dates = sorted(_load_local_market_dates(project_root))
    later_dates = [item for item in market_dates if item > signal_day]
    if later_dates:
        return {"date": later_dates[0].strftime("%Y/%m/%d"), "reason": "", "source": "local_market_dates"}

    if latest_day and signal_day == latest_day:
        inferred = _next_weekday(signal_day)
        return {
            "date": inferred.strftime("%Y/%m/%d"),
            "reason": "",
            "source": "weekday_inferred",
        }

    return {
        "date": "待确认",
        "reason": "交易日历缺失，或当前信号日不是最新完整交易日",
        "source": "unresolved",
    }


def _selected_strategy_row(data: DashboardData, selected_strategy: str) -> pd.Series:
    return strategy_row(data.signals, selected_strategy)


def _recommendation_summary(row: pd.Series, etf_names: dict[str, str]) -> tuple[str, str, str]:
    if row.empty:
        return "N/A", "N/A", "N/A"
    targets = target_symbols(row)
    buys = buy_symbols(row)
    sells = sell_symbols(row)
    holds = hold_symbols(row)
    buy_table = parse_buy_table(row)
    if not buy_table.empty and "交易动作" in buy_table.columns:
        actions = [str(item) for item in buy_table["交易动作"].dropna().unique()]
        if any(item == "降低金额买入" for item in actions):
            action = "降低仓位买入"
        elif any(item == "买入" for item in actions):
            action = "今日可买入"
        else:
            action = actions[0]
    elif buys:
        action = "今日可买入"
    elif sells:
        action = "今日不买入，按风控卖出"
    elif holds:
        action = "今日不买入，继续持有"
    elif targets:
        action = "暂不买入，只观察"
    else:
        action = "今日不买入"
    target_weight = f"{100 / len(targets):.1f}%" if targets else "0%"
    return format_symbol_list(targets, etf_names), action, target_weight


def validate_signal_dates(overview: dict[str, Any], selected_date: date, project_root: Path) -> list[str]:
    errors: list[str] = []
    selected_text = selected_date.isoformat()
    requested_text = _date_text(overview.get("requested_signal_date"))
    effective_text = _date_text(overview.get("effective_signal_date"))
    execute_text = _date_text(overview.get("execute_date"))
    source = _date_text(overview.get("signal_date_source")) or "auto"
    last_mode = st.session_state.get("last_signal_generation_mode")

    if last_mode == "manual" and source != "manual":
        errors.append("本次没有按所选日期生成信号，请重新生成。")
    if source == "manual" and requested_text != selected_text:
        errors.append("生成结果日期与所选日期不一致。")
    if last_mode == "manual" and requested_text != selected_text:
        errors.append("生成结果日期与所选日期不一致。")

    requested_date = _parse_date(requested_text)
    effective_date = _parse_date(effective_text)
    execute_date = _parse_date(execute_text)
    latest_data_date = _parse_date(overview.get("latest_data_date"))

    if requested_date and effective_date and effective_date > requested_date:
        errors.append("实际计算信号日不能晚于用户选择的信号日。")

    if requested_date and effective_date and effective_date < requested_date:
        market_dates = _load_local_market_dates(project_root)
        trading_dates_between = [item for item in market_dates if effective_date < item <= requested_date]
        if trading_dates_between:
            errors.append("实际计算信号日过早，期间存在本地交易日数据，不能回退到更早月份。")
        elif not market_dates and (requested_date - effective_date).days > 10:
            errors.append("实际计算信号日距离用户选择日期过远，请检查本地交易日数据。")

    if effective_date and execute_date and execute_date <= effective_date:
        errors.append("预计执行日必须晚于实际计算信号日。")
    if effective_date and not execute_date and not _is_pending_execute_date(execute_text):
        if latest_data_date and effective_date == latest_data_date:
            errors.append("本地尚无下一交易日行情，预计执行日应显示为待数据确认。")
        else:
            errors.append("预计执行日缺失或格式异常。")

    return list(dict.fromkeys(errors))


def default_signal_date(overview: dict[str, Any]) -> date:
    return (
        _parse_date(overview.get("requested_signal_date"))
        or _parse_date(overview.get("latest_data_date"))
        or _parse_date(overview.get("effective_signal_date"))
        or date.today()
    )


def default_observation_cash() -> float:
    try:
        return float(ensure_current_position(CURRENT_POSITION).get("cash", 0))
    except (TypeError, ValueError, OSError, yaml.YAMLError):
        return 0.0


def _position_rows_from_file(etf_names: dict[str, str]) -> list[dict[str, Any]]:
    current_position = ensure_current_position(CURRENT_POSITION)
    rows = []
    next_id = 1
    for item in current_position.get("holdings", []):
        symbol = str(item.get("symbol", "")).zfill(6)
        rows.append(
            {
                "id": next_id,
                "symbol": symbol,
                "name": etf_names.get(symbol, symbol),
                "shares": float(item.get("shares", 0) or 0),
            }
        )
        next_id += 1
    return rows or [{"id": next_id, "symbol": "", "name": "", "shares": 0.0}]


def _next_position_row_id() -> int:
    current = int(st.session_state.get("position_next_row_id", 1))
    st.session_state["position_next_row_id"] = current + 1
    return current


def _init_position_editor(etf_names: dict[str, str]) -> None:
    if "position_rows" in st.session_state:
        return
    rows = _position_rows_from_file(etf_names)
    max_id = max(int(row["id"]) for row in rows) if rows else 0
    st.session_state["position_rows"] = rows
    st.session_state["position_next_row_id"] = max_id + 1


def _normalize_symbol(value: Any) -> str:
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    raw = str(value or "").strip()
    return raw.zfill(6) if raw.isdigit() else raw


def _save_current_position(cash: float, current_empty: bool, holdings: list[dict[str, Any]]) -> None:
    if current_empty:
        payload: dict[str, Any] = {"cash": float(cash), "current_empty": True, "holdings": []}
    else:
        payload = {"cash": float(cash), "holdings": holdings}
    CURRENT_POSITION.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_POSITION.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _validate_holdings(rows: list[dict[str, Any]], etf_names: dict[str, str]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    holdings: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol"))
        shares = float(row.get("shares", 0) or 0)
        if not symbol and shares <= 0:
            continue
        if not symbol.isdigit() or len(symbol) != 6:
            errors.append(f"ETF 代码必须是 6 位数字：{symbol or '空'}")
            continue
        if symbol not in etf_names:
            errors.append(f"{symbol} 不在当前 ETF 池中。")
            continue
        if shares <= 0:
            errors.append(f"{symbol} 持有份额必须大于 0。")
            continue
        if shares % 100 != 0:
            warnings.append(f"{symbol} 持有份额建议为 100 的整数倍。")
        if symbol in seen:
            errors.append(f"{symbol} 重复填写，请合并为一行。")
            continue
        seen.add(symbol)
        holdings.append({"symbol": symbol, "shares": float(shares)})
    return holdings, errors, warnings


def render_sidebar(data: DashboardData, observation_cash: float) -> tuple[date, str, float, bool]:
    command_ran = False
    st.sidebar.header("操作")
    st.sidebar.markdown("**资金设置**")
    observation_cash = float(
        st.sidebar.number_input(
            "本次观察资金",
            min_value=1000.0,
            value=max(float(observation_cash or 0), 1000.0),
            step=100.0,
            key="observation_cash_input",
        )
    )

    st.sidebar.markdown("**策略选择**")
    available = set(data.signals["strategy_name"].astype(str)) if not data.signals.empty and "strategy_name" in data.signals.columns else set()
    preferred = ["momentum_rotation_monthly", "reduced_equal_weight_monthly", "equal_weight_monthly"]
    options = [name for name in preferred if not available or name in available] or preferred
    selected_strategy = st.sidebar.selectbox(
        "策略选择",
        options,
        index=0,
        format_func=strategy_label,
        help="稳健基准保留固定篮子等权配置；动态轮动会按信号日重新计算动量排名。",
        key="strategy_selector",
    )

    st.sidebar.divider()
    st.sidebar.markdown("**日常使用**")
    st.sidebar.caption("联网更新行情，耗时较长，会显示进度。")
    if st.sidebar.button("刷新行情并生成最新信号", width="stretch", type="primary", key="btn_update_and_generate"):
        st.session_state["last_signal_generation_mode"] = "auto"
        st.session_state.pop("last_requested_signal_date", None)
        run_update_and_generate_with_progress(None, observation_cash, mode="refresh")
        command_ran = True

    st.sidebar.markdown("**快速查看**")
    st.sidebar.caption("只使用本地已有数据，不联网，速度快。")
    if st.sidebar.button("快速生成最新信号", width="stretch", key="btn_generate_latest"):
        with st.spinner("正在使用本地数据生成信号..."):
            st.session_state["last_signal_generation_mode"] = "auto"
            st.session_state.pop("last_requested_signal_date", None)
            append_logs(run_commands([["generate-signal", "--cash", f"{observation_cash:.2f}", "--use-cache"]]))
            append_run_event("快速生成最新信号", "只使用本地已有行情和缓存，不联网。")
        command_ran = True

    st.sidebar.markdown("**历史复盘**")
    selected_date = st.sidebar.date_input(
        "信号日选择器",
        value=default_signal_date(data.overview),
        help="如果选择日期不是交易日，系统会使用此前最近交易日计算信号，不会向未来滚动使用未来行情。",
        key="signal_date_input",
    )
    signal_date = selected_date.isoformat()
    st.sidebar.caption("用于历史复盘，不代表今日可交易。")
    if st.sidebar.button("回看某日信号", width="stretch", key="btn_generate_selected"):
        with st.spinner("正在生成历史信号..."):
            st.session_state["last_signal_generation_mode"] = "manual"
            st.session_state["last_requested_signal_date"] = signal_date
            append_logs(run_commands([["generate-signal", "--signal-date", signal_date, "--cash", f"{observation_cash:.2f}", "--use-cache"]]))
            append_run_event("回看某日信号", f"使用历史信号日 {signal_date}，不自动更新行情。")
        command_ran = True

    st.sidebar.markdown("**维护**")
    st.sidebar.caption(_universe_meta_text())
    st.sidebar.caption("只检查数据、ETF池和策略状态，不生成交易建议。")
    if st.sidebar.button("体检数据和策略", width="stretch", key="btn_qa_check"):
        with st.spinner("正在运行质量检查..."):
            append_logs(run_commands(["qa-check"]))
            append_run_event("体检数据和策略", "已运行数据、ETF池和策略状态检查。")
        command_ran = True

    st.sidebar.caption("本工具只生成观察信号，不自动下单，不连接券商。")
    return selected_date, selected_strategy, observation_cash, command_ran


def render_overview(overview: dict[str, Any], selected_date: date, observation_cash: float, data: DashboardData) -> None:
    st.subheader("信号总览")
    quality_report = _quality_report(overview)
    execution = expected_execution_info(overview, PROJECT_ROOT)
    render_compact_metric_grid(
        [
            ("你选择的信号日", _format_cn_date(selected_date)),
            ("实际计算信号日", _format_cn_date(overview["effective_signal_date"])),
            ("预计执行日", execution["date"]),
            ("当前状态", overview.get("execution_status", "N/A")),
            ("本次观察资金", f"{observation_cash:.2f} 元"),
            ("日期来源", overview["signal_date_source_label"]),
            ("最新本地数据日期", _format_cn_date(overview["latest_data_date"])),
            ("数据质量状态", overview.get("data_quality_status", "N/A")),
            ("交易使用等级", _compact_quality_status(overview)),
            ("质量评分", overview.get("quality_score", "N/A")),
            ("信号文件更新时间", data.output_mtimes.get("compare_signal.csv", "未生成")),
        ]
    )

    if execution["date"] == "待确认":
        st.warning(f"预计执行日：待确认。原因：{execution['reason']}")
    st.info("执行说明：下一个交易日按开盘后流动性情况执行。建议执行时间：09:35 - 10:00。价格规则：人工限价单，参考实时盘口，不自动下单。")
    with st.expander("质量状态详情", expanded=False):
        detail_rows = [
            ("数据质量状态", quality_report.get("data_quality_status", "N/A")),
            ("交易使用等级", quality_report.get("trade_usage_level", "N/A")),
            ("当前执行状态", quality_report.get("execution_status", overview.get("execution_status", "N/A"))),
            ("信号日", quality_report.get("signal_date", overview.get("effective_signal_date", "N/A"))),
            ("执行日", quality_report.get("execute_date", overview.get("execute_date", "N/A"))),
            ("最新本地数据日期", quality_report.get("latest_data_date", overview.get("latest_data_date", "N/A"))),
            ("原始 ETF 数量", quality_report.get("raw_etf_count", "N/A")),
            ("A股 ETF 数量", quality_report.get("a_share_etf_count", "N/A")),
            ("过滤后 ETF 数量", quality_report.get("filtered_etf_count", "N/A")),
            ("进入排名 ETF 数量", quality_report.get("ranked_etf_count", "N/A")),
            ("下载失败数量", quality_report.get("download_failed_count", "N/A")),
            ("QA 是否通过", "是" if quality_report.get("qa_passed") else "否"),
            ("质量提示数量", quality_report.get("qa_warning_count", "N/A")),
            ("质量评分", quality_report.get("score", "N/A")),
            ("下一等级", quality_report.get("next_level", "N/A")),
        ]
        st.dataframe(localize_columns(pd.DataFrame(detail_rows, columns=["项目", "内容"])), hide_index=True, width="stretch", height=400)

        st.markdown("**限制原因**")
        for reason in _display_list(quality_report.get("blocking_reasons")):
            st.write(f"- {reason}")
        st.markdown("**数据提示列表**")
        for reason in _display_list(quality_report.get("warning_reasons")):
            st.write(f"- {reason}")
        st.markdown("**质量提示明细**")
        for warning in _display_list(quality_report.get("qa_warnings")):
            st.write(f"- {warning}")
        st.markdown("**如何提升到下一等级**")
        for requirement in _display_list(quality_report.get("next_level_requirements")):
            st.write(f"- {requirement}")

    if overview.get("data_stale_after_close"):
        st.warning("今日已收盘，但本地数据可能尚未更新到今日完整日线。这个提示只影响数据新鲜度判断，不代表执行窗口状态。")
        with st.expander("查看说明", expanded=False):
            st.write("可以稍后更新数据后重新生成信号。")


def render_top_summary(data: DashboardData, selected_strategy: str, observation_cash: float) -> None:
    row = _selected_strategy_row(data, selected_strategy)
    recommended_etf, action, target_weight = _recommendation_summary(row, data.etf_names)
    execution = expected_execution_info(data.overview, PROJECT_ROOT)
    items = [
        ("当前信号日", _format_cn_date(data.overview.get("effective_signal_date"))),
        ("最新数据日期", _format_cn_date(data.overview.get("latest_data_date"))),
        ("预计执行日", execution["date"]),
        ("本次观察资金", f"{observation_cash:.2f} 元"),
        ("当前策略", strategy_label(selected_strategy)),
        ("数据质量状态", data.overview.get("data_quality_status", "N/A")),
        ("最近一次更新时间", data.output_mtimes.get("compare_signal.csv", "未生成")),
        ("当前推荐 ETF", recommended_etf),
        ("操作方向", action),
        ("目标仓位", target_weight),
    ]
    render_compact_metric_grid(items, class_name="compact-metric-grid summary-metric-grid")
    if execution["date"] == "待确认":
        st.warning(f"预计执行日：待确认。原因：{execution['reason']}")


def _truthy_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "yes", "是"])


def build_etf_pool_view(data: DashboardData) -> pd.DataFrame:
    base = data.coverage.copy()
    if base.empty:
        base = data.rankings.copy()
    if base.empty:
        return pd.DataFrame()

    if "symbol" in base.columns:
        base["symbol"] = base["symbol"].astype(str).str.zfill(6)
    ranked_symbols = set()
    selected_symbols = set()
    if not data.rankings.empty and "symbol" in data.rankings.columns:
        rankings = data.rankings.copy()
        rankings["symbol"] = rankings["symbol"].astype(str).str.zfill(6)
        ranked_symbols = set(rankings["symbol"])
        if "selected" in rankings.columns:
            selected_symbols = set(rankings.loc[_truthy_series(rankings["selected"]), "symbol"])

    rows = []
    for _, item in base.iterrows():
        symbol = str(item.get("symbol", "")).zfill(6)
        success = str(item.get("success", "True")).lower() not in {"false", "0", "no", "否"}
        included = symbol in ranked_symbols if ranked_symbols else success
        reason = str(item.get("filter_reason") or item.get("failure_reason") or "")
        if included and not reason:
            reason = "纳入策略观察池"
        elif not included and not reason:
            reason = "未进入当前策略筛选池"
        rows.append(
            {
                "symbol": symbol,
                "name": item.get("name", data.etf_names.get(symbol, "")),
                "status": item.get("status", "正常" if success else "异常"),
                "eligible": included,
                "selected": symbol in selected_symbols,
                "reason": reason,
                "latest_date": item.get("latest_date", item.get("end_date", "")),
            }
        )
    return pd.DataFrame(rows)


def render_universe_module(data: DashboardData) -> None:
    st.subheader("当前策略 ETF 池")
    coverage = data.coverage.copy()
    rankings = data.rankings.copy()
    raw = data.universe_raw.copy()
    snapshot = data.universe_snapshot.copy()
    counts = build_universe_stage_counts(raw if not raw.empty else snapshot, coverage, rankings) if (not raw.empty or not snapshot.empty) else {
        "raw_total": 0,
        "a_share_equity_total": 0,
        "listed_pass_count": 0,
        "amount_pass_count": 0,
        "completeness_pass_count": 0,
        "ranked_count": len(rankings),
    }
    success_count = int(coverage["success"].astype(str).str.lower().isin(["true", "1"]).sum()) if "success" in coverage.columns else 0
    failed = coverage[~coverage["success"].astype(str).str.lower().isin(["true", "1"])] if "success" in coverage.columns else pd.DataFrame()
    pool_view = build_etf_pool_view(data)
    included_count = int(pool_view["eligible"].sum()) if not pool_view.empty and "eligible" in pool_view.columns else counts["ranked_count"]
    excluded_count = max(len(pool_view) - included_count, 0) if not pool_view.empty else 0

    render_compact_metric_grid(
        [
            ("当前 ETF 总数", len(pool_view) or counts["raw_total"] or "N/A"),
            ("纳入策略的 ETF 数", included_count or "N/A"),
            ("被排除的 ETF 数", excluded_count),
            ("当前策略 ETF 池", counts["ranked_count"] or included_count or "N/A"),
            ("下载成功", success_count or "N/A"),
            ("下载失败", len(failed) if not coverage.empty else "N/A"),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )
    st.caption("当前策略 ETF 池：{} 只。这不是全市场 ETF 数量，而是当前策略筛选后的观察池。".format(counts["ranked_count"] or included_count or 0))

    if not pool_view.empty:
        display_cols = [col for col in ["symbol", "name", "status", "eligible", "selected", "reason", "latest_date"] if col in pool_view.columns]
        st.dataframe(localize_columns(pool_view[display_cols]), hide_index=True, width="stretch", height=500)

    if rankings.empty:
        st.caption("尚未生成过滤后排名，请先运行生成信号。")
    else:
        scope_options = ["全部", "宽基", "行业", "债券", "商品", "跨境", "风格", "货币"]
        scope = st.selectbox("ETF 池范围", scope_options, index=0, key="universe_scope")
        view = rankings.copy()
        if scope != "全部":
            keyword_map = {
                "宽基": ["broad_based", "A股宽基"],
                "行业": ["sector", "行业ETF"],
                "债券": ["bond", "债券ETF"],
                "商品": ["commodity", "商品ETF"],
                "跨境": ["cross_border", "跨境ETF", "overseas"],
                "风格": ["style", "风格ETF"],
                "货币": ["cash", "货币ETF"],
            }
            keys = keyword_map.get(scope, [])
            mask = pd.Series(False, index=view.index)
            pattern = "|".join(keys)
            for col in ["asset_class", "theme", "sector"]:
                if col in view.columns:
                    text = view[col].astype(str)
                    mask = mask | text.isin(keys) | text.str.contains(pattern, regex=True, na=False)
            view = view[mask]
        st.markdown("**过滤后可交易池排名 Top 20**")
        show_dataframe_or_empty(view.head(20), empty_text="当前范围暂无通过过滤的 ETF。", key="universe_top20", height=400)
        with st.expander("查看 Top 10", expanded=False):
            show_dataframe_or_empty(view.head(10), empty_text="当前范围暂无通过过滤的 ETF。", key="universe_top10", height=400)

    if not failed.empty:
        st.markdown("**失败下载列表**")
        business, details = _business_error_frame(failed)
        show_dataframe_or_empty(business, key="universe_failed_downloads", height=400)
        with st.expander("查看技术详情", expanded=False):
            show_dataframe_or_empty(details, key="universe_failed_downloads_debug", height=260)


def render_data_quality_tab(data: DashboardData) -> None:
    st.subheader("数据质量")
    coverage = data.coverage.copy()
    overview = data.overview
    latest_date = overview.get("latest_data_date", "N/A")
    signal_date = overview.get("effective_signal_date", "N/A")
    failed = pd.DataFrame()
    lagged = pd.DataFrame()
    missing = pd.DataFrame()
    if not coverage.empty:
        if "success" in coverage.columns:
            failed = coverage[~coverage["success"].astype(str).str.lower().isin(["true", "1", "yes", "是"])]
        if "latest_date" in coverage.columns:
            lagged = coverage[coverage["latest_date"].astype(str) < str(latest_date)]
        if "missing_count" in coverage.columns:
            missing = coverage[pd.to_numeric(coverage["missing_count"], errors="coerce").fillna(0) > 0]

    status = "正常"
    if not failed.empty or len(lagged) > max(3, len(coverage) * 0.05):
        status = "异常"
    elif not missing.empty or not lagged.empty:
        status = "警告"
    if overview.get("data_quality_status") in {"异常", "警告", "正常"}:
        status = str(overview.get("data_quality_status"))

    render_compact_metric_grid(
        [
            ("总体状态", status),
            ("最新本地数据日期", _format_cn_date(latest_date)),
            ("信号日", _format_cn_date(signal_date)),
            ("存在缺失数据", "是" if not missing.empty else "否"),
            ("存在下载失败", "是" if not failed.empty else "否"),
            ("存在 ETF 数据落后", "是" if not lagged.empty else "否"),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )

    quality_report = _quality_report(overview)
    reasons = _display_list(quality_report.get("blocking_reasons")) + _display_list(quality_report.get("warning_reasons"))
    with st.expander("质量判断说明", expanded=True):
        for reason in [item for item in reasons if item != "无"]:
            st.write(f"- {reason}")
        if all(item == "无" for item in reasons):
            st.write("当前未发现阻断性质量问题。")

    if not coverage.empty:
        cols = [col for col in ["symbol", "name", "latest_date", "local_latest_date", "target_update_date", "missing_count", "duplicate_count"] if col in coverage.columns]
        st.markdown("**ETF 数据状态**")
        st.dataframe(localize_columns(coverage[cols]), hide_index=True, width="stretch", height=500)

    abnormal = pd.concat([failed, lagged, missing], ignore_index=True).drop_duplicates(subset=["symbol"] if "symbol" in coverage.columns else None)
    st.markdown("**异常 ETF 列表**")
    if abnormal.empty:
        st.caption("暂无异常 ETF。")
    else:
        business, details = _business_error_frame(abnormal)
        st.dataframe(localize_columns(business), hide_index=True, width="stretch", height=400)
        with st.expander("查看技术详情", expanded=False):
            st.dataframe(localize_columns(details), hide_index=True, width="stretch", height=260)


def render_current_position_module(etf_names: dict[str, str]) -> float:
    st.subheader("当前持仓")
    current_position = ensure_current_position(CURRENT_POSITION)
    _init_position_editor(etf_names)

    if not current_position.get("position_file_exists") or not current_position.get("position_configured"):
        st.warning("未填写当前持仓，暂只能展示目标组合。")
    elif current_position.get("current_empty"):
        st.info(EMPTY_POSITION_REASON)

    cash_default = max(float(current_position.get("cash", 0) or 0), 0.0)
    cash_col, empty_col, add_col, save_col = st.columns([1.2, 0.8, 0.9, 0.9], vertical_alignment="bottom")
    with cash_col:
        cash = float(
            st.number_input(
                "可用现金",
                min_value=0.0,
                value=cash_default,
                step=100.0,
                key="position_cash_input",
            )
        )
    with empty_col:
        current_empty = st.checkbox(
            "当前空仓",
            value=bool(current_position.get("current_empty", False)),
            key="position_empty_checkbox",
        )

    with add_col:
        add_row = st.button("新增持仓", width="stretch", key="position_add_row", disabled=current_empty)
    with save_col:
        save_position = st.button("保存持仓", width="stretch", key="position_save")

    if add_row:
        row_id = _next_position_row_id()
        st.session_state["position_rows"].append({"id": row_id, "symbol": "", "name": "", "shares": 0.0})

    edited_rows: list[dict[str, Any]] = []
    if current_empty:
        st.caption("已选择当前空仓，保存后系统只会生成买入计划，不生成卖出计划。")
    else:
        editor_rows = []
        for row in st.session_state["position_rows"]:
            symbol = str(row.get("symbol", ""))
            normalized_symbol = _normalize_symbol(symbol)
            editor_rows.append(
                {
                    "row_id": int(row["id"]),
                    "ETF代码": normalized_symbol,
                    "ETF名称": etf_names.get(normalized_symbol, ""),
                    "持有份额": float(row.get("shares", 0) or 0),
                    "操作": False,
                }
            )
        edited_frame = st.data_editor(
            pd.DataFrame(editor_rows),
            hide_index=True,
            width="stretch",
            height=min(280, max(150, 42 * (len(editor_rows) + 1))),
            column_order=["ETF代码", "ETF名称", "持有份额", "操作"],
            disabled=["ETF名称"],
            key="position_editor",
            column_config={
                "row_id": None,
                "ETF代码": st.column_config.TextColumn("ETF代码", width="small", help="填写 6 位 ETF 代码"),
                "ETF名称": st.column_config.TextColumn("ETF名称", width="medium"),
                "持有份额": st.column_config.NumberColumn("持有份额", min_value=0.0, step=100.0, format="%.0f", width="small"),
                "操作": st.column_config.CheckboxColumn("操作", help="勾选后删除该行", width="small"),
            },
        )
        deleted_any = False
        for _, row in edited_frame.iterrows():
            if bool(row.get("操作", False)):
                deleted_any = True
                continue
            normalized_symbol = _normalize_symbol(row.get("ETF代码", ""))
            raw_id = row.get("row_id")
            row_id = _next_position_row_id() if pd.isna(raw_id) else int(raw_id)
            edited_rows.append(
                {
                    "id": row_id,
                    "symbol": normalized_symbol,
                    "name": etf_names.get(normalized_symbol, ""),
                    "shares": float(row.get("持有份额", 0) or 0),
                }
            )
        if deleted_any:
            st.info("已删除勾选的持仓行，保存后写入配置文件。")
        st.session_state["position_rows"] = edited_rows or [{"id": _next_position_row_id(), "symbol": "", "name": "", "shares": 0.0}]

    if save_position:
        if current_empty:
            _save_current_position(cash, True, [])
            st.success("当前持仓已保存为空仓。请重新生成信号以得到买入计划。")
            st.session_state["position_rows"] = [{"id": _next_position_row_id(), "symbol": "", "name": "", "shares": 0.0}]
            return cash

        holdings, errors, warnings = _validate_holdings(st.session_state["position_rows"], etf_names)
        for warning in warnings:
            st.warning(warning)
        if errors:
            for error in errors:
                st.error(error)
            return cash
        if not holdings:
            st.error(NO_POSITION_INPUT_REASON)
            return cash
        _save_current_position(cash, False, holdings)
        st.success("当前持仓已保存。请重新生成信号以得到完整买入、卖出和继续持有计划。")

    return cash


def _display_value(row: pd.Series, key: str, default: str = "N/A") -> str:
    value = row.get(key, default)
    if value in ("", None):
        return default
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    return str(value)


def render_manual_execution_plan(row: pd.Series, selected_date: date, observation_cash: float, etf_names: dict[str, str], key_prefix: str) -> None:
    execution = expected_execution_info(
        {
            "effective_signal_date": _display_value(row, "effective_signal_date", _display_value(row, "signal_date")),
            "latest_data_date": _display_value(row, "latest_data_date"),
        },
        PROJECT_ROOT,
    )
    plan = pd.DataFrame(
        [
            {"项目": "你选择的信号日", "内容": selected_date.isoformat()},
            {"项目": "实际计算信号日", "内容": _display_value(row, "effective_signal_date", _display_value(row, "signal_date"))},
            {"项目": "预计执行日", "内容": execution["date"]},
            {"项目": "建议执行时间", "内容": "09:35 - 10:00"},
            {"项目": "价格规则", "内容": "人工限价单，参考实时盘口，不自动下单"},
            {"项目": "本次观察资金", "内容": f"{observation_cash:.2f} 元"},
            {"项目": "当前持仓", "内容": format_symbol_list(current_symbols(row), etf_names)},
            {"项目": "目标组合", "内容": format_symbol_list(target_symbols(row), etf_names)},
        ]
    )
    show_dataframe_or_empty(plan, key=f"{key_prefix}_manual_execution_plan")


def render_change_summary(row: pd.Series, etf_names: dict[str, str], key_prefix: str) -> None:
    targets = target_symbols(row)
    current = current_symbols(row)
    buys = buy_symbols(row)
    sells = sell_symbols(row)
    holds = hold_symbols(row)
    changed = portfolio_changed(row)
    summary = pd.DataFrame(
        [
            {"项目": "目标 ETF", "内容": format_symbol_list(targets, etf_names)},
            {"项目": "当前持仓", "内容": format_symbol_list(current, etf_names)},
            {"项目": "买入列表", "内容": format_symbol_list(buys, etf_names)},
            {"项目": "卖出列表", "内容": format_symbol_list(sells, etf_names)},
            {"项目": "继续持有", "内容": format_symbol_list(holds, etf_names)},
            {"项目": "目标是否变化", "内容": "是" if changed else "否"},
        ]
    )
    show_dataframe_or_empty(summary, key=f"{key_prefix}_change_summary")


def render_strategy_explanation(strategy_name: str) -> None:
    if strategy_name in {"reduced_equal_weight_monthly", "equal_weight_monthly"}:
        st.info("该策略是固定篮子等权配置，目标 ETF 通常不会因日期变化而变化。")
        st.caption("策略定位：固定篮子基准策略 / 精选等权配置策略，用于和动态轮动策略做对照。")
    elif strategy_name == "momentum_rotation_monthly":
        st.info("该策略会根据所选信号日重新计算动量排名和趋势状态，目标 ETF 可能随日期变化。")
    else:
        st.warning("该策略仅用于研究或防守参考，不建议作为主跟随策略。")


def render_strategy_block(
    row: pd.Series,
    selected_date: date,
    observation_cash: float,
    etf_names: dict[str, str],
    primary: bool = False,
    key_prefix: str = "strategy",
) -> None:
    if row.empty:
        st.warning("未找到该策略信号，请先生成信号。")
        return

    strategy_name = str(row.get("strategy_name", ""))
    if primary:
        st.markdown(f"**当前查看：{strategy_label(strategy_name)}**")
    status_badge(str(row.get("strategy_status", "unknown")))
    render_strategy_explanation(strategy_name)
    execution = expected_execution_info(
        {
            "effective_signal_date": _display_value(row, "effective_signal_date", _display_value(row, "signal_date")),
            "latest_data_date": _display_value(row, "latest_data_date"),
        },
        PROJECT_ROOT,
    )

    render_compact_metric_grid(
        [
            ("实际计算信号日", _display_value(row, "effective_signal_date", _display_value(row, "signal_date"))),
            ("预计执行日", execution["date"]),
            ("预计剩余现金", _display_value(row, "estimated_remaining_cash")),
            ("调仓节奏", rebalance_rule_label(row.get("rebalance_rule"))),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )

    st.markdown("**完整人工执行计划**")
    render_manual_execution_plan(row, selected_date, observation_cash, etf_names, key_prefix)

    st.markdown("**组合变化摘要**")
    render_change_summary(row, etf_names, key_prefix)

    st.markdown("**目标组合**")
    show_dataframe_or_empty(parse_target_table(row, etf_names), key=f"{key_prefix}_target_table")

    rank_table = parse_rank_table(row)
    if strategy_name == "momentum_rotation_monthly":
        st.markdown("**动量排名表**")
        show_dataframe_or_empty(rank_table, empty_text="暂无动量排名。", key=f"{key_prefix}_rank_table")

    st.info("本页面用于模拟盘内测。买入计划不是开盘立即买入，而是结合数据质量、趋势条件和盘中价格分档执行。若数据质量不足或趋势失效，系统会自动降低买入金额或取消买入。")
    st.markdown("**买入计划**")
    show_dataframe_or_empty(parse_buy_table(row), empty_text="无买入计划。", key=f"{key_prefix}_buy_table")

    st.markdown("**盘中买入执行计划**")
    st.caption("如果没有实时行情，以下价格基于最新完整交易日收盘价、近期波动率、ATR 和均线生成，作为下一交易日参考买入价。")
    show_dataframe_or_empty(parse_intraday_execution_table(row), empty_text="无盘中买入执行计划。", key=f"{key_prefix}_intraday_execution_table", height=420)
    st.info("若触发失效条件，今日三档买入价全部取消，不再新增买入。")

    st.markdown("**资金不足提示**")
    show_dataframe_or_empty(parse_skip_table(row), empty_text="无资金不足提示。", key=f"{key_prefix}_skip_table")

    st.markdown("**卖出计划**")
    sell_table = parse_sell_table(row)
    if sell_table.empty:
        st.caption("无卖出计划：当前持仓为空，或当前持仓均在目标组合内。")
    else:
        show_dataframe_or_empty(sell_table, key=f"{key_prefix}_sell_table")

    st.markdown("**继续持有**")
    show_dataframe_or_empty(parse_hold_table(row), empty_text="无继续持有计划。", key=f"{key_prefix}_hold_table")

    st.markdown("**不操作原因**")
    st.write(_display_value(row, "no_action_reason", "无"))

    st.markdown("**风险提示**")
    st.write(_display_value(row, "risk_note", "仅用于人工观察，不构成投资建议。"))


def render_today_signal_tab(data: DashboardData, selected_strategy: str, selected_date: date, observation_cash: float) -> None:
    execution = expected_execution_info(data.overview, PROJECT_ROOT)
    st.subheader("今日信号")
    info = pd.DataFrame(
        [
            {"项目": "信号日", "内容": _format_cn_date(data.overview.get("effective_signal_date"))},
            {"项目": "最新数据", "内容": _format_cn_date(data.overview.get("latest_data_date"))},
            {"项目": "预计执行日", "内容": execution["date"]},
            {"项目": "执行说明", "内容": "下一个交易日按开盘后流动性情况执行"},
        ]
    )
    if execution["date"] == "待确认":
        info = pd.concat([info, pd.DataFrame([{"项目": "原因", "内容": execution["reason"]}])], ignore_index=True)
    st.dataframe(localize_columns(info), hide_index=True, width="stretch", height=220)
    render_strategy_block(
        _selected_strategy_row(data, selected_strategy),
        selected_date,
        observation_cash,
        data.etf_names,
        primary=True,
        key_prefix="today_signal",
    )


def render_strategy_comparison(data: DashboardData, selected_date: date, observation_cash: float, selected_strategy: str) -> None:
    st.subheader("策略对照")
    st.info("下表为不同策略的独立参考结果，不是组合后的总仓位。实际买入计划只使用当前启用的主策略。各策略独立参考，不可相加。")
    summary_rows = []
    for strategy_name in STRATEGY_ORDER:
        row = strategy_row(data.signals, strategy_name)
        recommended_etf, action, target_weight = _recommendation_summary(row, data.etf_names)
        summary_rows.append(
            {
                "策略": strategy_label(strategy_name),
                "状态": status_label(str(row.get("strategy_status", "unknown"))) if not row.empty else "未生成",
                "当前推荐 ETF": recommended_etf,
                "操作": action,
                "该策略单独运行时目标仓位": target_weight,
                "是否参与实际买入计划": "是" if strategy_name == selected_strategy else "否，仅供对照",
            }
        )
    st.dataframe(localize_columns(pd.DataFrame(summary_rows)), hide_index=True, width="stretch", height=260)
    with st.expander("策略说明", expanded=True):
        st.markdown(
            """
1. 动量轮动策略
含义：从 ETF 池中选择近期涨势最强、且趋势仍保持向上的 ETF。
适合：进攻型配置。
实际用途：当前项目的主策略候选。

2. 固定篮子基准策略
含义：固定选择一组代表性宽基或行业 ETF，不频繁轮动。
适合：作为长期基准对照。
实际用途：判断轮动策略是否真的优于简单持有。

3. 全池等权配置策略
含义：把全部合格 ETF 平均分配权重。
适合：观察整个 ETF 池的整体表现。
实际用途：作为分散化参考，不建议直接照抄买入。

4. 均衡轮动研究策略
含义：尝试在多个强势 ETF 之间分散配置。
适合：研究不同轮动方式的稳定性。
实际用途：仅供研究，不进入默认买入计划。

5. 防守参考策略
含义：当市场波动较大或主策略信号不稳定时，用于观察防守资产。
适合：控制回撤。
实际用途：只在防守模式下参考。
            """.strip()
        )

    selected = st.selectbox(
        "查看策略明细",
        STRATEGY_ORDER,
        format_func=strategy_label,
        key="diagnostic_strategy_selector",
    )
    render_strategy_block(
        strategy_row(data.signals, selected),
        selected_date,
        observation_cash,
        data.etf_names,
        key_prefix=f"compare_{selected}",
    )


def _safe_log_text(value: object) -> str:
    text = str(value or "")
    if any(hint in text for hint in TECHNICAL_ERROR_HINTS):
        return "页面或命令出现技术错误，详情仅在高级诊断信息中查看。"
    return text or "(无)"


def render_logs() -> None:
    run_logs = st.session_state.get("run_logs", [])
    if run_logs:
        st.markdown("**运行步骤**")
        st.dataframe(localize_columns(pd.DataFrame(run_logs)), hide_index=True, width="stretch", height=400)

    logs = st.session_state.get("command_logs", [])
    if not logs:
        return
    st.markdown("**详细日志**")
    for idx, item in enumerate(reversed(logs), start=1):
        with st.expander(f"命令 {idx}，返回码 {item['returncode']}", expanded=idx == 1):
            st.code(str(item["command"]), language="powershell")
            st.text_area("stdout", _safe_log_text(item["stdout"]), height=160, key=f"log_stdout_{idx}")
            st.text_area("stderr", _safe_log_text(item["stderr"]), height=120, key=f"log_stderr_{idx}")


def render_advanced_diagnostics(data: DashboardData | None = None) -> None:
    with st.expander("高级诊断信息", expanded=False):
        st.caption("以下内容用于排查本地面板、命令输出和环境问题，普通使用时无需查看。")
        cols = st.columns(3)
        if cols[0].button("打开输出文件夹", width="stretch", key="diag_open_output"):
            open_local_path(OUTPUT_DIR)
        if cols[1].button("打开持仓配置文件", width="stretch", key="diag_open_position"):
            open_local_path(CURRENT_POSITION)
        if cols[2].button("打开说明文档", width="stretch", key="diag_open_readme"):
            open_local_path(README)

        st.markdown("**运行环境**")
        st.code(str(PYTHON_EXE), language="text")
        if data is not None:
            st.markdown("**输出文件更新时间**")
            st.write(data.output_mtimes)

        render_error = st.session_state.get("last_render_error")
        if render_error:
            st.markdown("**最近一次页面异常详情**")
            st.code(str(render_error), language="text")

        compare_txt = OUTPUT_DIR / "compare_signal.txt"
        st.markdown("**原始 compare_signal.txt**")
        if compare_txt.exists():
            st.text_area("compare_signal.txt", compare_txt.read_text(encoding="utf-8", errors="ignore"), height=240, key="diag_compare_txt")
        else:
            st.caption("未找到 compare_signal.txt")


def render_page() -> None:
    data = load_dashboard_data(PROJECT_ROOT)
    observation_cash = default_observation_cash()
    selected_date, selected_strategy, observation_cash, command_ran = render_sidebar(data, observation_cash)
    if command_ran:
        data = load_dashboard_data(PROJECT_ROOT)

    st.title("交易工作台")
    st.caption("本地量化研究与人工观察信号。不自动下单，不连接券商，不构成投资建议。")
    render_top_summary(data, selected_strategy, observation_cash)

    date_errors = validate_signal_dates(data.overview, selected_date, PROJECT_ROOT)
    for error in date_errors:
        st.error(error)
    if date_errors:
        st.warning("当前输出未通过日期一致性校验，请重新生成信号或查看运行日志。")

    tabs = st.tabs(["总览", "今日信号", "ETF池", "数据质量", "策略诊断", "运行日志"])
    with tabs[0]:
        st.subheader("总览")
        render_overview(data.overview, selected_date, observation_cash, data)
        row = _selected_strategy_row(data, selected_strategy)
        recommended_etf, action, target_weight = _recommendation_summary(row, data.etf_names)
        st.dataframe(
            localize_columns(
                pd.DataFrame(
                    [
                        {"项目": "当前推荐 ETF", "内容": recommended_etf},
                        {"项目": "操作方向", "内容": action},
                        {"项目": "目标仓位", "内容": target_weight},
                        {"项目": "当前策略", "内容": strategy_label(selected_strategy)},
                    ]
                )
            ),
            hide_index=True,
            width="stretch",
            height=220,
        )
        render_current_position_module(data.etf_names)

    with tabs[1]:
        render_today_signal_tab(data, selected_strategy, selected_date, observation_cash)

    with tabs[2]:
        render_universe_module(data)

    with tabs[3]:
        render_data_quality_tab(data)

    with tabs[4]:
        render_strategy_comparison(data, selected_date, observation_cash, selected_strategy)
        render_advanced_diagnostics(data)

    with tabs[5]:
        render_logs()

    st.divider()
    st.caption("安全边界：不自动下单，不连接券商，不构成投资建议；所有结果仅用于人工观察和研究。")


def main() -> None:
    st.set_page_config(page_title="A股 ETF 低频量化观察面板", layout="wide")
    st.markdown(
        """
        <style>
        [data-testid="stToolbar"], #MainMenu, footer {display: none;}

        html, body, [class*="css"] {
            font-size: 15px;
            line-height: 1.45;
        }

        .block-container {
            max-width: 1320px;
            padding-top: 2.1rem;
            padding-bottom: 2rem;
            padding-left: 2rem;
            padding-right: 2rem;
        }

        section[data-testid="stSidebar"] {
            width: 300px !important;
            min-width: 300px !important;
        }

        section[data-testid="stSidebar"] > div {
            width: 300px !important;
            padding-top: 1.25rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }

        h1 {
            font-size: 34px !important;
            line-height: 1.35 !important;
            margin: 0 0 0.25rem !important;
            padding: 0.2rem 0 0.05rem !important;
            letter-spacing: 0 !important;
            overflow: visible !important;
        }

        h2, h3 {
            font-size: 24px !important;
            line-height: 1.25 !important;
            margin-top: 0.9rem !important;
            margin-bottom: 0.45rem !important;
            letter-spacing: 0 !important;
        }

        p, li, label, [data-testid="stMarkdownContainer"], .stCaptionContainer {
            font-size: 15px;
            line-height: 1.45;
            overflow-wrap: anywhere;
            word-break: break-word;
            white-space: normal;
        }

        div[data-testid="stVerticalBlock"] {
            gap: 0.55rem;
        }

        hr {
            margin: 1rem 0 !important;
        }

        .stAlert {
            padding: 0.55rem 0.75rem;
        }

        .stAlert div,
        .stAlert p {
            font-size: 14px !important;
            line-height: 1.4 !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }

        .sidebar-cash {
            margin: 0.25rem 0 0.75rem;
            padding: 0.5rem 0.6rem;
            border: 1px solid rgba(49, 51, 63, 0.14);
            border-radius: 6px;
            background: rgba(248, 249, 251, 0.8);
            color: rgba(49, 51, 63, 0.86);
            font-size: 14px;
            line-height: 1.35;
            overflow-wrap: anywhere;
        }

        .sidebar-cash strong {
            font-size: 15px;
            font-weight: 650;
        }

        .compact-metric-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.55rem;
            margin: 0.35rem 0 0.75rem;
        }

        .summary-metric-grid {
            position: sticky;
            top: 0.35rem;
            z-index: 10;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            padding: 0.5rem 0;
            background: rgba(255, 255, 255, 0.96);
            backdrop-filter: blur(4px);
            border-bottom: 1px solid rgba(49, 51, 63, 0.08);
        }

        .compact-metric-card {
            min-height: 72px;
            padding: 0.62rem 0.7rem;
            border: 1px solid rgba(49, 51, 63, 0.14);
            border-radius: 6px;
            background: #ffffff;
            overflow: visible;
        }

        .compact-metric-label {
            color: rgba(49, 51, 63, 0.62);
            font-size: 13px;
            line-height: 1.25;
            margin-bottom: 0.28rem;
            overflow-wrap: anywhere;
            white-space: normal;
        }

        .compact-metric-value {
            color: rgba(49, 51, 63, 0.95);
            font-size: 22px;
            font-weight: 650;
            line-height: 1.22;
            overflow-wrap: anywhere;
            word-break: break-word;
            white-space: normal;
        }

        .strategy-metric-grid .compact-metric-card {
            min-height: 64px;
        }

        .strategy-metric-grid .compact-metric-value {
            font-size: 20px;
        }

        div[data-testid="stMetric"] {
            padding: 0.45rem 0.55rem;
            border: 1px solid rgba(49, 51, 63, 0.14);
            border-radius: 6px;
        }

        div[data-testid="stMetricLabel"] p {
            font-size: 13px !important;
        }

        div[data-testid="stMetricValue"] {
            font-size: 22px !important;
            line-height: 1.25 !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }

        div[data-testid="stNumberInput"] input,
        div[data-testid="stTextInput"] input,
        div[data-baseweb="select"] {
            min-height: 34px;
            font-size: 14px;
        }

        div[data-testid="stButton"] button {
            min-height: 34px;
            padding: 0.25rem 0.6rem;
            font-size: 14px;
            border-radius: 6px;
            white-space: normal;
        }

        div[data-testid="stDataFrame"],
        div[data-testid="stTable"] {
            font-size: 14px;
        }

        div[data-testid="stDataFrame"] * {
            white-space: normal !important;
            text-overflow: clip !important;
        }

        @media (max-width: 1100px) {
            .compact-metric-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }

            .summary-metric-grid {
                position: static;
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }

            .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    try:
        st.session_state.pop("last_render_error", None)
        render_page()
    except Exception:  # noqa: BLE001
        st.session_state["last_render_error"] = traceback.format_exc()
        st.error("页面渲染异常，请刷新页面或重启本地面板。")
        render_advanced_diagnostics()


if __name__ == "__main__":
    main()
