from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import streamlit as st
import yaml

from ui.components import metric_card, show_dataframe_or_empty, status_badge
from ui.signal_parser import (
    MAIN_STRATEGY,
    STRATEGY_ORDER,
    load_dashboard_data,
    parse_buy_table,
    parse_skip_table,
    parse_target_table,
    split_pipe_items,
    strategy_row,
)


PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = Path(sys.executable)
OUTPUT_DIR = PROJECT_ROOT / "output"
CURRENT_POSITION = PROJECT_ROOT / "config" / "current_position.yaml"
README = PROJECT_ROOT / "README.md"
MONTHLY_STRATEGY_CONFIGS = [
    PROJECT_ROOT / "config" / "strategy_reduced_equal_weight_monthly.yaml",
    PROJECT_ROOT / "config" / "strategy_equal_weight_monthly.yaml",
]
REBALANCE_TIMINGS = ["month_end", "month_start", "nth_trading_day", "day_of_month"]
REBALANCE_ROLLS = ["next", "previous", "nearest"]


def run_project_command(command: str) -> dict[str, object]:
    args = [str(PYTHON_EXE), "main.py", command]
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


def run_commands(commands: list[str]) -> list[dict[str, object]]:
    logs = []
    for command in commands:
        item = run_project_command(command)
        logs.append(item)
        if command == "qa-check" and int(item["returncode"]) != 0:
            break
        if int(item["returncode"]) != 0:
            break
    return logs


def open_local_path(path: Path) -> None:
    if not path.exists():
        st.error(f"路径不存在：{path}")
        return
    os.startfile(str(path))  # type: ignore[attr-defined]


def append_logs(logs: list[dict[str, object]]) -> None:
    st.session_state.setdefault("command_logs", [])
    st.session_state["command_logs"].extend(logs)


def load_rebalance_settings() -> dict[str, object]:
    path = MONTHLY_STRATEGY_CONFIGS[0]
    if not path.exists():
        return {
            "rebalance_timing": "month_end",
            "rebalance_day": 5,
            "rebalance_day_of_month": 15,
            "rebalance_roll": "next",
        }
    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    raw = config.get("strategy", {}) or {}
    return {
        "rebalance_timing": str(raw.get("rebalance_timing") or "month_end"),
        "rebalance_day": int(raw.get("rebalance_day") or 5),
        "rebalance_day_of_month": int(raw.get("rebalance_day_of_month") or 15),
        "rebalance_roll": str(raw.get("rebalance_roll") or "next"),
    }


def save_rebalance_settings(
    rebalance_timing: str,
    rebalance_day: int,
    rebalance_day_of_month: int,
    rebalance_roll: str,
) -> None:
    for path in MONTHLY_STRATEGY_CONFIGS:
        with path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        raw = config.setdefault("strategy", {})
        raw["frequency"] = "monthly"
        raw["rebalance_frequency"] = "monthly"
        raw["rebalance_timing"] = rebalance_timing
        raw["rebalance_day"] = int(rebalance_day) if rebalance_timing == "nth_trading_day" else None
        raw["rebalance_day_of_month"] = int(rebalance_day_of_month) if rebalance_timing == "day_of_month" else None
        raw["rebalance_roll"] = rebalance_roll
        path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


def render_rebalance_settings() -> None:
    settings = load_rebalance_settings()
    timing = str(settings["rebalance_timing"])
    if timing not in REBALANCE_TIMINGS:
        timing = "month_end"
    roll = str(settings["rebalance_roll"])
    if roll not in REBALANCE_ROLLS:
        roll = "next"

    with st.sidebar.form("rebalance_settings_form"):
        st.caption("调仓日期设置")
        selected_timing = st.selectbox(
            "信号日期规则",
            REBALANCE_TIMINGS,
            index=REBALANCE_TIMINGS.index(timing),
            help="会同步更新两个 monthly 策略配置。",
        )
        selected_day = int(settings["rebalance_day"])
        selected_day_of_month = int(settings["rebalance_day_of_month"])
        selected_roll = roll
        if selected_timing == "nth_trading_day":
            selected_day = int(st.number_input("每月第 N 个交易日", min_value=1, max_value=31, value=selected_day, step=1))
        if selected_timing == "day_of_month":
            selected_day_of_month = int(st.number_input("每月几号", min_value=1, max_value=31, value=selected_day_of_month, step=1))
            selected_roll = st.selectbox("非交易日处理", REBALANCE_ROLLS, index=REBALANCE_ROLLS.index(roll))
        submitted = st.form_submit_button("保存并生成信号", use_container_width=True)
    if submitted:
        save_rebalance_settings(selected_timing, selected_day, selected_day_of_month, selected_roll)
        append_logs(run_commands(["compare-signal"]))
        st.rerun()


