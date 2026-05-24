from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html
import yaml


def _enable_project_signal_submodules() -> None:
    """Allow project signal.* modules when stdlib signal was imported first."""
    import signal as stdlib_signal

    signal_dir = Path(__file__).with_name("signal")
    search_locations = list(getattr(stdlib_signal, "__path__", []))
    signal_dir_text = str(signal_dir)
    if signal_dir.exists() and signal_dir_text not in search_locations:
        stdlib_signal.__path__ = [signal_dir_text, *search_locations]


_enable_project_signal_submodules()

from data.universe import UNIVERSE_META_PATH, build_universe_stage_counts
from data.sector_map import load_etf_sector_map
from data.storage import normalize_symbol as normalize_etf_symbol
from data.trading_calendar import get_next_trading_day, load_a_share_trading_calendar
from data.portfolio_store import (
    append_trade,
    calculate_weighted_average_cost,
    load_portfolio,
    save_portfolio,
    trade_from_buy,
)
from data.quotes import get_etf_quotes
from signal.daily_signal import EMPTY_POSITION_REASON, NO_POSITION_INPUT_REASON, ensure_current_position
from signal.trade_policy import normalize_error_message
from api import control_actions as action_api
from api.action_schema import format_datetime_shanghai, format_trade_date
from ui.components import localize_columns, show_dataframe_or_empty, status_badge
from ui.signal_parser import (
    DashboardData,
    MAIN_STRATEGY,
    buy_symbols,
    current_symbols,
    format_symbol_list,
    hold_symbols,
    load_dashboard_data,
    parse_buy_table,
    parse_intraday_execution_table,
    parse_hold_table,
    parse_rank_table,
    parse_sell_execution_table,
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
PORTFOLIO_SNAPSHOT = PROJECT_ROOT / "data" / "portfolio.csv"
PORTFOLIO_TRADES = PROJECT_ROOT / "data" / "portfolio_trades.csv"
README = PROJECT_ROOT / "README.md"

CommandArgs = str | list[str]

V21_FRONTEND_JSON_FILES = (
    "daily_decision_snapshot.json",
    "risk_gate_snapshot.json",
    "portfolio_snapshot.json",
    "order_intent.json",
    "learning_summary.json",
    "historical_ml_summary.json",
    "v21_backend_status.json",
)

V21_FRONTEND_OUTPUT_FILES = (
    "daily_decision_snapshot.csv",
    "daily_decision_snapshot.json",
    "risk_gate_snapshot.csv",
    "risk_gate_snapshot.json",
    "portfolio_snapshot.csv",
    "portfolio_snapshot.json",
    "order_intent.csv",
    "order_intent.json",
    "learning_summary.csv",
    "learning_summary.json",
    "historical_ml_summary.csv",
    "historical_ml_summary.json",
    "v21_backend_status.json",
)

REQUIRED_ACCEPTANCE_OUTPUT_FILES = V21_FRONTEND_OUTPUT_FILES

TECHNICAL_ERROR_HINTS = (
    "NotFoundError",
    "removeChild",
    "JavaScript",
    "static/js",
    "Traceback",
)

PENDING_EXECUTE_DATE_MARKERS = ("下一交易日", "待数据确认", "待下一交易日确认")
SIDEBAR_QUERY_PARAM = "side_panel"


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
        raw_error = str(row.get("error") or row.get("failure_reason") or row.get("filter_reason") or row.get("errors") or "")
        normalized = normalize_error_message(raw_error)
        symbol = normalize_etf_symbol(row.get("symbol", ""))
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


def _load_risk_gate() -> dict[str, Any]:
    path = OUTPUT_DIR / "risk_gate.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _risk_yes_no(value: Any) -> str:
    return "是" if bool(value) else "否"


def _risk_level_label(level: Any) -> str:
    labels = {
        "R0": "R0 正常",
        "R1": "R1 轻微扰动",
        "R2": "R2 谨慎",
        "R3": "R3 高风险",
        "R4": "R4 P0 预警",
    }
    return labels.get(str(level or "R0").upper(), "R0 正常")


def render_risk_warning_banner() -> None:
    gate = _load_risk_gate()
    if not gate:
        st.info("风险预警：尚未生成 risk_gate.json，当前页面只展示普通信号。")
        return
    level = str(gate.get("risk_level") or "R0").upper()
    score = gate.get("risk_score", 0)
    affected = gate.get("affected_sectors") or []
    affected_text = "、".join(str(item) for item in affected) if affected else "无"
    cap_text = f"{float(gate.get('equity_cap_override', 1.0) or 0.0):.0%}"
    explain = str(gate.get("explain") or "暂无风险说明。")
    if level == "R4":
        st.error("P0 风险预警：entry 已冻结，建议人工接管。")
    elif level == "R3":
        st.warning("普通权益买入已暂停，需要人工复核。")
    elif level == "R2":
        st.warning("风险升高，建议降低仓位并提高买入门槛。")
    elif level == "R1":
        st.info("存在轻微扰动，策略可正常运行。")
    else:
        st.success("当前未识别到生效的 P0 / 系统性风险。")
    render_compact_metric_grid(
        [
            ("次日风险等级", _risk_level_label(level)),
            ("次日风险分数", score),
            ("是否冻结买入", _risk_yes_no(gate.get("freeze_entry"))),
            ("权益仓位上限", cap_text),
            ("需要人工复核", _risk_yes_no(gate.get("require_manual_review"))),
            ("需要人工接管", _risk_yes_no(gate.get("manual_takeover_required"))),
            ("受影响方向", affected_text),
        ],
        class_name="compact-metric-grid risk-warning-grid",
    )
    st.caption(f"风险说明：{explain}")


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
    state = state if isinstance(state, dict) else {}
    eta_seconds = float(state.get("eta_seconds", 0) or 0)
    abnormal = (
        str(state.get("mode", "")) in {"日常增量", "daily_incremental", "incremental"}
        and eta_seconds > 1800
    )
    return "\n".join(
        [
            f"当前模式：{state.get('mode', '日常增量')}",
            f"当前阶段：{state.get('stage', '准备中')}",
            f"已处理 ETF：{state.get('current', 0)} / {state.get('total', 0)}",
            f"当前 ETF：{state.get('symbol', '')} {state.get('name', '')}".strip(),
            f"本地最新行情日期：{state.get('local_latest_date', 'N/A') or 'N/A'}",
            f"目标行情日期：{state.get('expected_signal_date', state.get('target_date', 'N/A')) or 'N/A'}",
            f"信号日期：{state.get('signal_date', state.get('expected_signal_date', state.get('target_date', 'N/A'))) or 'N/A'}",
            f"计划执行日：{state.get('execute_date', '待信号生成') or '待信号生成'}",
            "阶段说明：正在准备 ETF 池、检查本地缓存或等待首个行情任务返回；0/总数 不代表卡死。" if int(state.get("current") or 0) == 0 and int(state.get("total") or 0) >= 100 else "",
            f"本地缓存扫描进度：{state.get('current', 0)} / {state.get('total', 0)}" if str(state.get("stage", "")) == "扫描本地缓存" else "",
            f"已识别最新日期：{state.get('latest_data_date', state.get('local_latest_date', 'N/A')) or 'N/A'}",
            f"需要更新 ETF 数量：{state.get('need_update_count', 0)}",
            f"已跳过，因已是最新：{state.get('up_to_date_count', 0)}",
            f"缓存可用：{state.get('cached_success_count', 0)}",
            f"联网更新成功：{state.get('success_count', 0)}",
            f"联网失败但保留缓存：{state.get('cached_success_count', 0)}",
            f"无缓存且失败：{state.get('failed_count', 0)}",
            f"今日可参与排名 ETF 数：{state.get('rankable_count', '待生成')}",
            f"其他跳过：{state.get('skipped_count', 0)}",
            f"提示：{state.get('status', '')}" if state.get("status") else "",
            f"错误：{state.get('error', '')}" if state.get("error") else "",
            "当前刷新耗时异常，建议停止并使用最近可用缓存生成信号；请稍后运行修复缺失行情。" if abnormal else "",
            f"已耗时：{_fmt_duration(state.get('elapsed_seconds', 0))}",
            f"预计剩余时间：{_fmt_duration(eta_seconds)}",
        ]
    )


def run_update_and_generate_with_progress(signal_date: str | None, observation_cash: float, mode: str = "incremental") -> dict[str, Any]:
    from main import command_compare_signal, command_update_data

    mode_label = {"incremental": "日常增量", "refresh": "日常增量", "repair_missing": "修复缺失", "full_refresh": "全量重建", "rebuild": "全量重建"}.get(mode, mode)
    state: dict[str, Any] = {"mode": mode_label, "stage": "初始化", "current": 0, "total": 8}
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

    with st.status(f"{mode_label}：刷新行情并生成最新信号", expanded=True) as status:
        render({"mode": mode_label, "stage": "初始化", "current": 1, "total": 8})
        render({"mode": mode_label, "stage": "读取 ETF 池", "current": 2, "total": 8})
        render({"mode": mode_label, "stage": "检查缓存", "current": 3, "total": 8})
        update_metrics = command_update_data(mode=mode, max_workers=8, progress_callback=render, exit_on_all_failed=False)
        try:
            _load_local_market_dates.clear()
        except Exception:
            pass

        signal_file = OUTPUT_DIR / "compare_signal.csv"
        data_changed = int(update_metrics.get("success_count", 0) or 0) > 0
        cash_matches = False
        if signal_file.exists():
            try:
                previous = pd.read_csv(signal_file)
                cash_values = pd.to_numeric(previous["observation_cash"], errors="coerce").dropna() if "observation_cash" in previous.columns else pd.Series(dtype=float)
                cash_matches = bool(not cash_values.empty and abs(float(cash_values.iloc[0]) - float(observation_cash)) < 0.01)
            except Exception:
                cash_matches = False
        can_reuse_signal = not data_changed and cash_matches and signal_file.exists() and (OUTPUT_DIR / "compare_signal.txt").exists()

        if can_reuse_signal:
            render({"stage": "生成信号", "current": 6, "total": 8, "eta_seconds": 0, "status": "本地数据已是最新，复用已有信号"})
            append_run_event("复用已有信号", "本地数据已是最新，观察资金未变化，直接复用 compare_signal 结果。")
            result = pd.read_csv(signal_file) if signal_file.exists() else pd.DataFrame()
            if not result.empty:
                first = result.iloc[0].to_dict()
                render(
                    {
                        "signal_date": first.get("effective_signal_date") or first.get("signal_date"),
                        "execute_date": first.get("execute_date") or first.get("execution_date"),
                        "rankable_count": first.get("ranked_etf_count", "见信号表"),
                    }
                )
            signal_seconds = 0.0
        else:
            render({"stage": "生成信号", "current": 6, "total": 8, "eta_seconds": 0})
            signal_started = time.perf_counter()
            try:
                result = command_compare_signal(signal_date=signal_date, cash=observation_cash, use_cache=True, signal_mode="latest_after_refresh" if signal_date is None else "manual_selected_date")
            except Exception as exc:  # noqa: BLE001
                status.update(label="生成信号失败", state="error", expanded=True)
                st.error(str(exc))
                append_run_event("生成信号失败", str(exc))
                total_seconds = time.perf_counter() - started
                update_metrics = dict(update_metrics)
                update_metrics["signal_seconds"] = round(time.perf_counter() - signal_started, 3)
                update_metrics["total_seconds"] = round(total_seconds, 3)
                update_metrics["signal_error"] = str(exc)
                timing_box.json(update_metrics)
                append_logs(
                    [
                        {
                            "command": "in-process update-data --incremental + generate-signal --use-cache",
                            "returncode": 1,
                            "stdout": f"metrics={update_metrics}",
                            "stderr": str(exc),
                        }
                    ]
                )
                return update_metrics
            signal_seconds = time.perf_counter() - signal_started
            if isinstance(result, pd.DataFrame) and not result.empty:
                first = result.iloc[0].to_dict()
                render(
                    {
                        "signal_date": first.get("effective_signal_date") or first.get("signal_date"),
                        "execute_date": first.get("execute_date") or first.get("execution_date"),
                        "rankable_count": first.get("ranked_etf_count", "见信号表"),
                    }
                )

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
        f"成功更新 {update_metrics.get('success_count', 0)}｜缓存可用 {update_metrics.get('cached_success_count', 0)}｜跳过 {update_metrics.get('skipped_count', 0)}｜"
        f"失败 {update_metrics.get('failed_count', 0)}｜最新数据日期 {update_metrics.get('latest_data_date', 'N/A')}｜"
        f"信号文件更新时间 {signal_mtime}"
    )
    append_logs(
        [
            {
                "command": "in-process update-data --incremental + generate-signal --use-cache",
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
        actual_buy_table = _actual_buy_plan_frame(buy_table)
        actions = [str(item) for item in actual_buy_table["交易动作"].dropna().unique()] if not actual_buy_table.empty else []
        if any(item == "降低金额买入" for item in actions):
            action = "降低仓位买入"
        elif any(item == "买入" for item in actions):
            action = "今日可买入"
        elif actions:
            action = _candidate_action_text(actions[0])
        elif targets:
            action = "候选观察（不是买入）"
        else:
            action = "今日不买入"
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


def _is_actual_buy_action(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if any(token in text for token in ("观察", "watch", "等待", "禁止", "forbid", "wait")):
        return False
    return "买入" in text or "buy" in text


def _actual_buy_plan_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    action_col = "交易动作" if "交易动作" in frame.columns else "操作" if "操作" in frame.columns else ""
    if not action_col:
        return frame
    result = frame[frame[action_col].map(_is_actual_buy_action)].copy()
    if "建议仓位" in result.columns:
        size = pd.to_numeric(result["建议仓位"], errors="coerce").fillna(0)
        result = result[size > 0]
    return result


def _candidate_action_text(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"观察", "watch", "Watch"}:
        return "候选观察（不是买入）"
    if text == "WATCH":
        return "观察，不买入"
    if text in {"STANDARD_BUY", "PROBE_BUY", "FORBID_BUY", "HOLD", "RISK_EXIT", "TREND_DECAY_EXIT", "REPLACEMENT_EXIT"}:
        return _clean_display_value(text)
    if text == "校验通过":
        return "校验通过（仅代表价格数据可信，不等于买入信号）"
    if ":观察" in text:
        return text.replace(":观察", ":候选观察（不是买入）")
    return text or "无"


DISPLAY_VALUE_MAP = {
    "selected": "进入候选池",
    "eligible_not_selected": "通过过滤但未进候选池",
    "filtered_out": "未通过过滤",
    "up_to_date": "行情已是最新",
    "outdated": "行情需要更新",
    "cached_success": "使用缓存",
    "success": "更新成功",
    "failed": "更新失败",
    "not_required": "无需校验",
    "DRAFT": "订单草稿",
    "SIMULATION": "模拟执行",
    "MANUAL_CONFIRM": "人工确认",
    "DRAFT_BUY": "买入订单草稿",
    "BLOCKED_BUY": "买入已阻断",
    "DRAFT_EXIT": "卖出订单草稿",
    "NO_ORDER": "无订单意图",
    "BUY": "买入方向",
    "SELL": "卖出方向",
    "LIMIT": "人工限价",
    "V2_MODULAR": "V2.1 模块化总控信号",
    "V1_LEGACY": "V1 传统信号（仅用于对照）",
    "WATCH": "观察，不买入",
    "watch": "观察，不买入",
    "HOLD": "继续持有",
    "FORBID_BUY": "禁止买入",
    "STANDARD_BUY": "标准买入",
    "PROBE_BUY": "试探买入",
    "RISK_EXIT": "风险退出",
    "TREND_DECAY_EXIT": "趋势衰减退出",
    "REPLACEMENT_EXIT": "调仓替换退出",
    "entry_signal.csv": "买入信号输出",
    "exit_signal.csv": "退出信号输出",
    "v21_orchestrator": "V2.1 总控",
    "completed": "总控已完成",
    "completed_with_fallback": "总控已完成（存在降级说明）",
    "true": "是",
    "false": "否",
    "True": "是",
    "False": "否",
}

DISPLAY_EMBEDDED_REPLACEMENTS = {
    "fallback_reason": "降级原因",
    "risk_block_reason": "风险阻断原因",
    "manual_takeover_required": "需要人工接管",
    "freeze_entry": "当前风险门控已冻结新买入",
    "DRAFT/MANUAL_CONFIRM": "订单草稿/人工确认",
    "MANUAL_CONFIRM": "人工确认",
    "SIMULATION": "模拟执行",
    "DRAFT": "订单草稿",
    "V2_MODULAR": "V2.1 模块化总控信号",
    "V1_LEGACY": "V1 传统信号（仅用于对照）",
    "qmt_execution": "QMT 执行模块",
    "historical_ml": "历史学习模块",
    "risk_warning": "风险预警模块",
    "RiskGate": "风险门控",
    "entry 信号": "买入信号",
    "entry_signal.csv": "买入信号输出",
    "exit_signal.csv": "退出信号输出",
    "V1/V2 selected ETFs match": "V1 与 V2.1 候选 ETF 一致",
    "V1 empty while V2 has candidates": "V1 无候选，V2.1 已产生候选 ETF",
    "V2 has no selected candidates; likely filtered by market, trend, or data rules": "V2.1 暂无候选 ETF，可能受市场、趋势或数据规则限制",
    "V1/V2 candidate sets differ; V2 no-buy reasons": "V1 与 V2.1 候选集合不同；V2.1 未买入原因",
    "V1/V2 candidate sets differ": "V1 与 V2.1 候选集合不同",
}

NAME_MISSING_TEXT = "名称未匹配"
V2_CODE_COLUMNS = ("etf_code", "ETF代码", "symbol", "code")
V2_NAME_COLUMNS = ("etf_name", "ETF名称", "name", "fund_name")


def _clean_display_value(value: Any, default: str = "无") -> str:
    if value in ("", None):
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text):
        return format_datetime_shanghai(text) or default
    if text.lower() in EMPTY_REASON_TEXTS:
        return default
    if text == "未记录":
        return default
    if text in DISPLAY_VALUE_MAP:
        return DISPLAY_VALUE_MAP[text]
    return _translate_embedded_display_terms(text)


def _translate_embedded_display_terms(text: str) -> str:
    result = text
    for raw, translated in DISPLAY_EMBEDDED_REPLACEMENTS.items():
        result = result.replace(raw, translated)
    return result


def translate_status(value: Any, default: str = "暂无数据") -> str:
    return _clean_display_value(value, default)


def translate_signal_action(value: Any, default: str = "暂无动作") -> str:
    return _candidate_action_text(value) if value not in ("", None) else default


def translate_execution_mode(value: Any, default: str = "暂无执行模式") -> str:
    return _clean_display_value(value, default)


def translate_risk_level(value: Any, default: str = "暂无风险等级") -> str:
    return _clean_display_value(value, default)


def clean_display_value(value: Any, default: str = "暂无数据") -> str:
    return _clean_display_value(value, default)


def _is_missing_display_value(value: Any) -> bool:
    text = _clean_display_value(value, "")
    return not text or text in {"无", "未生成", "N/A", "空仓", "未记录", NAME_MISSING_TEXT}


def _clean_display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    for col in out.columns:
        if out[col].dtype == bool:
            out[col] = out[col].map(lambda value: "是" if value else "否")
        else:
            out[col] = out[col].map(_clean_display_value)
    return out


def _split_signal_items(value: Any) -> list[str]:
    text = _clean_display_value(value, "")
    if not text or text in {"无", "未生成", "N/A", "空仓"}:
        return []
    return [
        item.strip(" ，,;；")
        for item in re.split(r"\s*\|\s*|、|，|,", text)
        if item.strip(" ，,;；")
    ]


def _split_code_and_text(item: str) -> tuple[str, str]:
    text = _clean_display_value(item, "")
    match = re.match(r"^(?P<code>\d{6})(?:\s*[:：]\s*|\s+)?(?P<rest>.*)$", text)
    if not match:
        return "", text
    return match.group("code"), match.group("rest").strip()


def _first_existing_column(frame: pd.DataFrame, columns: tuple[str, ...]) -> str | None:
    for col in columns:
        if col in frame.columns:
            return col
    return None


def _normal_etf_code(value: Any) -> str:
    code = normalize_etf_symbol(value)
    return code.zfill(6) if code.isdigit() else code


@st.cache_data(ttl=300, show_spinner=False)
def _load_sector_records() -> dict[str, dict[str, Any]]:
    return load_etf_sector_map(PROJECT_ROOT / "config" / "etf_sector_map.yaml")


def _value_from_columns(row: pd.Series, columns: tuple[str, ...], default: str = "") -> str:
    for col in columns:
        if col in row.index and not _is_missing_display_value(row.get(col)):
            return _clean_display_value(row.get(col), default)
    return default


@st.cache_data(ttl=120, show_spinner=False)
def _load_v2_output_frame(filename: str) -> pd.DataFrame:
    path = OUTPUT_DIR / filename
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        return pd.DataFrame()


def _merge_v2_reference_frame(frame: pd.DataFrame, source: str, lookup: dict[str, dict[str, str]]) -> None:
    if frame.empty:
        return
    code_col = _first_existing_column(frame, V2_CODE_COLUMNS)
    if not code_col:
        return
    for _, row in frame.iterrows():
        code = _normal_etf_code(row.get(code_col))
        if not code:
            continue
        item = lookup.setdefault(code, {"ETF代码": code})
        name = _value_from_columns(row, V2_NAME_COLUMNS)
        if name and _is_missing_display_value(item.get("ETF名称")):
            item["ETF名称"] = name
            item["名称来源"] = source
        sector = _value_from_columns(row, ("level1_sector", "sector", "入选板块", "theme"))
        if sector and _is_missing_display_value(item.get("入选板块")):
            item["入选板块"] = sector
        action = _value_from_columns(row, ("entry_action", "buy_action", "交易动作", "action"))
        if action and _is_missing_display_value(item.get("买入动作")):
            item["买入动作"] = _candidate_action_text(action)
        weight = _value_from_columns(row, ("target_weight", "position_size", "建议仓位", "weight"), "0")
        if not _is_missing_display_value(weight) and _is_missing_display_value(item.get("建议仓位")):
            item["建议仓位"] = weight
        confidence = _value_from_columns(row, ("confidence", "信号置信度", "置信度"))
        if confidence and _is_missing_display_value(item.get("置信度")):
            item["置信度"] = confidence
        reason = _value_from_columns(row, ("reason", "entry_reason", "selection_reason", "买入原因"))
        if reason and _is_missing_display_value(item.get("完整原因")):
            item["完整原因"] = reason
        ml_advice = _value_from_columns(row, ("ml_entry_advice", "ML观察建议"), "无ML建议")
        if ml_advice and _is_missing_display_value(item.get("ML观察建议")):
            item["ML观察建议"] = ml_advice
        ml_confidence = _value_from_columns(row, ("ml_confidence", "ML置信度"), "0")
        if not _is_missing_display_value(ml_confidence) and _is_missing_display_value(item.get("ML置信度")):
            item["ML置信度"] = ml_confidence
        ml_reason = _value_from_columns(row, ("ml_reason", "ML原因"), "未找到历史校准建议，维持原 entry 判断。")
        if ml_reason and _is_missing_display_value(item.get("ML原因")):
            item["ML原因"] = ml_reason
        ml_action = _value_from_columns(row, ("ml_action_suggestion", "ML动作建议"), "NO_ML")
        if ml_action and _is_missing_display_value(item.get("ML动作建议")):
            item["ML动作建议"] = ml_action


def build_v2_etf_lookup(
    etf_names: dict[str, str] | None = None,
    entry: pd.DataFrame | None = None,
    pre_selection: pd.DataFrame | None = None,
    cases: pd.DataFrame | None = None,
) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for code, record in _load_sector_records().items():
        normalized = _normal_etf_code(code)
        item = lookup.setdefault(normalized, {"ETF代码": normalized})
        if not _is_missing_display_value(record.get("name")):
            item["ETF名称"] = _clean_display_value(record.get("name"), NAME_MISSING_TEXT)
            item["名称来源"] = "ETF 行业主题映射"
        item["入选板块"] = _clean_display_value(record.get("sector_l2") or record.get("sector"), "行业未录入")
        item["主题"] = _clean_display_value(record.get("theme"), "主题未录入")
        item["风险分组"] = _clean_display_value(record.get("risk_group"), "风险分组未录入")
    _merge_v2_reference_frame(entry if entry is not None else _load_v2_output_frame("entry_signal.csv"), "entry_signal.csv", lookup)
    _merge_v2_reference_frame(pre_selection if pre_selection is not None else _load_v2_output_frame("pre_selection_result.csv"), "pre_selection_result.csv", lookup)
    _merge_v2_reference_frame(cases if cases is not None else _load_v2_output_frame("signal_cases.csv"), "signal_cases.csv", lookup)
    for code, name in (etf_names or {}).items():
        normalized = _normal_etf_code(code)
        item = lookup.setdefault(normalized, {"ETF代码": normalized})
        if not _is_missing_display_value(name) and _is_missing_display_value(item.get("ETF名称")):
            item["ETF名称"] = _clean_display_value(name, NAME_MISSING_TEXT)
            item["名称来源"] = "ETF universe"
    for item in lookup.values():
        item.setdefault("ETF名称", NAME_MISSING_TEXT)
        item.setdefault("名称来源", "未匹配")
        item.setdefault("ML观察建议", "无ML建议")
        item.setdefault("ML置信度", "0")
        item.setdefault("ML原因", "未找到历史校准建议，维持原 entry 判断。")
        item.setdefault("ML动作建议", "NO_ML")
    return lookup


def _lookup_v2_info(code: str, lookup: dict[str, dict[str, str]]) -> dict[str, str]:
    normalized = _normal_etf_code(code)
    item = dict(lookup.get(normalized, {}))
    item.setdefault("ETF代码", normalized or NAME_MISSING_TEXT)
    item.setdefault("ETF名称", NAME_MISSING_TEXT)
    item.setdefault("名称来源", "未匹配")
    return item


def _truncate_reason(value: Any, limit: int = 52) -> str:
    text = _clean_display_value(value, "")
    if len(text) <= limit:
        return text or "无"
    return f"{text[:limit]}..."


def _with_reason_preview(frame: pd.DataFrame, reason_col: str = "完整原因") -> pd.DataFrame:
    if frame.empty or reason_col not in frame.columns:
        return frame
    out = frame.copy()
    out["原因摘要"] = out[reason_col].map(_truncate_reason)
    cols = [col for col in out.columns if col != reason_col]
    if "原因摘要" in cols:
        cols.remove("原因摘要")
    return out[[*cols, "原因摘要", reason_col]]


def build_v2_candidate_table(
    row: pd.Series,
    cases: pd.DataFrame | None = None,
    etf_names: dict[str, str] | None = None,
    lookup: dict[str, dict[str, str]] | None = None,
) -> pd.DataFrame:
    lookup = lookup or build_v2_etf_lookup(etf_names=etf_names, cases=cases)
    candidates = []
    latest_cases = cases if cases is not None else pd.DataFrame()
    if latest_cases is not None and not latest_cases.empty:
        latest_cases = latest_cases.copy()
        if "trade_date" in latest_cases.columns:
            latest_trade_date = str(latest_cases["trade_date"].max())
            latest_cases = latest_cases[latest_cases["trade_date"].astype(str) == latest_trade_date]
        for _, item in latest_cases.iterrows():
            symbol = _normal_etf_code(item.get("etf_code", item.get("symbol", "")))
            info = _lookup_v2_info(symbol, lookup)
            candidates.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": _clean_display_value(item.get("etf_name") or info.get("ETF名称"), NAME_MISSING_TEXT),
                    "入选板块": _clean_display_value(item.get("level1_sector") or info.get("入选板块"), "无"),
                    "主题": _clean_display_value(info.get("主题"), "主题未录入"),
                    "风险分组": _clean_display_value(info.get("风险分组"), "风险分组未录入"),
                    "排名": _clean_display_value(item.get("etf_rank"), "无"),
                    "买入动作": _candidate_action_text(item.get("entry_action")),
                    "建议仓位": _clean_display_value(item.get("target_weight"), "0"),
                    "置信度": _clean_display_value(item.get("confidence") or info.get("置信度"), "无"),
                    "ML观察建议": _clean_display_value(item.get("ml_entry_advice") or info.get("ML观察建议"), "无ML建议"),
                    "ML置信度": _clean_display_value(item.get("ml_confidence") or info.get("ML置信度"), "0"),
                    "ML动作建议": _clean_display_value(item.get("ml_action_suggestion") or info.get("ML动作建议"), "NO_ML"),
                    "ML原因": _clean_display_value(item.get("ml_reason") or info.get("ML原因"), "未找到历史校准建议，维持原 entry 判断。"),
                    "ML观察说明": "仅供观察，不自动修改交易参数。",
                    "完整原因": _clean_display_value(item.get("reason") or info.get("完整原因"), "无"),
                    "名称来源": info.get("名称来源", "未匹配"),
                }
            )
    if not candidates:
        for item in _split_signal_items(row.get("modular_candidate_etfs", row.get("v2_selected_etfs", ""))):
            symbol, text = _split_code_and_text(item)
            info = _lookup_v2_info(symbol, lookup)
            candidates.append(
                {
                    "ETF代码": symbol or text,
                    "ETF名称": _clean_display_value(info.get("ETF名称"), text or NAME_MISSING_TEXT),
                    "入选板块": _clean_display_value(info.get("入选板块") or row.get("modular_selected_sectors"), "无"),
                    "主题": _clean_display_value(info.get("主题"), "主题未录入"),
                    "风险分组": _clean_display_value(info.get("风险分组"), "风险分组未录入"),
                    "买入动作": "候选观察（不是买入）",
                    "ML观察建议": _clean_display_value(info.get("ML观察建议"), "无ML建议"),
                    "ML置信度": _clean_display_value(info.get("ML置信度"), "0"),
                    "ML动作建议": _clean_display_value(info.get("ML动作建议"), "NO_ML"),
                    "ML原因": _clean_display_value(info.get("ML原因"), "未找到历史校准建议，维持原 entry 判断。"),
                    "ML观察说明": "仅供观察，不自动修改交易参数。",
                    "完整原因": _clean_display_value(info.get("完整原因") or row.get("v2_reason"), "无"),
                    "名称来源": info.get("名称来源", "未匹配"),
                }
            )
    return _with_reason_preview(_clean_display_frame(pd.DataFrame(candidates)))


def build_v2_action_table(
    value: Any,
    etf_names: dict[str, str] | None = None,
    action_label: str = "动作",
    lookup: dict[str, dict[str, str]] | None = None,
    actual_buy_symbols: set[str] | None = None,
) -> pd.DataFrame:
    rows = []
    lookup = lookup or build_v2_etf_lookup(etf_names=etf_names)
    actual_buy_symbols = {_normal_etf_code(item) for item in (actual_buy_symbols or set())}
    for item in _split_signal_items(value):
        symbol, action = _split_code_and_text(item)
        normalized = _normal_etf_code(symbol)
        info = _lookup_v2_info(normalized, lookup)
        final_action = _candidate_action_text(action or item or info.get(action_label))
        rows.append(
            {
                "ETF代码": normalized or NAME_MISSING_TEXT,
                "ETF名称": info.get("ETF名称", NAME_MISSING_TEXT),
                "入选板块": _clean_display_value(info.get("入选板块"), "无"),
                "主题": _clean_display_value(info.get("主题"), "主题未录入"),
                "风险分组": _clean_display_value(info.get("风险分组"), "风险分组未录入"),
                action_label: final_action,
                "是否实际买入": "是" if normalized in actual_buy_symbols or _is_actual_buy_action(final_action) else "否",
                "建议仓位": _clean_display_value(info.get("建议仓位"), "0"),
                "置信度": _clean_display_value(info.get("置信度"), "无"),
                "ML观察建议": _clean_display_value(info.get("ML观察建议"), "无ML建议"),
                "ML置信度": _clean_display_value(info.get("ML置信度"), "0"),
                "ML动作建议": _clean_display_value(info.get("ML动作建议"), "NO_ML"),
                "ML原因": _clean_display_value(info.get("ML原因"), "未找到历史校准建议，维持原 entry 判断。"),
                "ML观察说明": "仅供观察，不自动修改交易参数。",
                "完整原因": _clean_display_value(info.get("完整原因"), "无"),
                "名称来源": info.get("名称来源", "未匹配"),
            }
        )
    return _with_reason_preview(_clean_display_frame(pd.DataFrame(rows)))


def build_v2_status_cards(row: pd.Series, comparison_row: pd.Series, cases: pd.DataFrame) -> list[tuple[str, Any]]:
    actual_buy = comparison_row.get("v2_actual_buy_etfs", row.get("v2_actual_buy_etfs", "")) if not comparison_row.empty else row.get("v2_actual_buy_etfs", "")
    candidate_count = row.get("modular_candidate_count", "")
    if _empty_signal_text(candidate_count):
        candidate_count = len(_split_signal_items(row.get("modular_candidate_etfs", row.get("v2_selected_etfs", ""))))
    actual_buy_count = 0 if _empty_signal_text(actual_buy) else len(_split_signal_items(actual_buy))
    sample_state = build_hindsight_sample_status(cases)
    return [
        ("当前信号版本", _clean_display_value(row.get("signal_version", "V2_MODULAR"), "V2_MODULAR")),
        ("ML 观察模式", _clean_display_value(row.get("ml_observation_status", row.get("v2_ml_observation_status", row.get("modular_ml_observation_status"))), "未启用")),
        ("市场状态", _clean_display_value(row.get("modular_market_state", row.get("v2_market_state")), "未生成")),
        ("是否有实际买入计划", "是" if actual_buy_count > 0 else "否"),
        ("候选数量", candidate_count),
        ("实际买入数量", actual_buy_count),
        ("当前后验样本状态", sample_state),
    ]


def build_hindsight_sample_status(cases: pd.DataFrame) -> str:
    if cases.empty:
        return "病例库尚未生成，等待首次信号积累。"
    if "hindsight_label" not in cases.columns:
        return "病例库缺少后验字段，请重新生成信号。"
    total = len(cases)
    insufficient = _count_matching(cases, "hindsight_label", {"样本不足"})
    completed = total - insufficient
    unique_dates = int(cases["trade_date"].astype(str).nunique()) if "trade_date" in cases.columns else 0
    if total > 0 and insufficient == total:
        if unique_dates <= 1:
            return "当前仅包含最新信号样本，病例库尚在积累期；未来 1/3/5/10 个交易日后会逐步回填。"
        return "当前后验样本均缺少足够未来交易日行情，不代表系统错误；后续运行会自动回填。"
    return f"已回填 {completed} 条，样本不足 {insufficient} 条；后验统计按 1/3/5/10 个交易日逐步更新。"


def build_output_file_status(output_dir: Path = OUTPUT_DIR) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for filename in REQUIRED_ACCEPTANCE_OUTPUT_FILES:
        path = output_dir / filename
        rows.append(
            {
                "输出文件": filename,
                "读取状态": "已生成" if path.exists() else "缺失，页面将降级显示",
                "说明": "缺失不会导致页面崩溃；请重新生成信号补齐。" if not path.exists() else "可读取",
            }
        )
    return pd.DataFrame(rows)


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
    data_cutoff_date = _parse_date(overview.get("data_cutoff_date")) or latest_data_date

    if requested_date and effective_date and effective_date > requested_date:
        errors.append("实际计算信号日不能晚于用户选择的信号日。")

    if requested_date and effective_date and effective_date < requested_date:
        errors.append("手动选择日期必须严格使用用户选择的信号日，不能自动回退到更早日期。")
    if last_mode == "manual" and data_cutoff_date and selected_date > data_cutoff_date:
        errors.append(
            f"你选择的是 {selected_date.isoformat()}，但本地数据只更新到 {data_cutoff_date.isoformat()}。"
            f"系统不能用 {data_cutoff_date.isoformat()} 冒充 {selected_date.isoformat()} 计算信号。"
            f"请点击刷新行情，或改选 {data_cutoff_date.isoformat()} 做复盘。"
        )

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
    holdings = list(current_position.get("holdings", []))
    if not holdings and PORTFOLIO_SNAPSHOT.exists():
        portfolio = load_portfolio(PORTFOLIO_SNAPSHOT)
        holdings = [
            {
                "symbol": item["ETF代码"],
                "name": item["ETF名称"],
                "shares": item["持仓份额"],
                "average_buy_price": item["平均买入价"],
                "last_buy_date": item["最近买入日期"],
                "note": item["备注"],
            }
            for _, item in portfolio.iterrows()
        ]
    for item in holdings:
        symbol = str(item.get("symbol", "")).zfill(6)
        average_buy_price = float(item.get("average_buy_price", item.get("cost_price", 0)) or 0)
        rows.append(
            {
                "id": next_id,
                "symbol": symbol,
                "name": etf_names.get(symbol, symbol),
                "shares": float(item.get("shares", 0) or 0),
                "average_buy_price": average_buy_price,
                "last_buy_date": str(item.get("last_buy_date", "") or date.today().isoformat()),
                "note": str(item.get("note", "")),
            }
        )
        next_id += 1
    return rows or [{"id": next_id, "symbol": "", "name": "", "shares": 0.0, "average_buy_price": 0.0, "last_buy_date": date.today().isoformat(), "note": ""}]


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
    st.session_state["position_original_rows"] = {int(row["id"]): dict(row) for row in rows if row.get("symbol")}
    st.session_state["position_next_row_id"] = max_id + 1


def _normalize_symbol(value: Any) -> str:
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return normalize_etf_symbol(value)


def _text_or_default(value: Any, default: str = "") -> str:
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    text = str(value or "").strip()
    return text or default


def _float_or_zero(value: Any) -> float:
    try:
        if value in ("", None) or pd.isna(value):
            return 0.0
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _save_current_position(cash: float, current_empty: bool, holdings: list[dict[str, Any]]) -> None:
    save_portfolio(
        holdings,
        cash=cash,
        current_empty=current_empty,
        portfolio_path=PORTFOLIO_SNAPSHOT,
        current_position_path=CURRENT_POSITION,
    )


def _latest_position_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    try:
        return get_etf_quotes(set(symbols), data_dir=PROJECT_ROOT / "data" / "cache")
    except Exception:
        return {
            symbol: {
                "code": symbol,
                "latest_price": None,
                "quote_date": "",
                "quote_time": "",
                "source": "行情接口",
                "price_status": "数据不可用",
                "frontend_message": "行情不可用，等待刷新或人工确认。",
                "debug_message": "获取行情失败",
                "price_actionable": False,
            }
            for symbol in symbols
        }


def _position_metrics(row: dict[str, Any], quote: dict[str, Any] | None) -> dict[str, Any]:
    shares = float(row.get("shares", 0) or 0)
    average_buy_price = float(row.get("average_buy_price", 0) or 0)
    position_cost = shares * average_buy_price
    quote = quote or {}
    market_price = float(quote.get("latest_price") or 0) if quote.get("price_status") != "价格异常，已停用" else 0.0
    market_value = shares * market_price if market_price > 0 else 0.0
    pnl = market_value - position_cost if market_price > 0 and position_cost > 0 else 0.0
    pnl_rate = pnl / position_cost if position_cost > 0 else 0.0
    return {
        "持仓成本": position_cost,
        "当前价格": market_price,
        "当前市值": market_value,
        "浮动盈亏": pnl,
        "浮动盈亏率": pnl_rate,
        "报价日期": quote.get("quote_date", ""),
        "报价时间": quote.get("quote_time", ""),
        "价格来源": quote.get("source", ""),
        "价格状态": quote.get("price_status", quote.get("status", "数据不可用")),
        "价格说明": "；".join(
            item
            for item in [
                str(quote.get("frontend_message", "") or ""),
                str(quote.get("daily_history_message", "") or ""),
            ]
            if item
        ),
        "行情调试": quote.get("debug") or {},
    }


def _validate_holdings(rows: list[dict[str, Any]], etf_names: dict[str, str]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    holdings: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol"))
        shares = _float_or_zero(row.get("shares"))
        average_buy_price = _float_or_zero(row.get("average_buy_price"))
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
        if average_buy_price <= 0:
            errors.append("请填写平均买入价，否则无法计算盈亏和卖出计划。")
            continue
        if shares % 100 != 0:
            warnings.append(f"{symbol} 持有份额建议为 100 的整数倍。")
        holdings.append(
            {
                "id": int(row.get("id", 0) or 0),
                "symbol": symbol,
                "name": etf_names.get(symbol, ""),
                "shares": float(shares),
                "average_buy_price": average_buy_price,
                "cost_price": average_buy_price,
                    "last_buy_date": _text_or_default(row.get("last_buy_date"), date.today().isoformat()),
                    "note": _text_or_default(row.get("note")),
            }
        )
    return holdings, errors, warnings


def _merge_holdings_by_symbol(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in holdings:
        symbol = item["symbol"]
        if symbol not in merged:
            merged[symbol] = dict(item)
            continue
        old = merged[symbol]
        new_average = calculate_weighted_average_cost(
            float(old.get("shares", 0) or 0),
            float(old.get("average_buy_price", 0) or 0),
            float(item.get("shares", 0) or 0),
            float(item.get("average_buy_price", 0) or 0),
        )
        old["shares"] = float(old.get("shares", 0) or 0) + float(item.get("shares", 0) or 0)
        old["average_buy_price"] = new_average
        old["cost_price"] = new_average
        old["last_buy_date"] = str(item.get("last_buy_date") or old.get("last_buy_date") or date.today().isoformat())
        notes = [str(old.get("note") or "").strip(), str(item.get("note") or "").strip()]
        old["note"] = "；".join(dict.fromkeys([note for note in notes if note]))
    return list(merged.values())


def _append_position_trades(holdings: list[dict[str, Any]]) -> None:
    original_rows = st.session_state.get("position_original_rows", {})
    for item in holdings:
        row_id = int(item.get("id", 0) or 0)
        original = original_rows.get(row_id)
        changed_existing = False
        if original:
            changed_existing = any(
                [
                    str(original.get("symbol")) != str(item.get("symbol")),
                    abs(float(original.get("shares", 0) or 0) - float(item.get("shares", 0) or 0)) > 1e-8,
                    abs(float(original.get("average_buy_price", 0) or 0) - float(item.get("average_buy_price", 0) or 0)) > 1e-8,
                ]
            )
        if original and not changed_existing:
            continue
        if original:
            append_trade(
                {
                    "日期": item.get("last_buy_date") or date.today().isoformat(),
                    "ETF代码": item["symbol"],
                    "ETF名称": item.get("name", ""),
                    "操作类型": "手动调整",
                    "成交价格": item["average_buy_price"],
                    "成交份额": item["shares"],
                    "成交金额": item["average_buy_price"] * item["shares"],
                    "交易原因": "手动调整当前持仓快照",
                    "备注": item.get("note", ""),
                },
                PORTFOLIO_TRADES,
            )
        else:
            append_trade(
                trade_from_buy(
                    item["symbol"],
                    item.get("name", ""),
                    item["average_buy_price"],
                    item["shares"],
                    trade_date=item.get("last_buy_date") or date.today().isoformat(),
                    note=item.get("note", ""),
                ),
                PORTFOLIO_TRADES,
            )


def _query_param_text(key: str) -> str | None:
    value = st.query_params.get(key)
    if isinstance(value, list):
        return str(value[-1]) if value else None
    return str(value) if value is not None else None


def sidebar_is_open() -> bool:
    value = _query_param_text(SIDEBAR_QUERY_PARAM)
    if value == "0":
        st.session_state["sidebar_open"] = False
    elif value == "1":
        st.session_state["sidebar_open"] = True
    elif "sidebar_open" not in st.session_state:
        st.session_state["sidebar_open"] = True
    return bool(st.session_state.get("sidebar_open", True))


def sidebar_toggle_href(open_sidebar: bool) -> str:
    params: dict[str, Any] = {}
    for key, value in st.query_params.items():
        if key != SIDEBAR_QUERY_PARAM:
            params[key] = value
    params[SIDEBAR_QUERY_PARAM] = "1" if open_sidebar else "0"
    return "?" + urlencode(params, doseq=True)


def render_sidebar_layout(sidebar_open: bool) -> None:
    collapsed_css = ""
    if not sidebar_open:
        collapsed_css = """
        section[data-testid="stSidebar"] {
            display: none !important;
            width: 0 !important;
            min-width: 0 !important;
        }

        .sidebar-toggle-link {
            top: 3.25rem;
        }

        .block-container {
            max-width: min(1500px, calc(100vw - 2rem)) !important;
            padding-top: 6rem !important;
            padding-left: 2rem !important;
            padding-right: 2rem !important;
        }
        """

    st.markdown(
        f"""
        <style>
        .sidebar-toggle-link {{
            position: fixed;
            top: 0.85rem;
            left: 0.85rem;
            z-index: 100000;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 96px;
            min-height: 42px;
            padding: 0.48rem 0.88rem;
            border: 1px solid rgba(49, 51, 63, 0.24);
            border-radius: 6px;
            background: #ffffff;
            color: rgba(49, 51, 63, 0.92) !important;
            font-size: 14px;
            font-weight: 650;
            line-height: 1.2;
            text-decoration: none !important;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.12);
        }}

        .sidebar-toggle-link:hover {{
            border-color: rgba(49, 51, 63, 0.42);
            background: #f8fafc;
            color: rgba(49, 51, 63, 1) !important;
        }}

        section[data-testid="stSidebar"] > div {{
            padding-top: 4.25rem !important;
        }}

        {collapsed_css}

        @media (max-width: 1100px) {{
            .sidebar-toggle-link {{
                top: 0.65rem;
                left: 0.65rem;
            }}

            .block-container {{
                padding-top: 4rem !important;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_toggle(sidebar_open: bool) -> None:
    label = "收起侧栏" if sidebar_open else "展开侧栏"
    href = sidebar_toggle_href(not sidebar_open)
    container = st.sidebar if sidebar_open else st
    container.markdown(
        f'<a class="sidebar-toggle-link" href="{escape(href, quote=True)}" target="_self">{escape(label)}</a>',
        unsafe_allow_html=True,
    )


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

    selected_strategy = MAIN_STRATEGY

    st.sidebar.divider()
    st.sidebar.markdown("**日常使用**")
    st.sidebar.caption("默认只做日常增量刷新：已有目标行情日期的 ETF 会直接跳过。")
    if st.sidebar.button("刷新行情并生成最新信号", width="stretch", type="primary", key="btn_update_and_generate"):
        st.session_state["last_signal_generation_mode"] = "auto"
        st.session_state.pop("last_requested_signal_date", None)
        run_update_and_generate_with_progress(None, observation_cash, mode="incremental")
        command_ran = True

    if st.sidebar.button("修复缺失行情", width="stretch", key="btn_repair_missing"):
        st.session_state["last_signal_generation_mode"] = "auto"
        st.session_state.pop("last_requested_signal_date", None)
        run_update_and_generate_with_progress(None, observation_cash, mode="repair_missing")
        command_ran = True

    st.sidebar.markdown("**快速查看**")
    st.sidebar.caption("只使用本地已有数据，不联网，速度快。")
    if st.sidebar.button("快速生成最新信号", width="stretch", key="btn_generate_latest"):
        with st.spinner("正在使用本地数据生成信号..."):
            st.session_state["last_signal_generation_mode"] = "auto"
            st.session_state.pop("last_requested_signal_date", None)
            logs = run_commands([["generate-signal", "--cash", f"{observation_cash:.2f}", "--use-cache", "--signal-mode", "latest_after_refresh"]])
            append_logs(logs)
            if logs and int(logs[-1]["returncode"]) != 0:
                st.error(str(logs[-1].get("stderr") or logs[-1].get("stdout") or "快速生成失败"))
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
            logs = run_commands([["generate-signal", "--signal-date", signal_date, "--cash", f"{observation_cash:.2f}", "--use-cache", "--signal-mode", "manual_selected_date"]])
            append_logs(logs)
            if logs and int(logs[-1]["returncode"]) != 0:
                st.error(str(logs[-1].get("stderr") or logs[-1].get("stdout") or "历史信号生成失败"))
            append_run_event("回看某日信号", f"使用历史信号日 {signal_date}，不自动更新行情。")
        command_ran = True

    st.sidebar.markdown("**维护**")
    st.sidebar.caption(_universe_meta_text())
    st.sidebar.warning("全量重建历史行情会重新下载全部 ETF 历史数据，可能耗时数小时，不建议日常使用。")
    if st.sidebar.button("全量重建历史行情", width="stretch", key="btn_full_refresh"):
        st.session_state["last_signal_generation_mode"] = "auto"
        st.session_state.pop("last_requested_signal_date", None)
        run_update_and_generate_with_progress(None, observation_cash, mode="full_refresh")
        command_ran = True
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
            ("当前信号来源", str(overview.get("signal_version", "V2_MODULAR"))),
            ("ML 观察模式", str(overview.get("ml_observation_status", "未启用"))),
            ("你选择的信号日", _format_cn_date(selected_date)),
            ("实际计算信号日", _format_cn_date(overview.get("actual_signal_date", overview["effective_signal_date"]))),
            ("数据截止日", _format_cn_date(overview.get("data_cutoff_date", overview["latest_data_date"]))),
            ("预计执行日", execution["date"]),
            ("当前状态", overview.get("execution_status", "N/A")),
            ("当前市场阶段", overview.get("market_phase", "N/A")),
            ("当前数据模式", overview.get("data_mode", "N/A")),
            ("本次观察资金", f"{observation_cash:.2f} 元"),
            ("最新本地数据日期", _format_cn_date(overview["latest_data_date"])),
            ("是否使用实时收盘补全", overview.get("use_realtime_close_patch", "否")),
            ("数据质量状态", overview.get("data_quality_status", "N/A")),
            ("交易使用等级", _compact_quality_status(overview)),
            ("信号文件更新时间", data.output_mtimes.get("compare_signal.csv", "未生成")),
        ]
    )

    if execution["date"] == "待确认":
        st.warning(f"预计执行日：待确认。原因：{execution['reason']}")
    st.info("执行说明：下一个交易日按开盘后流动性情况执行。建议执行时间：09:35 - 10:00。价格规则：人工限价单，参考实时盘口，不自动下单。")
    st.info("页面语义：观察表示进入候选观察池，不等于实际买入；校验通过只代表价格数据可信，不等于买入信号。")
    st.markdown("**V2 模块化信号摘要**")
    render_modular_pipeline_summary(_selected_strategy_row(data, MAIN_STRATEGY), "overview_modular", data.etf_names)
    st.markdown("**V2 模拟盘校准数据底座**")
    render_control_foundation_summary()
    st.markdown("**买入计划（实际买入） / 候选观察（非买入）**")
    st.caption("总览页只显示结论：当前无买入计划时，候选 ETF 仍只是观察对象；完整表格在“今日信号”页。")
    with st.expander("行情源调试信息", expanded=False):
        st.caption("校验通过表示行情/价格数据当前可信，可用于展示或估值；它不是买入信号，也不会改变 entry 决策。完整行情明细见当前持仓模块。")
    with st.expander("输出文件读取状态", expanded=False):
        show_dataframe_or_empty(build_output_file_status(), key="output_file_status", height=300)
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


def render_v2_native_tab(data: DashboardData, selected_date: date, observation_cash: float) -> None:
    st.subheader("日频右侧确认型 ETF 动量轮动策略")
    row = _selected_strategy_row(data, MAIN_STRATEGY)
    comparison = _load_control_output("v1_v2_comparison.csv")
    cases = _load_control_output("signal_cases.csv")
    comparison_row = _latest_control_row(comparison)
    execution = expected_execution_info(data.overview, PROJECT_ROOT)
    quality_report = _quality_report(data.overview)

    render_compact_metric_grid(
        build_v2_status_cards(row, comparison_row, cases)
        + [
            ("信号日", _format_cn_date(data.overview.get("effective_signal_date"))),
            ("预计执行日", execution["date"]),
            ("交易使用等级", _compact_quality_status(data.overview)),
            ("信号文件更新时间", data.output_mtimes.get("compare_signal.csv", "未生成")),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )
    st.info("当前主策略只有一个：日频右侧确认型 ETF 动量轮动策略。候选 ETF 表示进入观察池，不等于实际买入；买入计划只看 entry 输出的真实买入动作和建议仓位。")
    if execution["date"] == "待确认":
        st.warning(f"预计执行日：待确认。原因：{execution['reason']}")

    render_modular_pipeline_summary(row, "v2_native", data.etf_names)

    with st.expander("数据质量摘要", expanded=False):
        details = pd.DataFrame(
            [
                {"项目": "数据质量状态", "内容": quality_report.get("data_quality_status", data.overview.get("data_quality_status", "N/A"))},
                {"项目": "执行状态", "内容": quality_report.get("execution_status", data.overview.get("execution_status", "N/A"))},
                {"项目": "最新本地数据日期", "内容": _format_cn_date(data.overview.get("latest_data_date"))},
                {"项目": "QA 警告数量", "内容": quality_report.get("qa_warning_count", "N/A")},
            ]
        )
        show_dataframe_or_empty(_clean_display_frame(details), key="v2_native_quality_summary", height=220)


def render_v2_calibration_tab() -> None:
    st.subheader("校准研究")
    render_control_foundation_summary()
    with st.expander("输出文件状态", expanded=False):
        show_dataframe_or_empty(build_output_file_status(), key="calibration_output_file_status", height=320)


def render_legacy_v1_tab(data: DashboardData) -> None:
    st.subheader("历史对照 / 旧版参考")
    st.warning("该页面仅用于历史对照，不参与当前日频动量策略，不参与 risk_warning 门控，不作为当前交易建议来源。")
    comparison = _load_control_output("v1_v2_comparison.csv")
    row = _latest_control_row(comparison)
    with st.expander("旧版参考信号，仅用于历史对照", expanded=True):
        if row.empty:
            st.caption("暂无 V1/V2 对照数据。重新生成信号后会写入 v1_v2_comparison.csv。")
            return
        summary = pd.DataFrame(
            [
                {"项目": "交易日", "内容": row.get("trade_date", "未生成")},
                {"项目": "V1 选中 ETF", "内容": row.get("v1_selected_etfs", "未生成")},
                {"项目": "V2 候选 ETF", "内容": row.get("v2_candidate_etfs", "未生成")},
                {"项目": "V2 实际买入 ETF", "内容": row.get("v2_actual_buy_etfs", "无")},
                {"项目": "V1/V2 是否相同", "内容": row.get("same_as_v1", "未生成")},
                {"项目": "差异说明", "内容": row.get("difference_reason", "未生成")},
            ]
        )
        show_dataframe_or_empty(_clean_display_frame(summary), key="legacy_v1_comparison_summary", height=300)


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


ETF_STATUS_LABELS = {
    "up_to_date": "行情已是最新",
    "outdated": "需要更新",
    "failed": "更新失败",
    "success": "更新成功",
    "cached_success": "使用缓存",
    "cold_start": "首次写入缓存",
    "cold_start_deferred": "暂缓冷启动下载",
    "skipped": "已跳过",
    "ok": "行情已是最新",
}

EMPTY_REASON_TEXTS = {"", "nan", "none", "null", "nat", "n/a", "na", "<na>"}


def _clean_pool_text(value: Any, default: str = "未记录原因") -> str:
    if value in ("", None):
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return default if text.lower() in EMPTY_REASON_TEXTS else text


def _etf_status_label(value: Any) -> str:
    text = _clean_pool_text(value, "未知状态")
    return ETF_STATUS_LABELS.get(text, text)


def _etf_quality_status(row: pd.Series) -> str:
    success = str(row.get("success", "True")).lower() not in {"false", "0", "no", "否"}
    if not success:
        return "数据不可用"
    missing = pd.to_numeric(row.get("missing_count", 0), errors="coerce")
    duplicate = pd.to_numeric(row.get("duplicate_count", 0), errors="coerce")
    if (not pd.isna(missing) and float(missing) > 0) or (not pd.isna(duplicate) and float(duplicate) > 0):
        return "有质量提示"
    return "数据正常"


def build_etf_pool_view(data: DashboardData) -> pd.DataFrame:
    base = data.coverage.copy()
    if base.empty:
        base = data.rankings.copy()
    if base.empty:
        return pd.DataFrame()
    sector_records = _load_sector_records()

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
        sector_record = sector_records.get(symbol, {})
        success = str(item.get("success", "True")).lower() not in {"false", "0", "no", "否"}
        included = symbol in ranked_symbols if ranked_symbols else success
        reason = _clean_pool_text(item.get("filter_reason") or item.get("failure_reason") or item.get("error"), "")
        if included and not reason:
            reason = "纳入策略观察池"
        elif not success and not reason:
            reason = "基础数据不可用"
        elif not included and not reason:
            reason = "不在当前策略观察池"
        rows.append(
            {
                "symbol": symbol,
                "name": _clean_pool_text(item.get("name") or data.etf_names.get(symbol, "") or sector_record.get("name", ""), "名称未录入"),
                "sector": _clean_pool_text(item.get("sector") or sector_record.get("sector_l2") or sector_record.get("sector", ""), "行业未录入"),
                "theme": _clean_pool_text(item.get("theme") or sector_record.get("theme", ""), "主题未录入"),
                "risk_group": _clean_pool_text(item.get("risk_group") or sector_record.get("risk_group", ""), "风险分组未录入"),
                "status": _etf_status_label(item.get("status", "正常" if success else "异常")),
                "quality_status": _etf_quality_status(item),
                "eligible": included,
                "selected": symbol in selected_symbols,
                "reason": reason,
                "latest_date": _clean_pool_text(item.get("latest_date", item.get("end_date", "")), "未记录日期"),
            }
        )
    return pd.DataFrame(rows)


def render_universe_module(data: DashboardData) -> None:
    st.subheader("ETF 池分层")
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
    status_col = coverage["status"].astype(str) if "status" in coverage.columns else pd.Series(dtype=str)
    success_count = int((status_col == "success").sum()) if not status_col.empty else int(coverage["success"].astype(str).str.lower().isin(["true", "1"]).sum()) if "success" in coverage.columns else 0
    cached_count = int((status_col == "cached_success").sum()) if not status_col.empty else 0
    skipped_count = int((status_col == "skipped").sum()) if not status_col.empty else 0
    failed = coverage[~coverage["success"].astype(str).str.lower().isin(["true", "1"])] if "success" in coverage.columns else pd.DataFrame()
    pool_view = build_etf_pool_view(data)
    included_count = int(pool_view["eligible"].sum()) if not pool_view.empty and "eligible" in pool_view.columns else counts["ranked_count"]
    not_in_strategy_count = max(len(pool_view) - included_count, 0) if not pool_view.empty else 0
    candidate_count = int(pool_view["selected"].sum()) if not pool_view.empty and "selected" in pool_view.columns else 0

    render_compact_metric_grid(
        [
            ("全市场 ETF 数", len(pool_view) or counts["raw_total"] or "N/A"),
            ("当前策略观察池数量", included_count or "N/A"),
            ("暂不参与本策略的 ETF 数", not_in_strategy_count),
            ("今日候选池数量", candidate_count),
            ("下载成功", success_count or "N/A"),
            ("缓存可用", cached_count),
            ("跳过", skipped_count),
            ("下载失败", len(failed) if not coverage.empty else "N/A"),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )
    st.caption(
        "暂不参与本策略，不代表 ETF 不好，只表示当前未纳入本策略观察范围，或未满足基础数据、流动性、分类要求。"
        "候选池不等于买入计划，买入计划请看 entry_action 和建议仓位。"
    )

    with st.expander("ETF 池口径说明", expanded=False):
        st.markdown(
            """
- 全市场 ETF 池：系统已识别并维护行情缓存的 ETF 总范围。
- 策略观察池：当前策略允许纳入日常观察和排序的 ETF，通常已满足基础数据、流动性、分类等要求。
- 今日候选池：通过日频选前模型的候选 ETF，只表示进入候选观察，不等于买入。
- 买入计划：entry 模型给出实际买入动作且存在建议仓位的交易计划。
- 1160 只 ETF 中只有部分进入策略观察池，是因为本策略只覆盖当前定义的 A 股权益 ETF 范围，并要求数据完整度、流动性和分类可用。
            """.strip()
        )

    if not pool_view.empty:
        display_cols = [
            col
            for col in ["symbol", "name", "sector", "theme", "risk_group", "latest_date", "status", "quality_status", "reason"]
            if col in pool_view.columns
        ]
        strategy_pool = pool_view[pool_view["eligible"]].copy() if "eligible" in pool_view.columns else pool_view.copy()
        st.markdown("**当前策略观察池**")
        st.caption("这里只展示当前策略允许观察的 ETF；是否进入今日候选池由日频选前模型另行判断。")
        show_dataframe_or_empty(
            strategy_pool[display_cols],
            empty_text="当前没有 ETF 进入策略观察池。",
            key="strategy_observation_pool",
            height=500,
        )

        candidate_cols = [
            col
            for col in ["symbol", "name", "sector", "theme", "risk_group", "latest_date", "status", "quality_status", "reason"]
            if col in pool_view.columns
        ]
        candidate_pool = pool_view[pool_view["selected"]].copy() if "selected" in pool_view.columns else pd.DataFrame()
        st.markdown("**今日候选池（候选观察，不等于买入）**")
        st.caption("今日候选池只表示通过选前模型，实际买入还要看买入动作和建议仓位。")
        show_dataframe_or_empty(
            candidate_pool[candidate_cols] if not candidate_pool.empty else candidate_pool,
            empty_text="今日暂无候选 ETF；这不等于买入计划为空的唯一原因，仍需查看买入动作。",
            key="today_v2_candidate_pool",
            height=320,
        )

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
        st.markdown("**策略观察池排名 Top 20**")
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
    st.session_state.setdefault("position_cash_input", cash_default)
    st.session_state.setdefault("position_empty_checkbox", bool(current_position.get("current_empty", False)))

    add_row = st.button(
        "新增持仓",
        width="stretch",
        key="position_add_row",
        disabled=bool(st.session_state.get("position_empty_checkbox", False)),
    )
    if add_row:
        row_id = _next_position_row_id()
        st.session_state["position_rows"].append(
            {
                "id": row_id,
                "symbol": "",
                "name": "",
                "shares": 0.0,
                "average_buy_price": 0.0,
                "last_buy_date": date.today().isoformat(),
                "note": "",
            }
        )

    edited_rows: list[dict[str, Any]] = []
    quote_debug_rows: list[dict[str, Any]] = []
    editor_rows = []
    quote_map = _latest_position_quotes([_normalize_symbol(row.get("symbol")) for row in st.session_state["position_rows"] if _normalize_symbol(row.get("symbol"))])
    for row in st.session_state["position_rows"]:
        symbol = str(row.get("symbol", ""))
        normalized_symbol = _normalize_symbol(symbol)
        metrics = _position_metrics(row, quote_map.get(normalized_symbol))
        debug = metrics.get("行情调试")
        if isinstance(debug, dict) and normalized_symbol:
            quote_debug_rows.append(debug)
        editor_rows.append(
            {
                "row_id": int(row["id"]),
                "ETF代码": normalized_symbol,
                "ETF名称": etf_names.get(normalized_symbol, ""),
                "持仓份额": float(row.get("shares", 0) or 0),
                "平均买入价": float(row.get("average_buy_price", 0) or 0),
                "持仓成本": metrics["持仓成本"],
                "当前价格": metrics["当前价格"] if metrics["当前价格"] > 0 else None,
                "当前市值": metrics["当前市值"] if metrics["当前市值"] > 0 else None,
                "浮动盈亏": metrics["浮动盈亏"],
                "浮动盈亏率": metrics["浮动盈亏率"],
                "报价日期": metrics["报价日期"],
                "报价时间": metrics["报价时间"],
                "价格来源": metrics["价格来源"],
                "价格状态": metrics["价格状态"],
                "价格说明": metrics["价格说明"],
                "最近买入日期": _text_or_default(row.get("last_buy_date"), date.today().isoformat()),
                "备注": _text_or_default(row.get("note")),
                "删除": False,
            }
        )

    with st.form("position_editor_form", clear_on_submit=False):
        cash_col, empty_col = st.columns([1.2, 0.8], vertical_alignment="bottom")
        with cash_col:
            cash = float(
                st.number_input(
                    "可用现金",
                    min_value=0.0,
                    value=float(st.session_state.get("position_cash_input", cash_default)),
                    step=100.0,
                    key="position_cash_input",
                )
            )
        with empty_col:
            current_empty = st.checkbox(
                "当前空仓",
                value=bool(st.session_state.get("position_empty_checkbox", current_position.get("current_empty", False))),
                key="position_empty_checkbox",
            )

        edited_frame = pd.DataFrame(editor_rows)
        if current_empty:
            st.caption("已选择当前空仓，保存后系统只会生成买入计划，不生成卖出计划。")
        else:
            edited_frame = st.data_editor(
                pd.DataFrame(editor_rows),
                hide_index=True,
                width="stretch",
                height=min(360, max(180, 42 * (len(editor_rows) + 1))),
                num_rows="dynamic",
                column_order=["ETF代码", "ETF名称", "持仓份额", "平均买入价", "持仓成本", "当前价格", "报价日期", "报价时间", "价格来源", "价格状态", "价格说明", "当前市值", "浮动盈亏", "浮动盈亏率", "最近买入日期", "备注", "删除"],
                disabled=["ETF名称", "持仓成本", "当前价格", "报价日期", "报价时间", "价格来源", "价格状态", "价格说明", "当前市值", "浮动盈亏", "浮动盈亏率"],
                key="position_editor",
                column_config={
                    "row_id": None,
                    "ETF代码": st.column_config.TextColumn("ETF代码", width="small", help="填写 6 位 ETF 代码"),
                    "ETF名称": st.column_config.TextColumn("ETF名称", width="medium"),
                    "持仓份额": st.column_config.NumberColumn("持仓份额", min_value=0.0, step=100.0, format="%.0f", width="small"),
                    "平均买入价": st.column_config.NumberColumn("平均买入价", min_value=0.0, step=0.001, format="%.3f", width="small"),
                    "持仓成本": st.column_config.NumberColumn("持仓成本", format="%.2f", width="small"),
                    "当前价格": st.column_config.NumberColumn("当前价格", format="%.3f", width="small"),
                    "当前市值": st.column_config.NumberColumn("当前市值", format="%.2f", width="small"),
                    "浮动盈亏": st.column_config.NumberColumn("浮动盈亏", format="%.2f", width="small"),
                    "浮动盈亏率": st.column_config.NumberColumn("浮动盈亏率", format="%.2%", width="small"),
                    "报价日期": st.column_config.TextColumn("报价日期", width="small"),
                    "报价时间": st.column_config.TextColumn("报价时间", width="small"),
                    "价格来源": st.column_config.TextColumn("价格来源", width="small"),
                    "价格状态": st.column_config.TextColumn("价格状态", width="medium"),
                    "价格说明": st.column_config.TextColumn("价格说明", width="large"),
                    "最近买入日期": st.column_config.TextColumn("最近买入日期", width="small", help="可留空，保存时默认今天"),
                    "备注": st.column_config.TextColumn("备注", width="medium"),
                    "删除": st.column_config.CheckboxColumn("删除", help="勾选后保存即删除该行", width="small"),
                },
            )
        save_position = st.form_submit_button("保存持仓", width="stretch", type="primary")

    with st.expander("行情源调试信息", expanded=False):
        st.caption("校验通过表示行情/价格数据当前可信，可用于展示或估值；它不是买入信号，也不会改变 entry 决策。")
        show_dataframe_or_empty(pd.DataFrame(quote_debug_rows), empty_text="暂无行情调试信息。", key="position_quote_debug", height=260)

    if save_position:
        if current_empty:
            _save_current_position(cash, True, [])
            st.success("当前持仓已保存为空仓。请重新生成信号以得到买入计划。")
            st.session_state["position_rows"] = [{"id": _next_position_row_id(), "symbol": "", "name": "", "shares": 0.0, "average_buy_price": 0.0, "last_buy_date": date.today().isoformat(), "note": ""}]
            st.session_state["position_original_rows"] = {}
            return cash

        deleted_any = False
        for _, row in edited_frame.iterrows():
            if bool(row.get("删除", False)):
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
                    "shares": _float_or_zero(row.get("持仓份额")),
                    "average_buy_price": _float_or_zero(row.get("平均买入价")),
                    "last_buy_date": _text_or_default(row.get("最近买入日期"), date.today().isoformat()),
                    "note": _text_or_default(row.get("备注")),
                }
            )
        if deleted_any:
            st.info("已删除勾选的持仓行，并将在本次保存中写入配置文件。")
        st.session_state["position_rows"] = edited_rows or [{"id": _next_position_row_id(), "symbol": "", "name": "", "shares": 0.0, "average_buy_price": 0.0, "last_buy_date": date.today().isoformat(), "note": ""}]

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
        _append_position_trades(holdings)
        merged_holdings = _merge_holdings_by_symbol(holdings)
        _save_current_position(cash, False, merged_holdings)
        st.session_state["position_rows"] = _position_rows_from_file(etf_names)
        st.session_state["position_original_rows"] = {int(row["id"]): dict(row) for row in st.session_state["position_rows"] if row.get("symbol")}
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


def render_modular_pipeline_summary(row: pd.Series, key_prefix: str, etf_names: dict[str, str] | None = None) -> None:
    fields = [
        "signal_version",
        "modular_market_state",
        "modular_selected_sectors",
        "modular_candidate_etfs",
        "modular_buy_actions",
        "modular_ml_observation_status",
        "modular_ml_entry_advice",
        "modular_exit_actions",
        "modular_learning_advice",
        "modular_pipeline_status",
        "modular_pipeline_warnings",
    ]
    if row.empty or not any(str(row.get(key, "")).strip() for key in fields):
        return

    cases = _load_control_output("signal_cases.csv")
    lookup = build_v2_etf_lookup(etf_names=etf_names, cases=cases)
    ml_status = _clean_display_value(row.get("ml_observation_status", row.get("v2_ml_observation_status", row.get("modular_ml_observation_status"))), "未启用")
    st.info(f"{ml_status}。ML 建议仅供观察，不自动修改交易参数。")
    st.markdown("**入选板块**")
    sector_items = _split_signal_items(row.get("modular_selected_sectors", row.get("v2_selected_sectors", "")))
    if sector_items:
        st.markdown("  ".join(f"`{item}`" for item in sector_items))
    else:
        st.caption("暂无入选板块。")

    st.markdown("**候选 ETF**")
    st.caption("候选表中的 ML 观察字段仅供观察，不自动修改交易参数。")
    candidate_table = build_v2_candidate_table(row, cases=cases, etf_names=etf_names, lookup=lookup)
    display_candidate = candidate_table.drop(columns=["完整原因", "名称来源"], errors="ignore")
    show_dataframe_or_empty(
        display_candidate,
        empty_text="暂无 V2 候选 ETF。",
        key=f"{key_prefix}_v2_candidate_table",
        height=360,
    )
    if not candidate_table.empty and "完整原因" in candidate_table.columns:
        with st.expander("展开查看候选 ETF 完整原因", expanded=False):
            show_dataframe_or_empty(
                candidate_table[["ETF代码", "ETF名称", "完整原因", "名称来源"]],
                key=f"{key_prefix}_v2_candidate_reason_table",
                height=360,
            )

    st.markdown("**买入计划**")
    st.caption("ML 观察建议只作为旁路提示，不改变 buy_action、不改变建议仓位，也不绕过 RiskGate。")
    actual_buy = _split_signal_items(row.get("v2_actual_buy_etfs", ""))
    actual_buy_symbols = {_normal_etf_code(_split_code_and_text(item)[0] or item) for item in actual_buy}
    buy_actions = build_v2_action_table(
        row.get("modular_buy_actions", row.get("v2_entry_actions", "")),
        etf_names=etf_names,
        action_label="买入动作",
        lookup=lookup,
        actual_buy_symbols=actual_buy_symbols,
    )
    if actual_buy:
        st.success(f"当前 V2 有实际买入计划：{'、'.join(actual_buy)}")
    else:
        st.info("当前 V2 无实际买入计划；候选 ETF 仍只是观察对象。")
    show_dataframe_or_empty(
        buy_actions.drop(columns=["完整原因", "名称来源"], errors="ignore"),
        empty_text="暂无买入动作。",
        key=f"{key_prefix}_v2_buy_action_table",
        height=300,
    )
    if not buy_actions.empty and "完整原因" in buy_actions.columns:
        with st.expander("展开查看买入计划名称匹配与完整原因", expanded=False):
            show_dataframe_or_empty(
                buy_actions[["ETF代码", "ETF名称", "买入动作", "完整原因", "名称来源"]],
                key=f"{key_prefix}_v2_buy_reason_table",
                height=320,
            )

    st.markdown("**退出建议**")
    exit_actions = build_v2_action_table(row.get("modular_exit_actions", ""), etf_names=etf_names, action_label="退出动作", lookup=lookup)
    show_dataframe_or_empty(
        exit_actions.drop(columns=["完整原因", "名称来源"], errors="ignore"),
        empty_text="暂无退出建议。",
        key=f"{key_prefix}_v2_exit_action_table",
        height=260,
    )

    st.markdown("**学习建议**")
    st.write(_clean_display_value(row.get("modular_learning_advice"), "暂无学习建议。"))
    warnings = _clean_display_value(row.get("modular_pipeline_warnings"), "")
    if warnings:
        st.caption(f"模块状态：{_clean_display_value(row.get('modular_pipeline_status'), '未生成')}；{warnings}")


@st.cache_data(ttl=120, show_spinner=False)
def _load_control_output(filename: str) -> pd.DataFrame:
    path = OUTPUT_DIR / filename
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame([{"error": f"{filename} 读取失败: {exc}"}])


def _empty_signal_text(value: Any) -> bool:
    text = str(value or "").strip()
    return text in {"", "无", "空仓", "N/A", "鏃?", "绌轰粨"} or text.lower() in EMPTY_REASON_TEXTS


def _latest_control_row(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=object)
    if "trade_date" in frame.columns:
        frame = frame.sort_values("trade_date")
    return frame.iloc[-1]


def _regime_display(value: Any) -> str:
    text = str(value or "").strip()
    if text == "pre_20240924":
        return "2024-09-24 前"
    if text == "post_20240924":
        return "2024-09-24 后"
    return text or "未生成"


def _count_matching(frame: pd.DataFrame, column: str, values: set[str]) -> int:
    if frame.empty or column not in frame.columns:
        return 0
    lowered = {value.lower() for value in values}
    return int(frame[column].astype(str).str.strip().str.lower().isin(lowered).sum())


def _count_not_matching(frame: pd.DataFrame, column: str, values: set[str]) -> int:
    if frame.empty or column not in frame.columns:
        return 0
    lowered = {value.lower() for value in values}
    series = frame[column].astype(str).str.strip().str.lower()
    return int((~series.isin(lowered)).sum())


def _sum_int_column(frame: pd.DataFrame, column: str, default: int = 0) -> int:
    if frame.empty or column not in frame.columns:
        return default
    values = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    return int(values.sum())


def render_control_foundation_summary() -> None:
    comparison = _load_control_output("v1_v2_comparison.csv")
    cases = _load_control_output("signal_cases.csv")
    review = _load_control_output("signal_case_review.csv")
    row = _latest_control_row(comparison)
    if row.empty and cases.empty:
        st.caption("总控校准数据底座尚未生成。重新生成信号后会写入 signal_cases.csv 和 v1_v2_comparison.csv。")
        return

    actual_buy = row.get("v2_actual_buy_etfs", "") if not row.empty else ""
    has_buy_plan = not _empty_signal_text(actual_buy)
    if has_buy_plan:
        st.success(f"当前 V2 有实际买入计划：{actual_buy}")
    else:
        reason = row.get("v2_no_buy_reason", "暂无原因汇总") if not row.empty else "暂无原因汇总"
        st.info(f"当前无买入计划。主要原因：{reason or '暂无原因汇总'}")

    regime = ""
    if not cases.empty and "post_924_regime" in cases.columns:
        regime = _regime_display(cases.iloc[-1].get("post_924_regime"))
    elif not row.empty and row.get("trade_date"):
        regime = "2024-09-24 后" if str(row.get("trade_date")) >= "2024-09-24" else "2024-09-24 前"

    latest_cases = cases
    if not cases.empty and "trade_date" in cases.columns:
        latest_trade_date = str(cases["trade_date"].max())
        latest_cases = cases[cases["trade_date"].astype(str) == latest_trade_date]
    observation_count = _count_matching(latest_cases, "entry_action", {"观察", "瑙傚療", "watch"})
    hindsight_sample_count = _count_not_matching(cases, "hindsight_label", {"", "样本不足"})
    missed_count = _count_matching(cases, "hindsight_label", {"可能错过机会"})
    correct_count = _count_matching(cases, "hindsight_label", {"观察正确"})
    insufficient_count = _count_matching(cases, "hindsight_label", {"样本不足"})
    if not review.empty:
        missed_count = _sum_int_column(review, "missed_opportunity_count", missed_count)
        correct_count = _sum_int_column(review, "correct_observation_count", correct_count)
        insufficient_count = _sum_int_column(review, "insufficient_sample_count", insufficient_count)

    comparison_summary = pd.DataFrame(
        [
            {"项目": "样本区间", "内容": regime or "未生成"},
            {"项目": "V1 选中 ETF", "内容": row.get("v1_selected_etfs", "未生成") if not row.empty else "未生成"},
            {"项目": "V2 候选 ETF", "内容": row.get("v2_candidate_etfs", "未生成") if not row.empty else "未生成"},
            {"项目": "V2 实际买入 ETF", "内容": actual_buy or "无"},
            {"项目": "V1/V2 是否相同", "内容": row.get("same_as_v1", "未生成") if not row.empty else "未生成"},
            {"项目": "差异说明", "内容": row.get("difference_reason", "未生成") if not row.empty else "未生成"},
        ]
    )
    st.markdown("**V1/V2 对照摘要**")
    show_dataframe_or_empty(_clean_display_frame(comparison_summary), key="control_foundation_comparison_summary", height=240)

    st.markdown("**signal_cases 概览**")
    case_overview = pd.DataFrame(
        [
            {"项目": "signal_cases 行数", "内容": len(cases)},
            {"项目": "最近 V2 观察信号数量", "内容": observation_count},
            {"项目": "后验有效样本数量", "内容": hindsight_sample_count},
            {"项目": "样本区间", "内容": regime or "未生成"},
        ]
    )
    show_dataframe_or_empty(_clean_display_frame(case_overview), key="control_foundation_cases_overview", height=220)

    st.markdown("**signal_case_review 概览**")
    review_overview = pd.DataFrame(
        [
            {"项目": "review 行数", "内容": len(review)},
            {"项目": "可能错过机会", "内容": missed_count},
            {"项目": "观察正确", "内容": correct_count},
            {"项目": "样本不足", "内容": insufficient_count},
        ]
    )
    show_dataframe_or_empty(_clean_display_frame(review_overview), key="control_foundation_review_overview", height=220)

    hindsight_summary = pd.DataFrame(
        [
            {"项目": "最近 V2 观察信号数量", "内容": observation_count},
            {"项目": "已有后验样本数量", "内容": hindsight_sample_count},
            {"项目": "可能错过机会数量", "内容": missed_count},
            {"项目": "观察正确数量", "内容": correct_count},
            {"项目": "样本不足数量", "内容": insufficient_count},
            {"项目": "后验统计说明", "内容": "只用于 V2 校准研究，不参与当日交易决策。"},
        ]
    )
    st.markdown("**后验统计区域**")
    show_dataframe_or_empty(_clean_display_frame(hindsight_summary), key="control_foundation_hindsight_summary", height=260)
    st.caption("后验统计只用于 V2 校准研究，不参与当日交易决策。")


def render_strategy_explanation(strategy_name: str) -> None:
    st.info(
        "当前策略属于右侧确认型趋势跟随策略，不预测启动点，也不做左侧埋伏。"
        "系统通过日 K 动量、趋势形态、成交活跃度和相对强弱确认 ETF 已经走强后，再给出交易建议。"
        "策略通过日频更新进行纠错和风控，降低买晚、买错后的回撤风险。"
    )
    st.markdown(
        "核心逻辑：日频动量选方向；市场状态控仓位；主题确认提胜率；追高约束管执行；趋势失效及时退出。"
    )


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
    st.markdown("**第 5 阶段模块化决策流**")
    render_modular_pipeline_summary(row, key_prefix, etf_names)

    st.markdown("**目标组合**")
    show_dataframe_or_empty(parse_target_table(row, etf_names), key=f"{key_prefix}_target_table")

    rank_table = parse_rank_table(row)
    st.markdown("**动量排名表**")
    show_dataframe_or_empty(rank_table, empty_text="暂无动量排名。", key=f"{key_prefix}_rank_table")

    st.info("本页面用于模拟盘内测。观察表示进入候选观察池，不等于实际买入；买入计划只展示可执行买入动作。")
    raw_buy_table = parse_buy_table(row)
    actual_buy_table = _actual_buy_plan_frame(raw_buy_table)
    st.markdown("**买入计划（实际买入）**")
    st.caption("仅当 entry 输出真实买入动作且建议仓位大于 0 时才显示。校验通过只代表价格数据可信，不等于买入信号。")
    show_dataframe_or_empty(actual_buy_table, empty_text="无实际买入计划。", key=f"{key_prefix}_buy_table")
    st.markdown("**候选观察（非买入）**")
    st.caption("这里展示进入候选池但 entry 仍为观察/等待/禁止的对象，用于复盘，不代表今日买入。")
    candidate_table = raw_buy_table if actual_buy_table.empty else raw_buy_table.drop(actual_buy_table.index, errors="ignore")
    show_dataframe_or_empty(candidate_table, empty_text="无候选观察记录。", key=f"{key_prefix}_candidate_table")

    st.markdown("**盘中买入执行计划**")
    st.caption("如果没有实时行情，以下价格基于最新完整交易日收盘价、近期波动率、ATR 和均线生成，作为下一交易日参考买入价。")
    show_dataframe_or_empty(parse_intraday_execution_table(row), empty_text="无盘中买入执行计划。", key=f"{key_prefix}_intraday_execution_table", height=420)
    st.info("若触发失效条件，今日三档买入价全部取消，不再新增买入。")

    st.markdown("**持仓卖出执行计划**")
    st.caption("三档卖出价是用户实际参考挂单价；风险卖出时基于当前参考价生成。止盈价用于上涨后分批兑现，风控触发价只是判断线。")
    sell_execution_table = parse_sell_execution_table(row)
    if sell_execution_table.empty and str(row.get("current_empty", "")).strip() in {"是", "True", "true", "1"}:
        st.info("当前为空仓，暂无卖出计划。系统只生成买入计划。")
    elif sell_execution_table.empty:
        st.caption("暂无持仓卖出执行计划。")
    else:
        show_dataframe_or_empty(sell_execution_table, key=f"{key_prefix}_sell_execution_table", height=420)

    st.markdown("**资金不足提示**")
    show_dataframe_or_empty(parse_skip_table(row), empty_text="无资金不足提示。", key=f"{key_prefix}_skip_table")

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


def render_advanced_diagnostics(data: DashboardData | None = None, key_prefix: str = "diag") -> None:
    with st.expander("高级诊断信息", expanded=False):
        st.caption("以下内容用于排查本地面板、命令输出和环境问题，普通使用时无需查看。")
        cols = st.columns(3)
        if cols[0].button("打开输出文件夹", width="stretch", key=f"{key_prefix}_open_output"):
            open_local_path(OUTPUT_DIR)
        if cols[1].button("打开持仓配置文件", width="stretch", key=f"{key_prefix}_open_position"):
            open_local_path(CURRENT_POSITION)
        if cols[2].button("打开说明文档", width="stretch", key=f"{key_prefix}_open_readme"):
            open_local_path(README)

        st.markdown("**运行环境**")
        st.code(str(PYTHON_EXE), language="text")
        if data is not None:
            st.markdown("**输出文件更新时间**")
            st.write(data.output_mtimes)
            if not data.signals.empty:
                debug_cols = [
                    col
                    for col in ["strategy_name", "sell_plan", "sell_execution_plan"]
                    if col in data.signals.columns
                ]
                if debug_cols:
                    st.markdown("**内部卖出计划诊断**")
                    st.caption("以下为内部字段，仅用于排查，不在普通页面展示。")
                    st.dataframe(data.signals[debug_cols], hide_index=True, width="stretch", height=260)
            if not data.coverage.empty:
                status_col = data.coverage["status"].astype(str) if "status" in data.coverage.columns else pd.Series(dtype=str)
                st.markdown("**最近行情更新状态**")
                st.write(
                    {
                        "success": int((status_col == "success").sum()),
                        "cached_success": int((status_col == "cached_success").sum()),
                        "skipped": int((status_col == "skipped").sum()),
                        "failed": int((status_col == "failed").sum()),
                    }
                )
                problem = data.coverage[data.coverage.get("error", data.coverage.get("failure_reason", "")).astype(str).str.strip() != ""] if "failure_reason" in data.coverage.columns or "error" in data.coverage.columns else pd.DataFrame()
                if not problem.empty:
                    cols = [col for col in ["symbol", "name", "status", "latest_date", "source", "error", "failure_reason", "elapsed_seconds"] if col in problem.columns]
                    st.dataframe(localize_columns(problem[cols].head(50)), hide_index=True, width="stretch", height=260)

        update_failures = OUTPUT_DIR / "update_failures.csv"
        st.markdown("**最近更新失败 / 缓存回退明细**")
        if update_failures.exists():
            try:
                failures = pd.read_csv(update_failures, dtype={"symbol": str}).fillna("")
            except Exception as exc:  # noqa: BLE001
                failures = pd.DataFrame([{"error": f"update_failures.csv 读取失败: {exc}"}])
            cols = [col for col in ["symbol", "name", "status", "latest_date", "source", "error", "failure_reason", "elapsed_seconds"] if col in failures.columns]
            show_dataframe_or_empty(failures[cols] if cols else failures, key=f"{key_prefix}_update_failures", height=260)
        else:
            st.caption("未找到 update_failures.csv")

        render_error = st.session_state.get("last_render_error")
        if render_error:
            st.markdown("**最近一次页面异常详情**")
            st.code(str(render_error), language="text")

        compare_txt = OUTPUT_DIR / "compare_signal.txt"
        st.markdown("**原始 compare_signal.txt**")
        if compare_txt.exists():
            st.text_area("compare_signal.txt", compare_txt.read_text(encoding="utf-8", errors="ignore"), height=240, key=f"{key_prefix}_compare_txt")
        else:
            st.caption("未找到 compare_signal.txt")


def _dashboard_output_signature(project_root: Path) -> tuple[tuple[str, float, int], ...]:
    output = project_root / "output"
    filenames = [
        "compare_signal.csv",
        "data_coverage_report.csv",
        "etf_universe_raw.csv",
        "etf_universe_snapshot.csv",
        "qa_report.json",
        "risk_gate.json",
        "risk_warning_next_day.csv",
        "risk_learning_context.csv",
        "strategy_review.csv",
    ]
    signature = []
    for filename in filenames:
        path = output / filename
        if path.exists():
            stat = path.stat()
            signature.append((filename, stat.st_mtime, stat.st_size))
        else:
            signature.append((filename, 0.0, 0))
    return tuple(signature)


@st.cache_data(ttl=120, show_spinner=False)
def _load_dashboard_data_cached(project_root_text: str, signature: tuple[tuple[str, float, int], ...]) -> DashboardData:
    return load_dashboard_data(Path(project_root_text))


def _v21_output_signature(project_root: Path) -> tuple[tuple[str, float, int], ...]:
    output = project_root / "output"
    signature: list[tuple[str, float, int]] = []
    for filename in V21_FRONTEND_JSON_FILES:
        path = output / filename
        if path.exists():
            stat = path.stat()
            signature.append((filename, stat.st_mtime, stat.st_size))
        else:
            signature.append((filename, 0.0, 0))
    return tuple(signature)


def _read_v21_json_file(output_dir: Path, filename: str) -> Any:
    path = output_dir / filename
    if not path.exists():
        return [] if filename in {"portfolio_snapshot.json", "order_intent.json", "learning_summary.json", "historical_ml_summary.json"} else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": f"{filename} 读取失败，页面已降级显示。"}


@st.cache_data(ttl=30, show_spinner=False)
def _load_v21_snapshots_cached(project_root_text: str, signature: tuple[tuple[str, float, int], ...]) -> dict[str, Any]:
    output_dir = Path(project_root_text) / "output"
    payload = load_v21_frontend_snapshots(output_dir)
    payload["missing_files"] = [filename for filename, mtime, _ in signature if mtime <= 0]
    return payload


def load_v21_frontend_snapshots(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Load V2.1 frontend snapshots only; never generate or refresh signals."""

    return {
        "daily_decision": _read_v21_json_file(output_dir, "daily_decision_snapshot.json"),
        "risk_gate": _read_v21_json_file(output_dir, "risk_gate_snapshot.json"),
        "portfolio": _read_v21_json_file(output_dir, "portfolio_snapshot.json"),
        "order_intent": _read_v21_json_file(output_dir, "order_intent.json"),
        "learning": _read_v21_json_file(output_dir, "learning_summary.json"),
        "historical_ml": _read_v21_json_file(output_dir, "historical_ml_summary.json"),
        "status": _read_v21_json_file(output_dir, "v21_backend_status.json"),
        "missing_files": [filename for filename in V21_FRONTEND_JSON_FILES if not (output_dir / filename).exists()],
    }


def _v21_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        return [dict(value)] if value else []
    return []


def _v21_display_value(value: Any, default: str = "暂无") -> str:
    return _clean_display_value(value, default)


def _v21_join(value: Any, default: str = "暂无") -> str:
    if isinstance(value, list):
        cleaned = [_v21_display_value(item, default="") for item in value]
        cleaned = [item for item in cleaned if item]
        return "、".join(cleaned) if cleaned else default
    if isinstance(value, dict):
        parts = [f"{_v21_field_label(key)}：{_v21_display_value(item, default='暂无')}" for key, item in value.items()]
        return "；".join(parts) if parts else default
    return _v21_display_value(value, default=default)


def _v21_field_label(value: Any) -> str:
    text = str(value or "").strip()
    labels = {
        "fallback_reason": "降级原因",
        "risk_block_reason": "风险阻断原因",
        "manual_takeover_required": "需要人工接管",
        "freeze_entry": "冻结买入",
        "execution_mode": "执行模式",
        "requires_manual_confirm": "需要人工确认",
        "risk_check_passed": "风险检查通过",
        "source_signal": "来源信号",
    }
    return labels.get(text, _clean_display_value(text, "未命名字段"))


def _first_present(*values: Any) -> Any:
    for value in values:
        if value in ("", None):
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        return value
    return None


def _v21_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _v21_frame(records: Sequence[Mapping[str, Any]], columns: dict[str, str]) -> pd.DataFrame:
    rows = [{label: _v21_join(record.get(key)) for key, label in columns.items()} for record in records]
    return pd.DataFrame(rows, columns=list(columns.values()))


def _v21_count_actual_exit(decision: Mapping[str, Any]) -> int:
    return sum(1 for item in decision.get("exit_actions", []) or [] if isinstance(item, Mapping) and _v21_bool(item.get("actual_exit")))


def _v21_has_exit_priority(decision: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(part or "")
        for part in (
            decision.get("fallback_reason"),
            decision.get("explain"),
            json.dumps(decision.get("exit_actions", []), ensure_ascii=False),
        )
    )
    return any(token in text for token in ("退出风险优先", "exit 出现清仓", "清仓", "风险退出"))


def build_v21_frontend_status(snapshots: Mapping[str, Any]) -> dict[str, Any]:
    decision = snapshots.get("daily_decision") if isinstance(snapshots.get("daily_decision"), Mapping) else {}
    risk = snapshots.get("risk_gate") if isinstance(snapshots.get("risk_gate"), Mapping) else {}
    status = snapshots.get("status") if isinstance(snapshots.get("status"), Mapping) else {}
    return {
        "signal_version": _v21_display_value(_first_present(decision.get("signal_version"), status.get("signal_version"))),
        "trade_date": format_trade_date(_first_present(decision.get("trade_date"), risk.get("trade_date"), status.get("trade_date"))) or _v21_display_value(_first_present(decision.get("trade_date"), risk.get("trade_date"), status.get("trade_date"))),
        "market_state": _v21_display_value(decision.get("market_state")),
        "risk_level": _v21_display_value(_first_present(decision.get("risk_level"), risk.get("risk_level"))),
        "risk_score": _v21_display_value(_first_present(decision.get("risk_score"), risk.get("risk_score"))),
        "allow_entry": _v21_display_value(decision.get("allow_entry")),
        "freeze_entry": _v21_display_value(_first_present(decision.get("freeze_entry"), risk.get("freeze_entry"))),
        "manual_takeover_required": _v21_display_value(_first_present(decision.get("manual_takeover_required"), risk.get("manual_takeover_required"))),
        "ml_observation_status": _v21_display_value(decision.get("ml_observation_status"), "ML 观察模式未启用"),
        "candidate_count": len(decision.get("candidate_etfs", []) or []),
        "actual_buy_count": len(decision.get("actual_buy_etfs", []) or []),
        "exit_count": _v21_count_actual_exit(decision),
        "generated_at": format_datetime_shanghai(_first_present(status.get("generated_at"), decision.get("generated_at"))) or _v21_display_value(_first_present(status.get("generated_at"), decision.get("generated_at"))),
        "fallback_reason": _v21_display_value(_first_present(decision.get("fallback_reason"), status.get("fallback_reason")), "暂无降级说明。"),
    }


def _v21_order_frame(records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    return _v21_frame(
        records,
        {
            "etf_code": "ETF代码",
            "etf_name": "ETF名称",
            "action": "动作",
            "side": "方向",
            "target_weight": "目标权重",
            "current_weight": "当前权重",
            "delta_weight": "变化权重",
            "estimated_price": "估算价格",
            "estimated_amount": "估算金额",
            "execution_mode": "执行模式",
            "requires_manual_confirm": "需要人工确认",
            "risk_check_passed": "风险检查通过",
            "risk_block_reason": "风险阻断原因",
            "source_signal": "来源信号",
            "explain": "中文解释",
        },
    )


V21_TASK_STATUS_LABELS = {
    "pending": "等待执行",
    "running": "正在执行",
    "success": "执行成功",
    "failed": "执行失败",
    "cancelled": "已取消",
}
V21_TASK_TERMINAL_STATUSES = {"success", "failed", "cancelled"}

V21_ACTION_LABELS = {
    "refresh_market_data": "刷新行情数据",
    "run_daily_signal": "重新生成今日信号",
    "rebuild_v21_snapshot": "重建 V2.1 总控快照",
    "run_data_health_check": "运行数据健康检查",
    "run_historical_replay": "运行历史回放",
    "generate_daily_samples": "生成每日样本",
    "generate_entry_samples": "生成 entry 候选样本",
    "auto_label_samples": "自动打标签",
    "generate_failure_samples": "生成失败样本",
    "generate_missed_opportunity_samples": "生成错过样本",
    "generate_manual_review_queue": "生成手工复核队列",
    "export_manual_review_file": "导出人工复核表",
    "import_manual_labels": "导入人工复核表",
    "prefill_manual_review_labels": "自动预填人工复核",
    "adopt_high_confidence_manual_labels": "一键采纳高置信标注",
    "adopt_medium_confidence_manual_labels": "一键采纳中置信标注",
    "export_low_confidence_review_file": "导出低置信复核表",
    "export_pending_manual_review_file": "导出待人工复核表",
    "export_missed_winner_review_file": "导出 missed_big_winner 复核表",
    "import_manual_corrections": "导入人工修正表",
    "generate_entry_calibration_report": "生成 entry 校准报告",
    "generate_parameter_suggestions": "生成参数建议",
    "run_overfit_check": "运行过拟合检查",
    "sync_qmt_account": "同步 QMT 资金",
    "sync_qmt_positions": "同步 QMT 持仓",
    "sync_qmt_orders": "同步 QMT 委托",
    "sync_qmt_trades": "同步 QMT 成交",
}


def _v21_clear_snapshot_cache() -> None:
    _load_v21_snapshots_cached.clear()


def _v21_store_action_response(label: str, response: Mapping[str, Any], *, open_dialog: bool = False) -> None:
    st.session_state["v21_last_action_response"] = {"label": label, **dict(response)}
    task_id = str(response.get("task_id") or "")
    if task_id:
        st.session_state["v21_active_task_id"] = task_id
        st.session_state["v21_task_dialog_open"] = bool(open_dialog)
        st.session_state["v21_task_dialog_label"] = label


def _v21_run_action(
    label: str,
    action_func: Any,
    *args: Any,
    clear_snapshots: bool = False,
    open_dialog: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    response = action_func(*args, **kwargs)
    _v21_store_action_response(label, response, open_dialog=open_dialog)
    if clear_snapshots:
        _v21_clear_snapshot_cache()
    return dict(response)


def _v21_current_task(task_id: str) -> dict[str, Any]:
    response = action_api.get_task(task_id)
    data = response.get("data") if isinstance(response, Mapping) else {}
    task = data.get("task") if isinstance(data, Mapping) else {}
    return dict(task) if isinstance(task, Mapping) else {}


def _v21_task_progress_value(task: Mapping[str, Any]) -> int:
    try:
        return max(0, min(100, int(float(task.get("progress", 0) or 0))))
    except (TypeError, ValueError):
        return 0


def _v21_task_status_text(task: Mapping[str, Any]) -> str:
    status = str(task.get("status") or "pending")
    detail = _v21_display_value(task.get("status_detail"), "")
    label = V21_TASK_STATUS_LABELS.get(status, _v21_display_value(status))
    return f"{label} / {detail}" if detail else label


def _v21_task_dialog_summary(task: Mapping[str, Any]) -> list[tuple[str, Any]]:
    return [
        ("任务", V21_ACTION_LABELS.get(str(task.get("action_name") or ""), _v21_display_value(task.get("action_name"), ""))),
        ("状态", _v21_task_status_text(task)),
        ("耗时", _v21_display_value(task.get("elapsed_seconds"), 0)),
        ("结果数量", _v21_display_value(_v21_task_result_count(task), "")),
        ("输出路径", _v21_display_value(_v21_task_summary_value(task, "output_path", task.get("result_file", "")), "")),
        ("下一步", _v21_display_value(_v21_task_summary_value(task, "next_step", _v21_task_summary_value(task, "suggested_next_step", "")), "")),
    ]


def _v21_close_task_dialog_without_app_rerun() -> None:
    st.session_state["v21_task_dialog_open"] = False
    st.session_state["v21_active_task_id"] = ""
    st.session_state["v21_task_dialog_label"] = ""
    st_html(
        """
        <script>
        const doc = window.parent.document;
        function visible(element) {
          return Boolean(element && (element.offsetWidth || element.offsetHeight || element.getClientRects().length));
        }
        function closeDialog(attempt) {
          const buttons = Array.from(doc.querySelectorAll('button'));
          const closeButton = buttons.find((button) => (
            visible(button)
            && (button.getAttribute('aria-label') === 'Close' || button.getAttribute('title') === 'Close')
          ));
          if (closeButton) {
            closeButton.click();
            return;
          }
          if (attempt < 20) {
            window.setTimeout(() => closeDialog(attempt + 1), 100);
          }
        }
        closeDialog(0);
        </script>
        """,
        height=0,
        width=0,
    )
    st.stop()


@st.fragment(run_every=1)
def _v21_task_status_dialog_body() -> None:
    task_id = str(st.session_state.get("v21_active_task_id") or "")
    label = _v21_display_value(st.session_state.get("v21_task_dialog_label"), "")
    task = _v21_current_task(task_id) if task_id else {}
    if not task:
        st.warning("没有找到当前任务记录。")
        if st.button("确认关闭", key="v21_task_dialog_close_missing", width="stretch"):
            _v21_close_task_dialog_without_app_rerun()
        return

    status = str(task.get("status") or "pending")
    progress_value = _v21_task_progress_value(task)
    title = label or V21_ACTION_LABELS.get(str(task.get("action_name") or ""), "后台任务")
    st.write(f"**{title}**")
    st.caption(f"task_id: {task_id}")
    st.progress(progress_value / 100, text=f"{_v21_task_status_text(task)} · {progress_value}%")
    message = _v21_display_value(task.get("message"), "")
    if status == "failed":
        st.error(_v21_display_value(task.get("error") or message, "任务失败。"))
    elif status in V21_TASK_TERMINAL_STATUSES:
        st.success(message or "任务已完成。")
    else:
        st.info(message or "任务正在后台执行。")

    cols = st.columns(2)
    for index, (name, value) in enumerate(_v21_task_dialog_summary(task)):
        cols[index % 2].write(f"**{name}：** {_v21_display_value(value, '')}")

    if status in V21_TASK_TERMINAL_STATUSES:
        if st.button("确认关闭", key=f"v21_task_dialog_close_{task_id}", width="stretch"):
            _v21_close_task_dialog_without_app_rerun()
    else:
        st.caption("弹窗会自动更新进度，任务完成后再确认关闭。")


@st.dialog("任务状态", width="large", dismissible=True, on_dismiss="ignore")
def _v21_task_status_dialog() -> None:
    _v21_task_status_dialog_body()


def _v21_render_task_status_dialog_if_needed() -> None:
    if st.session_state.get("v21_task_dialog_open") and st.session_state.get("v21_active_task_id"):
        _v21_task_status_dialog()


def _v21_action_button(
    label: str,
    action_func: Any,
    key: str,
    *,
    args: Sequence[Any] = (),
    action_kwargs: dict[str, Any] | None = None,
    disabled: bool = False,
    clear_snapshots: bool = False,
    help: str | None = None,
) -> None:
    if st.button(label, key=key, width="stretch", disabled=disabled, help=help):
        response = _v21_run_action(label, action_func, *args, clear_snapshots=clear_snapshots, open_dialog=True, **(action_kwargs or {}))
        if response.get("success"):
            st.success(_v21_display_value(response.get("message")))
        else:
            st.error(_v21_display_value(response.get("error") or response.get("message")))
        if response.get("task_id"):
            st.info(f"task_id：{response['task_id']}")
            _v21_task_status_dialog()


def _v21_show_last_action_response() -> None:
    response = st.session_state.get("v21_last_action_response")
    if not isinstance(response, Mapping):
        return
    label = _v21_display_value(response.get("label"), "最近动作")
    message = _v21_display_value(response.get("message"), "暂无动作结果")
    task_id = str(response.get("task_id") or "")
    error = _v21_display_value(response.get("error"), "")
    with st.expander("最近动作结果", expanded=bool(task_id or error)):
        st.write(f"**动作：** {label}")
        st.write(f"**结果：** {message}")
        if task_id:
            st.write(f"**task_id：** {task_id}")
        if error:
            st.error(error)


def _v21_unimplemented_action(label: str) -> None:
    st.caption(f"{label}：该动作接口未实现，前端不提供假按钮。")


def _v21_manual_label_import_state(file_path: str) -> dict[str, Any]:
    text = str(file_path or "").strip()
    if not text:
        return {
            "disabled": True,
            "level": "info",
            "message": "请先导出人工标注表，人工填写后，在路径框中填入文件路径，再导入。",
        }
    path = Path(text)
    if not path.exists():
        return {"disabled": True, "level": "error", "message": f"人工标注表路径不存在：{text}"}
    if not path.is_file():
        return {"disabled": True, "level": "error", "message": f"人工标注表路径不是文件：{text}"}
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        return {"disabled": True, "level": "error", "message": f"人工标注表不可读：{exc}"}
    return {"disabled": False, "level": "success", "message": f"人工标注表可导入：{text}"}


def _v21_task_summary_value(item: Mapping[str, Any], key: str, default: Any = "") -> Any:
    summary = item.get("result_summary") if isinstance(item.get("result_summary"), Mapping) else {}
    return summary.get(key, default)


def _v21_task_result_count(item: Mapping[str, Any]) -> Any:
    for key in ["result_count", "output_rows", "review_queue_count", "failed_sample_count", "missed_winner_count"]:
        value = _v21_task_summary_value(item, key, "")
        if value not in ("", None):
            return value
    return ""


def _v21_task_frame(tasks: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in tasks:
        progress = item.get("progress", 0)
        try:
            progress_text = f"{int(progress)}%"
        except (TypeError, ValueError):
            progress_text = _v21_display_value(progress)
        rows.append(
            {
                "task_id": _v21_display_value(item.get("task_id"), ""),
                "task_name": V21_ACTION_LABELS.get(str(item.get("action_name") or ""), _v21_display_value(item.get("action_name"))),
                "action_name": V21_ACTION_LABELS.get(str(item.get("action_name") or ""), _v21_display_value(item.get("action_name"))),
                "status": V21_TASK_STATUS_LABELS.get(str(item.get("status") or ""), _v21_display_value(item.get("status"))),
                "progress": progress_text,
                "message": _v21_display_value(item.get("message"), ""),
                "start_time": format_datetime_shanghai(item.get("start_time")),
                "end_time": format_datetime_shanghai(item.get("end_time")),
                "elapsed_seconds": _v21_display_value(item.get("elapsed_seconds"), 0),
                "result_count": _v21_display_value(_v21_task_result_count(item), ""),
                "output_path": _v21_display_value(_v21_task_summary_value(item, "output_path", item.get("result_file", "")), ""),
                "input_fingerprint": _v21_display_value(_v21_task_summary_value(item, "input_fingerprint", ""), ""),
                "cache_hit": _v21_display_value(_v21_task_summary_value(item, "used_cache", False)),
                "stale_after_task": _v21_display_value(
                    any(
                        bool(_v21_task_summary_value(item, key, False))
                        for key in ["calibration_report_stale", "suggestions_stale", "stability_report_stale"]
                    )
                ),
                "next_step": _v21_display_value(_v21_task_summary_value(item, "next_step", _v21_task_summary_value(item, "suggested_next_step", "")), ""),
                "used_cache": _v21_display_value(_v21_task_summary_value(item, "used_cache", False)),
                "status_detail": _v21_display_value(item.get("status_detail") or _v21_task_summary_value(item, "status_detail", "")),
                "result_summary": _v21_display_value(json.dumps(item.get("result_summary", {}), ensure_ascii=False, default=str), ""),
                "error": _v21_display_value(item.get("error"), ""),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "task_id",
            "task_name",
            "action_name",
            "status",
            "progress",
            "message",
            "start_time",
            "end_time",
            "elapsed_seconds",
            "result_count",
            "output_path",
            "input_fingerprint",
            "cache_hit",
            "stale_after_task",
            "next_step",
            "used_cache",
            "status_detail",
            "result_summary",
            "error",
        ],
    )


def render_v21_task_queue_panel(expanded: bool = True, key_prefix: str = "v21_task_queue") -> None:
    with st.expander("任务队列", expanded=expanded):
        cols = st.columns([1, 1, 4])
        if cols[0].button("刷新任务状态", key=f"{key_prefix}_refresh", width="stretch"):
            st.session_state[f"{key_prefix}_refresh_at"] = time.time()
        if cols[1].button("查看任务队列", key=f"{key_prefix}_get_tasks", width="stretch"):
            _v21_run_action("查看任务队列", action_api.get_tasks)
        response = action_api.get_tasks()
        tasks = response.get("data", {}).get("tasks", []) if isinstance(response.get("data"), Mapping) else []
        show_dataframe_or_empty(
            _v21_task_frame(tasks),
            empty_text="暂无后台任务。",
            key=f"{key_prefix}_table",
            height=280,
        )
        failed_response = action_api.get_failed_tasks()
        failed = failed_response.get("data", {}).get("tasks", []) if isinstance(failed_response.get("data"), Mapping) else []
        if failed:
            st.markdown("**失败任务**")
            show_dataframe_or_empty(_v21_task_frame(failed), empty_text="暂无失败任务。", key=f"{key_prefix}_failed_table", height=180)


def render_v21_global_actions(snapshots: Mapping[str, Any]) -> None:
    st.markdown("**全局操作区**")
    st.caption("刷新页面快照只重新读取 output 快照；重新生成今日信号会进入后台任务队列。二者不是同一个动作。")
    cols = st.columns(6)
    if cols[0].button("刷新页面快照", key="v21_top_reload_snapshot", width="stretch"):
        _v21_clear_snapshot_cache()
        st.session_state["v21_last_action_response"] = {
            "label": "刷新页面快照",
            "success": True,
            "message": "已重新读取本地总控快照；未重新生成信号，未创建 task_id。",
            "task_id": "",
            "data": {},
            "error": "",
            "timestamp": "",
        }
        st.rerun()
    with cols[1]:
        _v21_action_button("刷新行情数据", action_api.refresh_market_data, "v21_top_refresh_market")
    with cols[2]:
        _v21_action_button("重新生成今日信号", action_api.run_daily_signal, "v21_top_run_daily_signal")
    with cols[3]:
        _v21_action_button("重建 V2.1 总控快照", action_api.rebuild_v21_snapshot, "v21_top_rebuild_snapshot", clear_snapshots=True)
    with cols[4]:
        _v21_action_button("查看任务队列", action_api.get_tasks, "v21_top_get_tasks")
    with cols[5]:
        _v21_action_button("下载今日日报", action_api.download_daily_report, "v21_top_download_report")
    _v21_show_last_action_response()
    if st.session_state.get("v21_active_task_id") or st.session_state.get("v21_last_action_response", {}).get("label") == "查看任务队列":
        render_v21_task_queue_panel(expanded=True, key_prefix="v21_global_task_queue")


def render_v21_overview(snapshots: Mapping[str, Any]) -> None:
    decision = snapshots.get("daily_decision") if isinstance(snapshots.get("daily_decision"), Mapping) else {}
    risk = snapshots.get("risk_gate") if isinstance(snapshots.get("risk_gate"), Mapping) else {}
    status = build_v21_frontend_status(snapshots)
    level = str(risk.get("risk_level") or decision.get("risk_level") or "R0").upper()
    manual_takeover = _v21_bool(decision.get("manual_takeover_required") or risk.get("manual_takeover_required"))
    freeze_entry = _v21_bool(decision.get("freeze_entry") or risk.get("freeze_entry"))
    action_cols = st.columns(5)
    with action_cols[0]:
        _v21_action_button("重新生成今日信号", action_api.run_daily_signal, "v21_overview_run_daily_signal")
    with action_cols[1]:
        _v21_action_button("刷新行情数据", action_api.refresh_market_data, "v21_overview_refresh_market")
    with action_cols[2]:
        _v21_action_button("重新计算市场状态", action_api.recalculate_market_state, "v21_overview_market_state")
    with action_cols[3]:
        _v21_action_button("重新计算风险门控", action_api.recalculate_risk_gate, "v21_overview_risk_gate", action_kwargs={"risk_level": level})
    with action_cols[4]:
        _v21_action_button("下载总控快照", action_api.download_daily_report, "v21_overview_download_snapshot")
    if level in {"R3", "R4", "P0"} or manual_takeover:
        st.error("R3/R4/P0 风险或人工接管已触发：今天优先风险处理，entry 买入信号不得绕过风险门控。")
    elif freeze_entry:
        st.warning("风险门控已冻结买入，候选 ETF 仅作为观察对象。")
    else:
        st.success("风险门控未冻结买入；是否实际买入仍以总控日内裁决和订单意图为准。")
    render_compact_metric_grid(
        [
            ("信号版本", status["signal_version"]),
            ("交易日期", status["trade_date"]),
            ("市场状态", status["market_state"]),
            ("风险等级", status["risk_level"]),
            ("风险分数", status["risk_score"]),
            ("允许买入", status["allow_entry"]),
            ("冻结买入", status["freeze_entry"]),
            ("人工接管", status["manual_takeover_required"]),
            ("ML 观察模式", status["ml_observation_status"]),
            ("候选 ETF 数", status["candidate_count"]),
            ("实际买入 ETF 数", status["actual_buy_count"]),
            ("退出/清仓建议数", status["exit_count"]),
            ("总控生成时间", status["generated_at"]),
            ("当前数据日期", format_trade_date(_first_present(decision.get("trade_date"), status.get("trade_date"))) or status["trade_date"]),
            ("行情最后更新时间", format_datetime_shanghai(_first_present(status.get("market_data_updated_at"), status.get("generated_at"), decision.get("generated_at"))) or status["generated_at"]),
            ("信号生成时间", format_datetime_shanghai(_first_present(decision.get("generated_at"), status.get("generated_at"))) or status["generated_at"]),
            ("总控版本", status["signal_version"]),
            ("是否为最新快照", "是" if not snapshots.get("missing_files") else "否"),
            ("是否存在降级原因", "是" if status["fallback_reason"] not in {"暂无降级说明。", "鏆傛棤闄嶇骇璇存槑銆?", "暂无"} else "否"),
        ],
        class_name="compact-metric-grid summary-metric-grid",
    )
    if int(status["actual_buy_count"] or 0) <= 0:
        st.info("当前无实际买入计划；候选 ETF 仅为观察对象，原因请继续查看买入动作裁决、风险阻断原因和降级说明。")
    if _v21_has_exit_priority(decision):
        st.warning("当前有退出风险优先处理，新买入被降级或冻结。")
    st.markdown("**总控解释**")
    st.write(_v21_display_value(decision.get("explain"), "总控解释缺失。"))
    for item in decision.get("warnings") or []:
        st.warning(_v21_display_value(item))
    st.markdown("**降级说明**")
    st.info(status["fallback_reason"])


def render_v21_candidates(snapshots: Mapping[str, Any]) -> None:
    decision = snapshots.get("daily_decision") if isinstance(snapshots.get("daily_decision"), Mapping) else {}
    orders = _v21_records(snapshots.get("order_intent"))
    cols = st.columns(4)
    with cols[0]:
        _v21_action_button("重新运行 pre_selection", action_api.run_pre_selection, "v21_candidates_run_pre_selection")
    with cols[1]:
        _v21_action_button("重新运行 entry", action_api.run_entry, "v21_candidates_run_entry")
    with cols[2]:
        _v21_action_button("生成订单草稿", action_api.generate_order_intents, "v21_candidates_generate_order_intents")
    with cols[3]:
        _v21_action_button("导出候选列表", action_api.download_daily_report, "v21_candidates_export")
    _v21_unimplemented_action("生成候选 ETF 列表")
    _v21_unimplemented_action("查看暂不参与本策略 ETF")
    _v21_unimplemented_action("查看同板块过滤原因")
    st.info("候选 ETF 表示进入观察池，不是买入计划。买入计划只看实际买入 ETF 列表和订单意图；订单意图是草稿，不是自动下单。")
    st.info(f"{_v21_display_value(decision.get('ml_observation_status'), 'ML 观察模式未启用')}。ML 建议仅供观察，不自动修改交易参数。")
    render_compact_metric_grid(
        [
            ("允许买入", _v21_display_value(decision.get("allow_entry"))),
            ("冻结买入", _v21_display_value(decision.get("freeze_entry"))),
            ("ML 观察模式", _v21_display_value(decision.get("ml_observation_status"), "未启用")),
            ("入选板块", _v21_join(decision.get("selected_sectors"))),
            ("订单草稿数", len(orders)),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )
    st.markdown("**候选 ETF**")
    show_dataframe_or_empty(
        _v21_frame(
            _v21_records(decision.get("candidate_etfs")),
            {
                "etf_code": "ETF代码",
                "etf_name": "ETF名称",
                "sector": "所属板块",
                "rank": "排名",
                "score": "得分",
                "ml_entry_advice": "ML观察建议",
                "ml_confidence": "ML置信度",
                "ml_action_suggestion": "ML动作建议",
                "ml_reason": "ML原因",
                "ml_observation_notice": "ML观察说明",
                "explain": "中文解释",
            },
        ),
        empty_text="暂无候选 ETF。",
        key="v21_candidates",
        height=300,
    )
    st.markdown("**买入动作裁决**")
    show_dataframe_or_empty(
        _v21_frame(
            _v21_records(decision.get("entry_actions")),
            {
                "etf_code": "ETF代码",
                "etf_name": "ETF名称",
                "entry_action": "买入动作",
                "actual_buy": "是否实际买入",
                "target_weight": "建议仓位",
                "confidence": "置信度",
                "ml_entry_advice": "ML观察建议",
                "ml_confidence": "ML置信度",
                "ml_action_suggestion": "ML动作建议",
                "ml_reason": "ML原因",
                "ml_observation_notice": "ML观察说明",
                "block_reason": "风险阻断原因",
                "explain": "中文解释",
            },
        ),
        empty_text="暂无 entry 动作。",
        key="v21_entry_actions",
        height=360,
    )
    with st.expander("查看完整买入说明", expanded=False):
        for item in _v21_records(decision.get("entry_actions")):
            st.write(f"**{_v21_display_value(item.get('etf_code'))} {_v21_display_value(item.get('etf_name'), '')}**")
            st.write(_v21_display_value(item.get("explain"), "暂无说明。"))
    st.markdown("**订单意图/草稿（买入）**")
    show_dataframe_or_empty(
        _v21_order_frame([item for item in orders if str(item.get("side") or "").upper() == "BUY"]),
        empty_text="当前无买入订单草稿。",
        key="v21_buy_orders",
        height=260,
    )


def render_v21_portfolio(snapshots: Mapping[str, Any]) -> None:
    decision = snapshots.get("daily_decision") if isinstance(snapshots.get("daily_decision"), Mapping) else {}
    cols = st.columns(4)
    with cols[0]:
        _v21_action_button("刷新持仓", action_api.sync_qmt_positions, "v21_portfolio_sync_positions")
    with cols[1]:
        _v21_action_button("重新运行 exit", action_api.run_exit, "v21_portfolio_run_exit")
    with cols[2]:
        _v21_action_button("生成卖出建议", action_api.run_exit, "v21_portfolio_generate_exit_advice")
    with cols[3]:
        _v21_action_button("导出持仓快照", action_api.download_daily_report, "v21_portfolio_export_snapshot")
    _v21_unimplemented_action("刷新实时价格")
    _v21_unimplemented_action("查看持仓风险暴露")
    st.caption("QMT 未连接时仍可展开持仓输入维护，手动导入持仓、更新成本、刷新价格；持仓写入只发生在表单点击保存持仓之后。")
    if _v21_has_exit_priority(decision):
        st.warning("当前有退出风险优先处理，新买入被降级或冻结。")
    st.info("持仓页只展示 V2.1 PortfolioSnapshot；数据校验通过表示数据可信，不等于买入信号。")
    show_dataframe_or_empty(
        _v21_frame(
            _v21_records(snapshots.get("portfolio")),
            {
                "etf_code": "ETF代码",
                "etf_name": "ETF名称",
                "cost_price": "成本价",
                "current_price": "当前价",
                "pnl": "浮盈亏",
                "pnl_pct": "浮盈亏比例",
                "holding_days": "持仓天数",
                "current_weight": "当前权重",
                "target_weight": "目标权重",
                "exit_action": "退出动作",
                "risk_status": "风险状态",
                "explain": "中文解释",
            },
        ),
        empty_text="当前 PortfolioSnapshot 为空。",
        key="v21_portfolio",
        height=360,
    )
    with st.expander("持仓输入维护（点击保存后才写入）", expanded=False):
        if st.button("加载持仓编辑器", key="v21_load_position_editor"):
            st.session_state["v21_position_editor_loaded"] = True
        if st.session_state.get("v21_position_editor_loaded"):
            render_current_position_module(load_etf_names(PROJECT_ROOT))
        else:
            st.caption("默认不加载持仓编辑器，避免打开页面时读取或写入持仓配置。需要维护时请点击加载。")


def render_v21_risk(snapshots: Mapping[str, Any]) -> None:
    risk = snapshots.get("risk_gate") if isinstance(snapshots.get("risk_gate"), Mapping) else {}
    level = str(risk.get("risk_level") or "R0").upper()
    with st.form("v21_create_risk_event_form", clear_on_submit=False):
        st.markdown("**新增风险事件**")
        event_title = st.text_input("事件标题", key="v21_risk_event_title")
        event_level = st.selectbox("风险等级", ["R1", "R2", "R3", "R4"], key="v21_risk_event_level")
        event_date = st.date_input("事件日期", value=date.today(), key="v21_risk_event_date")
        event_desc = st.text_area("事件说明", key="v21_risk_event_desc")
        if st.form_submit_button("新增风险事件", width="stretch"):
            _v21_run_action(
                "新增风险事件",
                action_api.create_risk_event,
                event_date=event_date.isoformat(),
                event_type="other",
                title=event_title or "人工录入风险事件",
                description=event_desc,
                risk_level=event_level,
                status="active",
            )
    cols = st.columns(4)
    with cols[0]:
        _v21_action_button("编辑风险事件", action_api.update_risk_event, "v21_risk_update_event")
    with cols[1]:
        _v21_action_button("关闭/过期风险事件", action_api.expire_risk_event, "v21_risk_expire_event")
    with cols[2]:
        _v21_action_button("重新计算风险评分", action_api.recalculate_risk_gate, "v21_risk_recalculate", action_kwargs={"risk_level": level})
    with cols[3]:
        _v21_action_button("导出风险日志", action_api.export_risk_log, "v21_risk_export_log")
    cols2 = st.columns(4)
    with cols2[0]:
        _v21_action_button("触发人工接管", action_api.trigger_manual_takeover, "v21_risk_manual_takeover")
    with cols2[1]:
        _v21_action_button("解除人工接管", action_api.release_manual_takeover, "v21_risk_release_takeover")
    with cols2[2]:
        _v21_action_button("查看影响板块", action_api.get_affected_sectors, "v21_risk_affected_sectors")
    with cols2[3]:
        _v21_action_button("查看风险等级说明", action_api.get_risk_level_explain, "v21_risk_level_explain", action_kwargs={"risk_level": level})
    if level in {"R3", "R4", "P0"}:
        st.error("高风险状态：风险门控高于 entry 买入信号。")
    else:
        st.info("风险门控高于 entry 买入信号；entry、pre_selection、historical_ml、QMT 都不能绕过它。")
    render_compact_metric_grid(
        [
            ("风险等级", _v21_display_value(risk.get("risk_level"))),
            ("风险分数", _v21_display_value(risk.get("risk_score"))),
            ("冻结买入", _v21_display_value(risk.get("freeze_entry"))),
            ("权益仓位上限", _v21_display_value(risk.get("equity_cap_override"))),
            ("人工接管", _v21_display_value(risk.get("manual_takeover_required"))),
            ("来源", _v21_display_value(risk.get("source"))),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )
    st.write(f"**影响板块：** {_v21_join(risk.get('affected_sectors'))}")
    st.write(f"**影响 ETF：** {_v21_join(risk.get('affected_etfs'))}")
    st.markdown("**风险事件**")
    risk_events = _v21_records(risk.get("risk_events"))
    show_dataframe_or_empty(pd.DataFrame(risk_events).map(_v21_display_value) if risk_events else pd.DataFrame(), empty_text="暂无生效风险事件。", key="v21_risk_events", height=240)
    st.markdown("**解释**")
    st.write(_v21_display_value(risk.get("explain"), "暂无风险解释。"))


HML_ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "historical_ml_61"


def _hml_dirs() -> dict[str, Path]:
    root = HML_ARTIFACT_ROOT
    return {
        "root": root,
        "generated": root / "generated",
        "to_review": root / "to_review",
        "review_return": root / "review_return",
        "state": root / "state",
        "logs": root / "logs",
    }


def _hml_existing(bucket: str, filename: str) -> Path:
    dirs = _hml_dirs()
    primary = dirs[bucket] / filename
    legacy = dirs["root"] / filename
    return primary if primary.exists() or not legacy.exists() else legacy


def _hml_count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    if path.suffix.lower() != ".csv":
        return 1 if path.read_text(encoding="utf-8", errors="ignore").strip() else 0
    try:
        return int(len(pd.read_csv(path)))
    except Exception:
        return 0


def _hml_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.suffix.lower() != ".csv":
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _hml_latest_return_file() -> Path | None:
    review_return = _hml_dirs()["review_return"]
    patterns = [
        "manual_review_labeled.csv",
        "manual_corrections.csv",
        "low_confidence_review_labeled.csv",
        "missed_big_winner_review_labeled.csv",
        "pending_human_review_labeled.csv",
        "*_labeled.csv",
        "*_corrections.csv",
    ]
    files: dict[Path, None] = {}
    for pattern in patterns:
        for path in review_return.glob(pattern):
            if path.is_file():
                files[path] = None
    return max(files, key=lambda path: path.stat().st_mtime_ns) if files else None


def _hml_stale_flags() -> dict[str, Any]:
    path = _hml_dirs()["state"] / "historical_ml_stale_flags.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _hml_status(outputs: list[Path], deps: list[Path] | None = None, stale_key: str = "") -> tuple[str, bool, str]:
    deps = deps or []
    missing = [path for path in deps if not path.exists()]
    if missing:
        return "未开始", True, f"依赖未满足：{missing[0]}"
    if stale_key and _hml_stale_flags().get(stale_key):
        return "已过期，需要重跑", False, "上游人工标注已变化"
    if all(path.exists() for path in outputs):
        return "已完成", False, ""
    return "可运行", False, ""


def _hml_metrics(paths: list[Path], labels: list[str]) -> list[tuple[str, Any]]:
    return [(label, _hml_count_rows(path)) for label, path in zip(labels, paths)]


def _hml_render_step(
    step_no: int,
    title: str,
    *,
    status: tuple[str, bool, str],
    buttons: list[dict[str, Any]],
    outputs: list[Path],
    metrics: list[tuple[str, Any]],
    note: str = "",
) -> None:
    status_text, dependency_disabled, reason = status
    with st.container(border=True):
        st.markdown(f"**Step {step_no}: {title}**")
        st.caption(f"状态：{status_text}" + (f"；{reason}" if reason else ""))
        if outputs:
            st.caption("输出：" + " | ".join(str(path) for path in outputs))
        if metrics:
            render_compact_metric_grid(metrics, class_name="compact-metric-grid strategy-metric-grid")
        if note:
            st.info(note)
        cols = st.columns(max(1, len(buttons)))
        for idx, button in enumerate(buttons):
            disabled = bool(button.get("disabled", False) or dependency_disabled)
            help_text = str(button.get("help") or reason or "")
            with cols[idx]:
                _v21_action_button(
                    button["label"],
                    button["action"],
                    button["key"],
                    args=button.get("args", ()),
                    action_kwargs=button.get("kwargs"),
                    disabled=disabled,
                    help=help_text or None,
                )


def render_v21_learning(snapshots: Mapping[str, Any]) -> None:
    learning = _v21_records(snapshots.get("learning"))
    historical = _v21_records(snapshots.get("historical_ml"))
    dirs = _hml_dirs()
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    st.markdown("**历史学习流程向导**")
    st.caption(f"真实目录：{dirs['root']}；系统结果在 generated，待复核文件在 to_review，人工回传文件在 review_return。")
    range_cols = st.columns(2)
    start_date = range_cols[0].date_input("历史区间开始", value=date.today() - timedelta(days=30), key="v21_hml_start_date")
    end_date = range_cols[1].date_input("历史区间结束", value=date.today(), key="v21_hml_end_date")
    action_kwargs = {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "artifacts_dir": str(dirs["root"])}
    force_cols = st.columns(3)
    force_calibration_report = force_cols[0].checkbox("强制重新生成校准报告", key="v21_hml_force_calibration_report")
    force_parameter_suggestions = force_cols[1].checkbox("强制重新生成参数建议", key="v21_hml_force_parameter_suggestions")
    force_overfit_check = force_cols[2].checkbox("强制重新运行过拟合检查", key="v21_hml_force_overfit_check")
    calibration_action_kwargs = {**action_kwargs, "force_regenerate_calibration_report": force_calibration_report}
    suggestion_action_kwargs = {**action_kwargs, "force_regenerate_parameter_suggestions": force_parameter_suggestions}
    overfit_action_kwargs = {**action_kwargs, "force_regenerate_overfit_check": force_overfit_check}

    daily_snapshot = _hml_existing("generated", "daily_decision_snapshot.csv")
    daily_etf = _hml_existing("generated", "daily_etf_samples.csv")
    daily_sector = _hml_existing("generated", "daily_sector_samples.csv")
    entry_unlabeled = _hml_existing("generated", "entry_candidate_samples_unlabeled.csv")
    entry_labeled = _hml_existing("generated", "entry_candidate_samples_labeled.csv")
    failure_samples = _hml_existing("generated", "failure_samples.csv")
    missed_samples = _hml_existing("generated", "missed_opportunity_samples.csv")
    review_queue = _hml_existing("to_review", "manual_review_queue.csv")
    review_prefilled = _hml_existing("to_review", "manual_review_prefilled.csv")
    review_accepted = _hml_existing("to_review", "manual_review_accepted.csv")
    low_conf = _hml_existing("to_review", "low_confidence_review.csv")
    missed_review = _hml_existing("to_review", "missed_big_winner_review.csv")
    pending_review = _hml_existing("to_review", "pending_human_review.csv")
    calibration_report = _hml_existing("generated", "entry_calibration_report.md")
    suggestions = _hml_existing("generated", "entry_calibration_suggestions.csv")
    stability_report = _hml_existing("generated", "ml_stability_report.md")
    latest_return = _hml_latest_return_file()

    labeled_df = _hml_csv(entry_labeled)
    review_df = _hml_csv(review_queue)
    prefilled_df = _hml_csv(review_prefilled)
    accepted_df = _hml_csv(review_accepted)
    failure_df = _hml_csv(failure_samples)
    missed_df = _hml_csv(missed_samples)

    _hml_render_step(
        1,
        "运行历史回放",
        status=_hml_status([daily_snapshot]),
        buttons=[{"label": "运行历史回放", "action": action_api.run_historical_replay, "key": "v21_hml_replay", "args": (start_date.isoformat(), end_date.isoformat()), "kwargs": {"artifacts_dir": str(dirs["root"])}}],
        outputs=[daily_snapshot],
        metrics=[("回放交易日数量", _hml_count_rows(daily_snapshot)), ("覆盖 ETF 数量", _v21_display_value("")), ("耗时", "见任务队列"), ("输出路径", daily_snapshot)],
    )
    _hml_render_step(
        2,
        "生成每日样本",
        status=_hml_status([daily_etf, daily_sector], [daily_snapshot]),
        buttons=[{"label": "生成每日样本", "action": action_api.generate_daily_samples, "key": "v21_hml_daily_samples", "args": (start_date.isoformat(), end_date.isoformat()), "kwargs": {"artifacts_dir": str(dirs["root"])}}],
        outputs=[daily_etf, daily_sector],
        metrics=[("样本行数", _hml_count_rows(daily_etf) + _hml_count_rows(daily_sector)), ("交易日数量", _v21_display_value("")), ("ETF 数量", _v21_display_value("")), ("板块数量", _v21_display_value(""))],
    )
    _hml_render_step(
        3,
        "生成 entry 候选样本",
        status=_hml_status([entry_unlabeled], [daily_etf]),
        buttons=[{"label": "生成 entry 候选样本", "action": action_api.generate_entry_samples, "key": "v21_hml_entry_samples", "args": (start_date.isoformat(), end_date.isoformat()), "kwargs": {"artifacts_dir": str(dirs["root"])}}],
        outputs=[entry_unlabeled],
        metrics=[("候选样本数量", _hml_count_rows(entry_unlabeled)), ("候选 ETF 数量", _v21_display_value("")), ("候选交易日数量", _v21_display_value(""))],
    )
    _hml_render_step(
        4,
        "自动打标签",
        status=_hml_status([entry_labeled], [entry_unlabeled]),
        buttons=[{"label": "自动打标签", "action": action_api.auto_label_samples, "key": "v21_hml_auto_label", "kwargs": action_kwargs}],
        outputs=[entry_labeled],
        metrics=[
            ("good_entry 数量", int(labeled_df.get("auto_label", pd.Series(dtype=str)).fillna("").astype(str).eq("good_entry").sum()) if not labeled_df.empty else 0),
            ("bad_entry 数量", int(labeled_df.get("auto_label", pd.Series(dtype=str)).fillna("").astype(str).eq("bad_entry").sum()) if not labeled_df.empty else 0),
            ("neutral_entry 数量", int(labeled_df.get("auto_label", pd.Series(dtype=str)).fillna("").astype(str).eq("neutral_entry").sum()) if not labeled_df.empty else 0),
            ("unlabeled 数量", int(labeled_df.get("auto_label", pd.Series(dtype=str)).fillna("").astype(str).eq("unlabeled").sum()) if not labeled_df.empty else 0),
        ],
    )
    _hml_render_step(
        5,
        "生成失败和错过样本",
        status=_hml_status([failure_samples, missed_samples], [entry_labeled]),
        buttons=[
            {"label": "生成失败样本", "action": action_api.generate_failure_samples, "key": "v21_hml_failure_samples", "kwargs": action_kwargs},
            {"label": "生成错过样本", "action": action_api.generate_missed_opportunity_samples, "key": "v21_hml_missed_samples", "kwargs": action_kwargs},
        ],
        outputs=[failure_samples, missed_samples],
        metrics=[
            ("large_loss_entry 数量", int(failure_df.get("review_reason", pd.Series(dtype=str)).fillna("").astype(str).eq("large_loss_entry").sum()) if not failure_df.empty else 0),
            ("quick_failure_entry 数量", int(failure_df.get("review_reason", pd.Series(dtype=str)).fillna("").astype(str).eq("quick_failure_entry").sum()) if not failure_df.empty else 0),
            ("bought_and_knocked_out 数量", int(failure_df.get("review_reason", pd.Series(dtype=str)).fillna("").astype(str).eq("bought_and_knocked_out").sum()) if not failure_df.empty else 0),
            ("missed_big_winner 数量", int(missed_df.get("review_reason", pd.Series(dtype=str)).fillna("").astype(str).eq("missed_big_winner").sum()) if not missed_df.empty else _hml_count_rows(missed_samples)),
        ],
    )
    _hml_render_step(
        6,
        "生成人工复核队列",
        status=_hml_status([review_queue], [failure_samples, missed_samples]),
        buttons=[
            {"label": "生成手工复核队列", "action": action_api.generate_manual_review_queue, "key": "v21_hml_review_queue", "kwargs": action_kwargs},
            {"label": "打开待复核文件夹", "action": action_api.open_manual_review_folder, "key": "v21_hml_open_to_review", "kwargs": {"artifacts_dir": str(dirs["root"])}},
        ],
        outputs=[review_queue],
        metrics=[
            ("review_queue 总数", _hml_count_rows(review_queue)),
            ("失败类样本数", int(review_df.get("review_reason", pd.Series(dtype=str)).fillna("").astype(str).isin(["large_loss_entry", "quick_failure_entry", "bought_and_knocked_out"]).sum()) if not review_df.empty else 0),
            ("错过机会样本数", int(review_df.get("review_reason", pd.Series(dtype=str)).fillna("").astype(str).eq("missed_big_winner").sum()) if not review_df.empty else 0),
        ],
    )
    _hml_render_step(
        7,
        "自动预填人工复核",
        status=_hml_status([review_prefilled], [review_queue]),
        buttons=[{"label": "自动预填人工复核", "action": action_api.prefill_manual_review_labels, "key": "v21_hml_prefill_review", "kwargs": action_kwargs}],
        outputs=[review_prefilled],
        metrics=[
            ("总行数", _hml_count_rows(review_prefilled)),
            ("自动预填数", int(prefilled_df.get("suggested_manual_label", pd.Series(dtype=str)).fillna("").astype(str).str.strip().ne("").sum()) if not prefilled_df.empty else 0),
            ("高置信数", int(prefilled_df.get("suggested_confidence", pd.Series(dtype=str)).fillna("").astype(str).eq("high").sum()) if not prefilled_df.empty else 0),
            ("中置信数", int(prefilled_df.get("suggested_confidence", pd.Series(dtype=str)).fillna("").astype(str).eq("medium").sum()) if not prefilled_df.empty else 0),
            ("低置信数", int(prefilled_df.get("suggested_confidence", pd.Series(dtype=str)).fillna("").astype(str).eq("low").sum()) if not prefilled_df.empty else 0),
            ("需要人工复核数", int(prefilled_df.get("need_human_review", pd.Series(dtype=str)).fillna("").astype(str).isin(["1", "true", "True", "yes", "是"]).sum()) if not prefilled_df.empty else 0),
            ("missed_big_winner 覆盖数", int(prefilled_df.get("review_reason", pd.Series(dtype=str)).fillna("").astype(str).eq("missed_big_winner").sum()) if not prefilled_df.empty else 0),
        ],
    )
    _hml_render_step(
        8,
        "采纳高置信标注",
        status=_hml_status([review_accepted], [review_prefilled]),
        buttons=[{"label": "一键采纳高置信标注", "action": action_api.adopt_high_confidence_manual_labels, "key": "v21_hml_adopt_high_conf", "kwargs": action_kwargs}],
        outputs=[review_accepted],
        metrics=[
            ("高置信采纳数量", int(accepted_df.get("manual_confidence", pd.Series(dtype=str)).fillna("").astype(str).eq("high").sum()) if not accepted_df.empty else 0),
            ("有效 manual_label 数量", int(accepted_df.get("manual_label", pd.Series(dtype=str)).fillna("").astype(str).str.strip().ne("").sum()) if not accepted_df.empty else 0),
            ("剩余待复核数量", int(accepted_df.get("manual_label", pd.Series(dtype=str)).fillna("").astype(str).str.strip().eq("").sum()) if not accepted_df.empty else 0),
        ],
    )
    low_note = "无低置信样本需要人工复核，可直接继续生成校准报告。" if low_conf.exists() and _hml_count_rows(low_conf) == 0 else ""
    _hml_render_step(
        9,
        "导出待人工复核文件",
        status=_hml_status([low_conf, missed_review, pending_review], [review_prefilled]),
        buttons=[
            {"label": "导出低置信复核表", "action": action_api.export_low_confidence_review_file, "key": "v21_hml_export_low_conf", "kwargs": action_kwargs},
            {"label": "导出 missed_big_winner 复核表", "action": action_api.export_missed_winner_review_file, "key": "v21_hml_export_missed_winner", "kwargs": action_kwargs},
            {"label": "导出全部待复核表", "action": action_api.export_pending_manual_review_file, "key": "v21_hml_export_pending", "kwargs": action_kwargs},
        ],
        outputs=[low_conf, missed_review, pending_review],
        metrics=_hml_metrics([low_conf, missed_review, pending_review], ["低置信表行数", "missed_big_winner 行数", "全部待复核行数"]),
        note=low_note,
    )
    latest_return_text = str(latest_return) if latest_return else "未在 review_return 目录发现人工修正表，请将填写后的文件放入该目录。"
    _hml_render_step(
        10,
        "导入人工回传文件",
        status=("可运行" if latest_return else "未开始", False, "" if latest_return else "review_return 里没有有效文件"),
        buttons=[
            {"label": "打开回传文件夹", "action": action_api.open_manual_review_return_folder, "key": "v21_hml_open_review_return", "kwargs": {"artifacts_dir": str(dirs["root"])}, "disabled": False},
            {"label": "扫描回传文件", "action": action_api.scan_manual_review_return_files, "key": "v21_hml_scan_return", "kwargs": {"artifacts_dir": str(dirs["root"])}, "disabled": False},
            {"label": "导入最新回传文件", "action": action_api.import_latest_manual_review_return, "key": "v21_hml_import_latest_return", "kwargs": {"artifacts_dir": str(dirs["root"])}, "disabled": not bool(latest_return)},
        ],
        outputs=[dirs["review_return"]],
        metrics=[("即将导入文件", latest_return_text), ("是否会影响校准报告", "导入后按有效 manual_label 判断")],
    )
    _hml_render_step(
        11,
        "生成 entry 校准报告",
        status=_hml_status([calibration_report], [entry_labeled], "calibration_report_stale"),
        buttons=[{"label": "生成 entry 校准报告", "action": action_api.generate_entry_calibration_report, "key": "v21_hml_calibration_report", "kwargs": calibration_action_kwargs}],
        outputs=[calibration_report],
        metrics=[("自动标签样本数", _hml_count_rows(entry_labeled)), ("人工修正数", "见任务队列"), ("input_fingerprint", "见任务队列")],
    )
    _hml_render_step(
        12,
        "生成参数建议",
        status=_hml_status([suggestions], [calibration_report], "suggestions_stale"),
        buttons=[{"label": "生成参数建议", "action": action_api.generate_parameter_suggestions, "key": "v21_hml_parameter_suggestions", "kwargs": suggestion_action_kwargs}],
        outputs=[suggestions],
        metrics=[("防错建议", "避免假突破/追高尾段/震荡冲高/板块拥挤"), ("敢买建议", "entry 不敢买/错过大涨/阈值过高/小仓试探")],
    )
    _hml_render_step(
        13,
        "运行过拟合检查",
        status=_hml_status([stability_report], [suggestions], "stability_report_stale"),
        buttons=[{"label": "运行过拟合检查", "action": action_api.run_overfit_check, "key": "v21_hml_overfit_check", "kwargs": overfit_action_kwargs}],
        outputs=[stability_report],
        metrics=[("训练区间", start_date.isoformat()), ("验证区间", end_date.isoformat()), ("参数稳定性", "见报告"), ("是否疑似过拟合", "见报告"), ("是否允许进入观察模式", "见报告")],
    )

    with st.expander("高级：手动路径和中置信采纳", expanded=False):
        manual_label_path = st.text_input("人工标注表路径", key="v21_manual_label_path")
        manual_label_state = _v21_manual_label_import_state(manual_label_path)
        if manual_label_state["level"] == "error":
            st.error(manual_label_state["message"])
        elif manual_label_state["level"] == "success":
            st.success(manual_label_state["message"])
        else:
            st.caption(manual_label_state["message"])
        usable_manual_path = manual_label_path if not manual_label_state["disabled"] else ""
        adv_cols = st.columns(3)
        with adv_cols[0]:
            _v21_action_button("导入人工标注表", action_api.import_manual_labels, "v21_hml_import_labels", args=(usable_manual_path,), action_kwargs={"artifacts_dir": str(dirs["root"])}, disabled=not bool(usable_manual_path))
        with adv_cols[1]:
            _v21_action_button("导入人工修正表", action_api.import_manual_corrections, "v21_hml_import_corrections", args=(usable_manual_path,), action_kwargs={"artifacts_dir": str(dirs["root"])}, disabled=not bool(usable_manual_path))
        with adv_cols[2]:
            _v21_action_button("一键采纳中置信标注", action_api.adopt_medium_confidence_manual_labels, "v21_hml_adopt_medium_conf", action_kwargs=action_kwargs)

    _v21_action_button("查看历史学习任务日志", action_api.get_historical_ml_task_logs, "v21_hml_task_logs")
    render_v21_task_queue_panel(expanded=False, key_prefix="v21_hml_task_queue")
    st.info("学习/历史机器学习只提供校准建议，不自动修改当日交易参数。")
    render_compact_metric_grid(
        [
            ("learning 样本数", len(learning)),
            ("historical_ml 样本数", len(historical)),
            ("2024-09-24 后样本", sum(1 for item in learning + historical if _v21_bool(item.get("post_924_regime")))),
            ("校准建议状态", "仅建议，不改参数"),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )
    columns = {
        "trade_date": "交易日期",
        "etf_code": "ETF代码",
        "etf_name": "ETF名称",
        "signal_type": "样本类型",
        "market_state": "市场状态",
        "entry_action": "entry 动作",
        "exit_action": "exit 动作",
        "post_924_regime": "2024-09-24 后样本",
        "hindsight_label": "后验样本状态",
        "failure_type": "失败归因",
        "calibration_suggestion": "校准建议",
        "explain": "中文解释",
    }
    st.markdown("**学习建议摘要**")
    show_dataframe_or_empty(_v21_frame(learning, columns), empty_text="暂无学习建议摘要。", key="v21_learning", height=320)
    st.markdown("**历史学习摘要**")
    show_dataframe_or_empty(_v21_frame(historical, columns), empty_text="暂无历史学习摘要。", key="v21_historical", height=320)


def render_v21_qmt(snapshots: Mapping[str, Any]) -> None:
    orders = _v21_records(snapshots.get("order_intent"))
    status = snapshots.get("status") if isinstance(snapshots.get("status"), Mapping) else {}
    risk = snapshots.get("risk_gate") if isinstance(snapshots.get("risk_gate"), Mapping) else {}
    risk_level = str(risk.get("risk_level") or "R0").upper()
    cols = st.columns(4)
    with cols[0]:
        _v21_action_button("连接 QMT", action_api.connect_qmt, "v21_qmt_connect")
    with cols[1]:
        _v21_action_button("断开 QMT", action_api.disconnect_qmt, "v21_qmt_disconnect")
    with cols[2]:
        _v21_action_button("同步资金", action_api.sync_qmt_account, "v21_qmt_sync_account")
    with cols[3]:
        _v21_action_button("同步持仓", action_api.sync_qmt_positions, "v21_qmt_sync_positions")
    cols2 = st.columns(4)
    with cols2[0]:
        _v21_action_button("同步委托", action_api.sync_qmt_orders, "v21_qmt_sync_orders")
    with cols2[1]:
        _v21_action_button("同步成交", action_api.sync_qmt_trades, "v21_qmt_sync_trades")
    with cols2[2]:
        _v21_action_button("生成订单草稿", action_api.generate_order_intents, "v21_qmt_generate_order_intents")
    with cols2[3]:
        _v21_action_button("运行下单前风控", action_api.run_pre_order_risk_check, "v21_qmt_pre_order_risk", action_kwargs={"risk_level": risk_level})
    cols3 = st.columns(3)
    with cols3[0]:
        _v21_action_button("模拟盘提交订单", action_api.submit_mock_order, "v21_qmt_submit_mock", action_kwargs={"risk_level": risk_level})
    with cols3[1]:
        _v21_action_button("撤单", action_api.cancel_mock_order, "v21_qmt_cancel_order", action_kwargs={"order_id": str(st.session_state.get("v21_cancel_order_id", ""))})
    with cols3[2]:
        _v21_action_button("查看执行日志", action_api.get_execution_logs, "v21_qmt_execution_logs")
    st.text_input("撤单订单号", key="v21_cancel_order_id")
    if risk_level in {"R3", "R4", "P0"}:
        st.error("当前 R3/R4/P0 风险状态会阻断 QMT 下单意图；模拟盘提交也会显示风险阻断原因。")
    st.warning("QMT 当前为人工确认/模拟执行边界，不自动实盘下单。")
    render_compact_metric_grid(
        [
            ("订单草稿数", len(orders)),
            ("实盘自动下单", "没有"),
            ("QMT 可用状态", _v21_display_value(status.get("qmt_execution_available"))),
            ("执行边界", "草稿 / 人工确认 / 只读"),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )
    show_dataframe_or_empty(_v21_order_frame(orders), empty_text="当前无订单意图。", key="v21_qmt_orders", height=420)


def render_v21_data_quality(snapshots: Mapping[str, Any]) -> None:
    status = snapshots.get("status") if isinstance(snapshots.get("status"), Mapping) else {}
    missing = snapshots.get("missing_files") or []
    cols = st.columns(4)
    with cols[0]:
        _v21_action_button("运行数据健康检查", action_api.run_data_health_check, "v21_dq_health_check")
    with cols[1]:
        _v21_action_button("检查 ETF 样本数量", action_api.check_etf_sample_count, "v21_dq_sample_count")
    with cols[2]:
        _v21_action_button("检查缺失数据", action_api.check_missing_data, "v21_dq_missing_data")
    with cols[3]:
        _v21_action_button("检查异常价格", action_api.check_abnormal_prices, "v21_dq_abnormal_prices")
    cols2 = st.columns(4)
    with cols2[0]:
        _v21_action_button("检查交易日", action_api.check_trading_calendar, "v21_dq_calendar")
    with cols2[1]:
        _v21_action_button("清理缓存", action_api.clear_cache, "v21_dq_clear_cache")
    with cols2[2]:
        _v21_action_button("重建总控快照", action_api.rebuild_control_snapshot, "v21_dq_rebuild_snapshot", clear_snapshots=True)
    with cols2[3]:
        _v21_action_button("查看最近任务日志", action_api.get_recent_logs, "v21_dq_recent_logs")
    _v21_action_button("查看失败任务", action_api.get_failed_tasks, "v21_dq_failed_tasks")
    render_v21_task_queue_panel(expanded=False, key_prefix="v21_dq_task_queue")
    st.info("数据校验通过不等于买入信号；这里只展示总控状态和降级说明，不深入各模块临时文件。")
    render_compact_metric_grid(
        [
            ("总控状态", _v21_display_value(status.get("status"))),
            ("总控生成时间", _v21_display_value(status.get("generated_at"))),
            ("缺失快照数", len(missing)),
            ("最近一次生成信号时间", _v21_display_value(status.get("generated_at"))),
        ],
        class_name="compact-metric-grid strategy-metric-grid",
    )
    if missing:
        st.warning("缺失总控快照：" + "、".join(missing))
    st.markdown("**降级原因**")
    st.write(_v21_display_value(status.get("fallback_reason"), "暂无降级说明。"))
    st.markdown("**运行警告**")
    for item in status.get("warnings") or []:
        st.warning(_v21_display_value(item))


def render_v21_v1_reference() -> None:
    st.info("V1 传统信号，仅用于对照。V2.1 主页面不依赖 V1 输出。")
    path = OUTPUT_DIR / "compare_signal.csv"
    if not path.exists():
        st.caption("暂无 compare_signal.csv。")
        return
    try:
        frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception as exc:  # noqa: BLE001
        st.warning(f"compare_signal.csv 读取失败：{exc}")
        return
    cols = [col for col in ["trade_date", "strategy_name", "signal_version", "target_symbols", "suggested_buy", "suggested_sell", "operation_reason"] if col in frame.columns]
    show_dataframe_or_empty(_clean_display_frame(frame[cols] if cols else frame.head(1)), empty_text="暂无 V1 对照数据。", key="v21_v1_reference", height=260)


def render_page() -> None:
    data = _load_dashboard_data_cached(str(PROJECT_ROOT), _dashboard_output_signature(PROJECT_ROOT))
    observation_cash = default_observation_cash()
    sidebar_open = sidebar_is_open()
    render_sidebar_layout(sidebar_open)
    render_sidebar_toggle(sidebar_open)
    if sidebar_open:
        selected_date, selected_strategy, observation_cash, command_ran = render_sidebar(data, observation_cash)
    else:
        selected_date = default_signal_date(data.overview)
        selected_strategy = MAIN_STRATEGY
        command_ran = False
    if command_ran:
        data = _load_dashboard_data_cached(str(PROJECT_ROOT), _dashboard_output_signature(PROJECT_ROOT))

    st.title("日频右侧确认型 ETF 动量轮动总控")
    st.caption(
        "当前策略属于右侧确认型趋势跟随策略，不预测启动点，也不做左侧埋伏。系统通过日 K 动量、趋势形态、"
        "成交活跃度和相对强弱确认 ETF 已经走强后，再给出交易建议。策略通过日频更新进行纠错和风控，"
        "降低买晚、买错后的回撤风险。"
    )
    render_risk_warning_banner()
    render_top_summary(data, selected_strategy, observation_cash)

    date_errors = validate_signal_dates(data.overview, selected_date, PROJECT_ROOT)
    for error in date_errors:
        st.error(error)
    if date_errors:
        st.warning("当前输出未通过日期一致性校验，请重新生成信号或查看运行日志。")

    tabs = st.tabs(["当前信号", "当前持仓", "校准研究", "数据质量", "历史对照 / 旧版参考", "运行日志"])
    with tabs[0]:
        render_v2_native_tab(data, selected_date, observation_cash)

    with tabs[1]:
        render_current_position_module(data.etf_names)

    with tabs[2]:
        render_v2_calibration_tab()

    with tabs[3]:
        render_data_quality_tab(data)
        with st.expander("ETF 池与缓存状态", expanded=False):
            render_universe_module(data)

    with tabs[4]:
        render_legacy_v1_tab(data)

    with tabs[5]:
        render_logs()

    st.divider()
    st.caption("安全边界：不自动下单，不连接券商，不构成投资建议；所有结果仅用于人工观察和研究。")


def render_page() -> None:
    snapshots = _load_v21_snapshots_cached(str(PROJECT_ROOT), _v21_output_signature(PROJECT_ROOT))
    render_v21_global_actions(snapshots)

    st.title("V2.1 ETF 总控决策台")
    st.caption("前端只读取 V2.1 总控快照；打开页面不会自动重新生成信号，也不会直接追 7 个项目部内部临时文件。")
    if st.button("重新读取总控快照", key="v21_reload_snapshots"):
        _load_v21_snapshots_cached.clear()
        st.rerun()

    tabs = st.tabs(["今日总览", "候选与买入", "持仓与卖出", "风险预警", "历史学习", "QMT 执行", "数据质量与运行日志", "V1 对照"])
    with tabs[0]:
        render_v21_overview(snapshots)
    with tabs[1]:
        render_v21_candidates(snapshots)
    with tabs[2]:
        render_v21_portfolio(snapshots)
    with tabs[3]:
        render_v21_risk(snapshots)
    with tabs[4]:
        render_v21_learning(snapshots)
    with tabs[5]:
        render_v21_qmt(snapshots)
    with tabs[6]:
        render_v21_data_quality(snapshots)
    with tabs[7]:
        with st.expander("V1 传统信号，仅用于对照", expanded=False):
            render_v21_v1_reference()

    st.divider()
    st.caption("安全边界：候选 ETF 不是买入计划；OrderIntent 是订单意图/草稿，不自动下单；学习建议不自动改参数。")


def main() -> None:
    st.set_page_config(page_title="日频右侧确认型 ETF 动量轮动策略", layout="wide")
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
        render_advanced_diagnostics(key_prefix="diag_render_error")


if __name__ == "__main__":
    main()