def _safe_int(value: object) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def rebalance_context_note(row) -> str:
    if row.empty or str(row.get("rebalance_timing", "")) != "day_of_month":
        return ""
    signal_date = str(row.get("signal_date", "") or "")
    latest_data_date = str(row.get("latest_data_date", "") or "")
    day_of_month = _safe_int(row.get("rebalance_day_of_month"))
    roll = str(row.get("rebalance_roll", "next") or "next")
    if not signal_date or not latest_data_date or day_of_month is None:
        return ""
    try:
        signal_month = signal_date[:7]
        latest_month = latest_data_date[:7]
        latest_day = int(latest_data_date[-2:])
    except ValueError:
        return ""
    if signal_month != latest_month and latest_day < day_of_month:
        return (
            f"当前数据最新日期是 {latest_data_date}，还没到本月 {day_of_month} 号；"
            f"按 {roll} 规则不能用未来行情生成信号，所以当前显示最近一次已发生的信号日 {signal_date}。"
        )
    return ""


def render_sidebar() -> None:
    st.sidebar.header("命令操作")
    st.sidebar.caption("当前解释器：")
    st.sidebar.code(str(PYTHON_EXE), language="text")
    if ".venv" not in str(PYTHON_EXE).lower():
        st.sidebar.warning("当前 Streamlit 似乎不是由项目 .venv 启动。建议使用 start_web_ui.bat。")

    render_rebalance_settings()
    st.sidebar.divider()

    if st.sidebar.button("一键运行全部", use_container_width=True):
        append_logs(run_commands(["update-data", "qa-check", "compare-signal"]))
        st.rerun()
    if st.sidebar.button("更新数据", use_container_width=True):
        append_logs(run_commands(["update-data"]))
        st.rerun()
    if st.sidebar.button("质量检查", use_container_width=True):
        append_logs(run_commands(["qa-check"]))
        st.rerun()
    if st.sidebar.button("生成信号", use_container_width=True):
        append_logs(run_commands(["compare-signal"]))
        st.rerun()

    st.sidebar.divider()
    if st.sidebar.button("打开 output 文件夹", use_container_width=True):
        open_local_path(OUTPUT_DIR)
    if st.sidebar.button("打开 current_position.yaml", use_container_width=True):
        open_local_path(CURRENT_POSITION)
    if st.sidebar.button("打开 README.md", use_container_width=True):
        open_local_path(README)

    st.sidebar.info(
        "当前真实持仓来自：config/current_position.yaml。第一版请用按钮打开该文件后手动编辑。"
    )


def render_logs() -> None:
    logs = st.session_state.get("command_logs", [])
    if not logs:
        return
    st.subheader("命令日志")
    for idx, item in enumerate(reversed(logs), start=1):
        label = f"{item['command']} | 返回码 {item['returncode']}"
        with st.expander(label, expanded=idx == 1):
            st.code(str(item["command"]), language="powershell")
            if int(item["returncode"]) == 0:
                st.success(f"返回码：{item['returncode']}")
            else:
                st.error(f"返回码：{item['returncode']}")
            st.text_area("stdout", str(item["stdout"]) or "(无)", height=180, key=f"stdout_{idx}_{len(logs)}")
            st.text_area("stderr", str(item["stderr"]) or "(无)", height=120, key=f"stderr_{idx}_{len(logs)}")


def render_strategy_block(row, etf_names: dict[str, str], primary: bool = False) -> None:
    if row.empty:
        st.warning("未找到该策略信号，请先运行 compare-signal。")
        return

    status = str(row.get("strategy_status", "unknown"))
    status_badge(status)
    if primary:
        st.caption("主观察策略。优先看这里，再结合 qa-check 结果人工判断。")

    cols = st.columns(4)
    cols[0].metric("信号日期", str(row.get("signal_date", "N/A")))
    cols[1].metric("预计剩余现金", str(row.get("estimated_remaining_cash", "N/A")))
    cols[2].metric("当前持仓", str(row.get("current_positions", "N/A")))
    cols[3].metric("调仓规则", str(row.get("rebalance_rule", "N/A")))
    note = rebalance_context_note(row)
    if note:
        st.info(note)

    st.markdown("**系统目标持仓**")
    show_dataframe_or_empty(parse_target_table(row, etf_names))

    st.markdown("**建议卖出**")
    sells = str(row.get("suggested_sell", "无"))
    st.write("无" if sells in ("", "无") else sells)

    st.markdown("**建议买入**")
    show_dataframe_or_empty(parse_buy_table(row))

    st.markdown("**跳过买入**")
    show_dataframe_or_empty(parse_skip_table(row))

    st.markdown("**风险提示**")
    st.write(str(row.get("risk_note", "仅用于人工观察，不构成投资建议。")))


def main() -> None:
    st.set_page_config(page_title="A股ETF低频量化系统 v0.1-core", layout="wide")
    render_sidebar()

    st.title("A股ETF低频量化系统 v0.1-core")
    st.caption("本地量化研究与信号面板，不自动下单，不连接券商。")

    data = load_dashboard_data(PROJECT_ROOT)
    overview = data.overview

    st.subheader("顶部总览")
    cols = st.columns(4)
    with cols[0]:
        metric_card("当前信号日期", overview["signal_date"])
    with cols[1]:
        metric_card("数据最新日期", overview["latest_data_date"])
    with cols[2]:
        metric_card("当前真实现金", overview["current_cash"])
    with cols[3]:
        metric_card("是否允许小额观察", overview["allow_small_observation"])
    cols2 = st.columns(3)
    cols2[0].metric("当前真实持仓", overview["current_positions"])
    cols2[1].metric("主观察策略", overview["main_strategy"])
    cols2[2].metric("风险状态", overview["risk_status"])

    if overview.get("data_stale_after_close"):
        st.error(
            "现在已经过 A 股 15:00 收盘，但本地日线数据还没有更新到 "
            f"{overview.get('expected_data_date')}；当前最新数据只有 {overview['latest_data_date']}。"
            "不要把当前信号当作今日收盘后信号使用，请先等数据源更新后重新运行 update-data / compare-signal。"
        )
    if overview["signal_date"] != "N/A" and overview["latest_data_date"] != "N/A" and overview["signal_date"] != overview["latest_data_date"]:
        st.warning("当前信号日期不是最新交易日，请确认是否为月度调仓信号，或先运行 update-data / compare-signal。")
    if overview["allow_small_observation"] == "NO":
        st.error("质量检查未通过，不建议操作。")

    st.divider()
    st.subheader("主观察策略区")
    render_strategy_block(strategy_row(data.signals, MAIN_STRATEGY), data.etf_names, primary=True)

    st.divider()
    st.subheader("四策略对照区")
    for strategy_name in STRATEGY_ORDER:
        row = strategy_row(data.signals, strategy_name)
        status = str(row.get("strategy_status", "unknown")) if not row.empty else "missing"
        title = f"{strategy_name} - {status}"
        with st.expander(title, expanded=strategy_name == MAIN_STRATEGY):
            if strategy_name == "balanced":
                st.warning("research_only，不建议作为主跟随策略。")
            if strategy_name == "conservative":
                st.info("defensive_only，不作为主策略。")
            render_strategy_block(row, data.etf_names)

    render_logs()

    st.divider()
    st.caption("安全边界：不自动下单，不连接券商，不构成投资建议；balanced 只作研究，conservative 只作防守参考，不建议直接 1 万元满仓。")


if __name__ == "__main__":
    main()
