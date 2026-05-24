"""V2.1 backend integration orchestrator.

This layer reads or receives outputs from the seven project modules and writes a
stable controller-level snapshot for the future frontend. It only arbitrates and
serializes; it does not rewrite module formulas, entry thresholds, or QMT safety
rules.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from contracts.v21_schema import (
    DAILY_DECISION_FIELDS,
    ORDER_INTENT_FIELDS,
    PORTFOLIO_SNAPSHOT_FIELDS,
    RISK_GATE_FIELDS,
    SIGNAL_VERSION,
    TRAINING_SAMPLE_FIELDS,
    DailyDecision,
    PortfolioSnapshot,
    TrainingSample,
    V21OrderIntent,
    V21RiskGate,
)


OUTPUT_FILES = (
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

SAFE_EXECUTION_MODES = {"SIMULATION", "DRAFT", "MANUAL_CONFIRM"}
RISK_FREEZE_LEVELS = {"R3", "R4", "P0"}
ML_OBSERVATION_NOTICE = "仅供观察，不自动修改交易参数。"


def run_v21_backend_pipeline(
    *,
    output_dir: str | Path = "output",
    trade_date: str | pd.Timestamp | None = None,
    pre_selection_rows: Sequence[Mapping[str, Any]] | None = None,
    risk_gate: Mapping[str, Any] | Any | None = None,
    entry_rows: Sequence[Mapping[str, Any]] | None = None,
    exit_rows: Sequence[Mapping[str, Any]] | None = None,
    learning_rows: Sequence[Mapping[str, Any]] | None = None,
    historical_ml_rows: Sequence[Mapping[str, Any]] | None = None,
    holdings: Sequence[Mapping[str, Any]] | None = None,
    qmt_execution_available: bool | None = None,
    qmt_status: Mapping[str, Any] | None = None,
    account_total_asset: float | None = None,
) -> dict[str, Any]:
    """Build and write all V2.1 backend integration snapshots.

    Direct row arguments are primarily for tests and higher-level callers. When
    they are omitted, the orchestrator reads existing module outputs under
    ``output_dir`` and degrades gracefully when an optional source is absent.
    """

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = _now()
    warnings: list[str] = []
    fallback_reasons: list[str] = []

    pre_rows = _rows_or_csv(pre_selection_rows, out_dir / "pre_selection_result.csv", warnings, "pre_selection")
    entry = _rows_or_csv(entry_rows, out_dir / "entry_signal.csv", warnings, "entry")
    exits = _rows_or_csv(exit_rows, out_dir / "exit_signal.csv", warnings, "exit")
    learning = _rows_or_csv(learning_rows, out_dir / "learning_report.csv", warnings, "learning")
    historical = _resolve_historical_rows(historical_ml_rows, out_dir, warnings, fallback_reasons)
    portfolio_holdings = _resolve_holdings(holdings, warnings, fallback_reasons)
    qmt_available, qmt_note = _resolve_qmt_status(qmt_execution_available, qmt_status)
    if qmt_note:
        warnings.append(qmt_note)
        fallback_reasons.append(qmt_note)

    effective_date = _resolve_trade_date(trade_date, pre_rows, entry, exits, learning, risk_gate)
    v21_risk = _build_risk_gate(risk_gate, out_dir, effective_date, warnings, fallback_reasons)

    risk_level = str(v21_risk.risk_level or "R0").upper()
    if risk_level in RISK_FREEZE_LEVELS and not (v21_risk.freeze_entry or v21_risk.manual_takeover_required):
        v21_risk = V21RiskGate(**{**v21_risk.to_dict(), "freeze_entry": True})
        warnings.append("风险等级达到 R3/R4/P0，总控已强制冻结 entry。")

    market_state = _first_text(pre_rows, "market_state", default=_first_text(entry, "market_state", default="未知"))
    selected_rows = [row for row in pre_rows if _truthy(row.get("selected"))]
    selected_symbols = {_symbol(row.get("symbol") or row.get("etf_code") or row.get("code")) for row in selected_rows}
    selected_sectors = _unique(row.get("sector") for row in selected_rows)
    entry_by_symbol = {
        _symbol(row.get("symbol") or row.get("etf_code") or row.get("code")): row
        for row in entry
    }
    candidate_etfs = [
        _candidate_payload(
            row,
            entry_by_symbol.get(_symbol(row.get("symbol") or row.get("etf_code") or row.get("code")), {}),
        )
        for row in selected_rows
    ]

    portfolio = _build_portfolio_snapshot(
        holdings=portfolio_holdings,
        exit_rows=exits,
        pre_rows=pre_rows,
        trade_date=effective_date,
        account_total_asset=account_total_asset,
    )
    exit_actions = _build_exit_actions(exits)
    high_priority_exit = any(_is_high_priority_exit(row) for row in exits)
    entry_actions = _build_entry_actions(entry, selected_symbols, v21_risk, high_priority_exit)
    actual_buy_etfs = [item for item in entry_actions if item["actual_buy"]]
    ml_observation_status = _ml_observation_status(entry)
    ml_entry_advice = _ml_entry_advice_summary(entry, selected_symbols)
    portfolio_actions = _build_portfolio_actions(portfolio, exit_actions)

    learning_summary = [_learning_sample(row, entry, exits).to_dict() for row in learning]
    historical_summary = [_historical_sample(row).to_dict() for row in historical]

    order_intents = _build_order_intents(
        trade_date=effective_date,
        entry_actions=entry_actions,
        exit_actions=exit_actions,
        portfolio=portfolio,
        risk=v21_risk,
        qmt_available=qmt_available,
        qmt_note=qmt_note,
    )

    allow_entry = not v21_risk.freeze_entry and not v21_risk.manual_takeover_required and not high_priority_exit
    if high_priority_exit:
        fallback_reasons.append("exit 出现清仓或风险退出建议，总控暂停新增买入并优先处理退出。")
    if v21_risk.freeze_entry:
        fallback_reasons.append("风险门控冻结买入，entry 信号只保留为观察和解释，不进入实际买入。")
    if not historical:
        fallback_reasons.append("historical_ml 暂无可用摘要，总控已降级为空建议，不中断今日决策。")

    decision = DailyDecision(
        trade_date=effective_date,
        signal_version=SIGNAL_VERSION,
        market_state=market_state,
        risk_level=v21_risk.risk_level,
        risk_score=int(_number(v21_risk.risk_score)),
        allow_entry=allow_entry,
        freeze_entry=bool(v21_risk.freeze_entry),
        manual_takeover_required=bool(v21_risk.manual_takeover_required),
        selected_sectors=selected_sectors,
        ml_observation_status=ml_observation_status,
        ml_entry_advice=ml_entry_advice,
        candidate_etfs=candidate_etfs,
        actual_buy_etfs=actual_buy_etfs,
        entry_actions=entry_actions,
        exit_actions=exit_actions,
        portfolio_actions=portfolio_actions,
        learning_summary=learning_summary,
        historical_ml_summary=historical_summary,
        order_intent_summary=order_intents,
        explain=_decision_explain(market_state, v21_risk, candidate_etfs, actual_buy_etfs, exit_actions, high_priority_exit),
        warnings=_unique(warnings),
        fallback_reason=_join_reason(fallback_reasons),
        generated_at=generated_at,
    ).to_dict()

    _write_table(out_dir / "daily_decision_snapshot.csv", DAILY_DECISION_FIELDS, [decision])
    _write_json(out_dir / "daily_decision_snapshot.json", decision)
    _write_table(out_dir / "risk_gate_snapshot.csv", RISK_GATE_FIELDS, [v21_risk.to_dict()])
    _write_json(out_dir / "risk_gate_snapshot.json", v21_risk.to_dict())
    _write_table(out_dir / "portfolio_snapshot.csv", PORTFOLIO_SNAPSHOT_FIELDS, [item.to_dict() for item in portfolio])
    _write_json(out_dir / "portfolio_snapshot.json", [item.to_dict() for item in portfolio])
    _write_table(out_dir / "order_intent.csv", ORDER_INTENT_FIELDS, order_intents)
    _write_json(out_dir / "order_intent.json", order_intents)
    _write_table(out_dir / "learning_summary.csv", TRAINING_SAMPLE_FIELDS, learning_summary)
    _write_json(out_dir / "learning_summary.json", learning_summary)
    _write_table(out_dir / "historical_ml_summary.csv", TRAINING_SAMPLE_FIELDS, historical_summary)
    _write_json(out_dir / "historical_ml_summary.json", historical_summary)

    status = {
        "trade_date": effective_date,
        "signal_version": SIGNAL_VERSION,
        "status": "completed_with_fallback" if fallback_reasons else "completed",
        "generated_at": generated_at,
        "output_files": list(OUTPUT_FILES),
        "module_order": [
            "pre_selection",
            "risk_warning",
            "entry",
            "exit",
            "learning",
            "historical_ml",
            "qmt_execution",
        ],
        "priority_rules": [
            "RiskGate/P0/R4/R3 风险优先于所有买入信号。",
            "持仓真实风险和 exit 风险退出优先于新增买入。",
            "learning/historical_ml 只给建议，不自动修改交易参数。",
            "qmt_execution 只消费总控订单意图，不反向改变策略判断。",
        ],
        "fallback_reason": decision["fallback_reason"],
        "warnings": decision["warnings"],
        "strategy_logic_modified": False,
        "entry_threshold_modified": False,
        "live_auto_order_enabled": False,
        "qmt_execution_available": qmt_available,
    }
    _write_json(out_dir / "v21_backend_status.json", status)

    return {
        "daily_decision": decision,
        "risk_gate": v21_risk.to_dict(),
        "portfolio_snapshot": [item.to_dict() for item in portfolio],
        "order_intent": order_intents,
        "learning_summary": learning_summary,
        "historical_ml_summary": historical_summary,
        "status": status,
    }


def _rows_or_csv(
    rows: Sequence[Mapping[str, Any]] | None,
    path: Path,
    warnings: list[str],
    module_name: str,
) -> list[dict[str, Any]]:
    if rows is not None:
        return [dict(row) for row in rows]
    if not path.exists():
        warnings.append(f"{module_name} 暂无输出文件，已按空数据降级。")
        return []
    try:
        return pd.read_csv(path, dtype=str).fillna("").to_dict("records")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{module_name} 输出读取失败，已按空数据降级：{exc}")
        return []


def _resolve_historical_rows(
    rows: Sequence[Mapping[str, Any]] | None,
    out_dir: Path,
    warnings: list[str],
    fallback_reasons: list[str],
) -> list[dict[str, Any]]:
    if rows is not None:
        return [dict(row) for row in rows]
    candidates = (
        out_dir / "entry_calibration_suggestions.csv",
        out_dir / "historical_ml_summary.csv",
        Path("historical_ml") / "output" / "entry_calibration_suggestions.csv",
        Path("historical_ml") / "artifacts" / "entry_calibration_suggestions.csv",
    )
    for path in candidates:
        if path.exists():
            try:
                return pd.read_csv(path, dtype=str).fillna("").to_dict("records")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"historical_ml 摘要读取失败，已降级为空建议：{exc}")
                fallback_reasons.append("historical_ml 摘要读取失败，总控不中断。")
                return []
    warnings.append("historical_ml 摘要缺失，已降级为空建议。")
    return []


def _resolve_holdings(
    holdings: Sequence[Mapping[str, Any]] | None,
    warnings: list[str],
    fallback_reasons: list[str],
) -> list[dict[str, Any]]:
    if holdings is not None:
        return [dict(item) for item in holdings]
    path = Path("config") / "current_position.yaml"
    if not path.exists():
        warnings.append("当前持仓文件缺失，PortfolioSnapshot 按空持仓输出。")
        fallback_reasons.append("当前持仓文件缺失，持仓页和订单意图按空持仓降级。")
        return []
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if payload.get("current_empty"):
            return []
        return [dict(item) for item in payload.get("holdings", []) or [] if isinstance(item, Mapping)]
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"当前持仓读取失败，PortfolioSnapshot 按空持仓输出：{exc}")
        fallback_reasons.append("当前持仓读取失败，持仓相关输出已降级。")
        return []


def _resolve_qmt_status(
    qmt_execution_available: bool | None,
    qmt_status: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    if qmt_execution_available is not None:
        return bool(qmt_execution_available), "" if qmt_execution_available else "qmt_execution 缺失或不可用，仅生成订单草稿和人工确认说明。"
    if qmt_status:
        return True, ""
    snapshot = Path("runtime") / "qmt_execution" / "qmt_readonly_snapshot.json"
    if snapshot.exists():
        return True, ""
    return False, "qmt_execution 只读快照缺失，总控不提交订单，仅输出 DRAFT/MANUAL_CONFIRM 草稿。"


def _build_risk_gate(
    raw_gate: Mapping[str, Any] | Any | None,
    out_dir: Path,
    trade_date: str,
    warnings: list[str],
    fallback_reasons: list[str],
) -> V21RiskGate:
    payload: dict[str, Any] = {}
    source = "risk_warning"
    if raw_gate is not None:
        payload = raw_gate.to_dict() if hasattr(raw_gate, "to_dict") else dict(raw_gate)
        source = str(payload.get("source") or source)
    elif (out_dir / "risk_gate.json").exists():
        try:
            payload = json.loads((out_dir / "risk_gate.json").read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"RiskGate JSON 读取失败，已按 R0 降级：{exc}")
            fallback_reasons.append("RiskGate 读取失败，总控按 R0 降级但保留 warning。")
    elif (out_dir / "risk_warning_next_day.csv").exists():
        rows = _rows_or_csv(None, out_dir / "risk_warning_next_day.csv", warnings, "risk_warning")
        payload = rows[0] if rows else {}
    else:
        warnings.append("risk_warning 输出缺失，总控按 R0 保守降级。")
        fallback_reasons.append("risk_warning 输出缺失，RiskGate 按 R0 降级。")

    risk_events = payload.get("risk_events") or payload.get("active_events") or []
    if isinstance(risk_events, str):
        try:
            risk_events = json.loads(risk_events)
        except json.JSONDecodeError:
            risk_events = []
    affected_etfs = _unique(
        asset
        for event in risk_events if isinstance(event, Mapping)
        for asset in _as_list(event.get("affected_assets") or event.get("affected_etfs"))
    )
    level = str(payload.get("risk_level") or "R0").upper()
    freeze = _bool(payload.get("freeze_entry")) or level in RISK_FREEZE_LEVELS
    manual = _bool(payload.get("manual_takeover_required")) or level in {"R4", "P0"}
    return V21RiskGate(
        trade_date=str(payload.get("trade_date") or payload.get("risk_date") or trade_date),
        risk_level=level,
        risk_score=int(_number(payload.get("risk_score"), 0)),
        freeze_entry=freeze,
        equity_cap_override=float(_number(payload.get("equity_cap_override"), 0.0 if freeze else 1.0)),
        manual_takeover_required=manual,
        affected_sectors=_as_list(payload.get("affected_sectors")),
        affected_etfs=affected_etfs,
        risk_events=[dict(item) for item in risk_events if isinstance(item, Mapping)],
        explain=str(payload.get("explain") or "风险门控无详细说明，按当前风险等级执行。"),
        source=source,
    )


def _build_entry_actions(
    entry_rows: Sequence[Mapping[str, Any]],
    selected_symbols: set[str],
    risk: V21RiskGate,
    high_priority_exit: bool,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in entry_rows:
        symbol = _symbol(row.get("symbol") or row.get("etf_code") or row.get("code"))
        action = str(row.get("buy_action") or row.get("entry_action") or row.get("action") or "")
        target_weight = _ratio(row.get("position_size") or row.get("target_weight") or row.get("suggested_weight"))
        intended_buy = symbol in selected_symbols and _is_buy_action(action) and target_weight > 0
        actionable = intended_buy
        block_reason = ""
        if intended_buy and risk.freeze_entry:
            actionable = False
            block_reason = "RiskGate 冻结买入，entry 不进入实际买入。"
        elif intended_buy and risk.manual_takeover_required:
            actionable = False
            block_reason = "RiskGate 要求人工接管，entry 不进入自动买入。"
        elif intended_buy and high_priority_exit:
            actionable = False
            block_reason = "exit 清仓或风险退出优先，暂停新增买入。"
        actions.append(
            {
                "etf_code": symbol,
                "etf_name": str(row.get("name") or row.get("etf_name") or ""),
                "entry_action": action,
                "target_weight": target_weight,
                "confidence": _number(row.get("confidence"), ""),
                "ml_entry_advice": str(row.get("ml_entry_advice") or "无ML建议"),
                "ml_confidence": _number(row.get("ml_confidence"), 0),
                "ml_reason": str(row.get("ml_reason") or "未找到历史校准建议，维持原 entry 判断。"),
                "ml_action_suggestion": str(row.get("ml_action_suggestion") or "NO_ML"),
                "ml_observation_notice": ML_OBSERVATION_NOTICE,
                "intended_buy": intended_buy,
                "actual_buy": actionable,
                "block_reason": block_reason,
                "explain": str(row.get("entry_reason") or row.get("explain") or "entry 输出无额外说明。"),
                "source_signal": str(row.get("source_file") or "entry_signal.csv"),
            }
        )
    return actions


def _ml_advice_active(row: Mapping[str, Any]) -> bool:
    action = str(row.get("ml_action_suggestion") or "").strip().upper()
    advice = str(row.get("ml_entry_advice") or "").strip()
    try:
        confidence = float(row.get("ml_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return bool((action and action != "NO_ML") or confidence > 0 or (advice and advice != "无ML建议"))


def _ml_observation_status(entry_rows: Sequence[Mapping[str, Any]]) -> str:
    if not entry_rows:
        return f"ML 观察模式未启用（无 entry 输出；{ML_OBSERVATION_NOTICE}）"
    if not all("ml_entry_advice" in row for row in entry_rows):
        return f"ML 观察模式未启用（entry 输出缺少 ML 字段；{ML_OBSERVATION_NOTICE}）"
    if any(_ml_advice_active(row) for row in entry_rows):
        return f"ML 观察模式已启用（{ML_OBSERVATION_NOTICE}）"
    return f"ML 观察模式已启用（当前无ML建议，维持原 entry 判断；{ML_OBSERVATION_NOTICE}）"


def _ml_entry_advice_summary(entry_rows: Sequence[Mapping[str, Any]], selected_symbols: set[str]) -> str:
    items: list[str] = []
    for row in entry_rows:
        symbol = _symbol(row.get("symbol") or row.get("etf_code") or row.get("code"))
        if symbol not in selected_symbols:
            continue
        items.append(
            f"{symbol}:{row.get('ml_entry_advice', '无ML建议')}"
            f"（置信度{row.get('ml_confidence', 0)}，动作建议{row.get('ml_action_suggestion', 'NO_ML')}；{ML_OBSERVATION_NOTICE}）"
        )
    return " | ".join(items) if items else f"无ML建议（{ML_OBSERVATION_NOTICE}）"


def _build_exit_actions(exit_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in exit_rows:
        action = str(row.get("sell_action") or row.get("exit_action") or row.get("action") or "")
        reduce_ratio = _ratio(row.get("reduce_ratio") or row.get("delta_weight"))
        actionable = _is_exit_action(action, reduce_ratio)
        actions.append(
            {
                "etf_code": _symbol(row.get("symbol") or row.get("etf_code") or row.get("code")),
                "etf_name": str(row.get("name") or row.get("etf_name") or ""),
                "exit_action": action,
                "reduce_ratio": reduce_ratio,
                "actual_exit": actionable,
                "explain": str(row.get("exit_reason") or row.get("explain") or "exit 输出无额外说明。"),
                "source_signal": str(row.get("source_file") or "exit_signal.csv"),
            }
        )
    return actions


def _build_portfolio_snapshot(
    *,
    holdings: Sequence[Mapping[str, Any]],
    exit_rows: Sequence[Mapping[str, Any]],
    pre_rows: Sequence[Mapping[str, Any]],
    trade_date: str,
    account_total_asset: float | None,
) -> list[PortfolioSnapshot]:
    exit_by_symbol = {_symbol(row.get("symbol") or row.get("etf_code") or row.get("code")): row for row in exit_rows}
    pre_by_symbol = {_symbol(row.get("symbol") or row.get("etf_code") or row.get("code")): row for row in pre_rows}
    values: list[float] = []
    normalized: list[dict[str, Any]] = []
    for item in holdings:
        symbol = _symbol(item.get("symbol") or item.get("etf_code") or item.get("code"))
        shares = _number(item.get("shares") or item.get("quantity"), 0.0)
        cost = _number(item.get("cost_price") or item.get("average_buy_price"), 0.0)
        exit_row = exit_by_symbol.get(symbol, {})
        current_price = _number(item.get("current_price") or item.get("last_price") or exit_row.get("sell_price") or cost, cost)
        value = shares * current_price if shares and current_price else 0.0
        values.append(value)
        normalized.append({**dict(item), "_symbol": symbol, "_shares": shares, "_cost": cost, "_current_price": current_price, "_value": value})
    total_asset = account_total_asset if account_total_asset and account_total_asset > 0 else sum(values)
    snapshots: list[PortfolioSnapshot] = []
    for item in normalized:
        symbol = item["_symbol"]
        exit_row = exit_by_symbol.get(symbol, {})
        pre_row = pre_by_symbol.get(symbol, {})
        current_price = item["_current_price"]
        cost = item["_cost"]
        shares = item["_shares"]
        value = item["_value"]
        pnl = (current_price - cost) * shares if current_price and cost and shares else ""
        pnl_pct = (current_price / cost - 1.0) if current_price and cost else ""
        current_weight = value / total_asset if total_asset else _ratio(item.get("current_weight"))
        exit_action = str(exit_row.get("sell_action") or exit_row.get("exit_action") or "")
        snapshots.append(
            PortfolioSnapshot(
                trade_date=trade_date,
                etf_code=symbol,
                etf_name=str(item.get("name") or item.get("etf_name") or exit_row.get("name") or symbol),
                current_weight=round(current_weight, 6),
                target_weight=_ratio(item.get("target_weight")),
                cost_price=cost or "",
                current_price=current_price or "",
                pnl=round(pnl, 4) if isinstance(pnl, (int, float)) else "",
                pnl_pct=round(pnl_pct, 6) if isinstance(pnl_pct, (int, float)) else "",
                holding_days=item.get("holding_days") or item.get("holding_days_count") or "",
                sector=str(item.get("sector") or pre_row.get("sector") or ""),
                risk_status="需要退出关注" if _is_exit_action(exit_action, _ratio(exit_row.get("reduce_ratio"))) else "正常跟踪",
                exit_action=exit_action,
                explain=str(exit_row.get("exit_reason") or "持仓纳入 V2.1 总控快照。"),
            )
        )
    return snapshots


def _build_portfolio_actions(
    portfolio: Sequence[PortfolioSnapshot],
    exit_actions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    exit_by_symbol = {item.get("etf_code"): item for item in exit_actions}
    actions: list[dict[str, Any]] = []
    for item in portfolio:
        exit_action = exit_by_symbol.get(item.etf_code, {})
        actions.append(
            {
                "etf_code": item.etf_code,
                "etf_name": item.etf_name,
                "current_weight": item.current_weight,
                "target_weight": item.target_weight,
                "action": exit_action.get("exit_action") or "继续持仓跟踪",
                "explain": exit_action.get("explain") or item.explain,
            }
        )
    return actions


def _build_order_intents(
    *,
    trade_date: str,
    entry_actions: Sequence[Mapping[str, Any]],
    exit_actions: Sequence[Mapping[str, Any]],
    portfolio: Sequence[PortfolioSnapshot],
    risk: V21RiskGate,
    qmt_available: bool,
    qmt_note: str,
) -> list[dict[str, Any]]:
    portfolio_by_symbol = {item.etf_code: item for item in portfolio}
    intents: list[dict[str, Any]] = []
    manual = True
    risk_block = ""
    if risk.freeze_entry:
        risk_block = "RiskGate 冻结买入，买入侧不得生成可执行订单。"
    if risk.manual_takeover_required:
        risk_block = "RiskGate 要求人工接管，所有订单意图必须人工确认。"

    for exit_action in exit_actions:
        if not exit_action.get("actual_exit"):
            continue
        symbol = str(exit_action.get("etf_code") or "")
        holding = portfolio_by_symbol.get(symbol)
        current_weight = holding.current_weight if holding else 0.0
        reduce_ratio = _ratio(exit_action.get("reduce_ratio")) or 1.0
        delta = -min(current_weight, current_weight * reduce_ratio if current_weight else reduce_ratio)
        intents.append(
            V21OrderIntent(
                trade_date=trade_date,
                etf_code=symbol,
                etf_name=str(exit_action.get("etf_name") or (holding.etf_name if holding else "")),
                action="DRAFT_EXIT",
                side="SELL",
                target_weight=max(current_weight + delta, 0.0),
                current_weight=current_weight,
                delta_weight=round(delta, 6),
                estimated_price=holding.current_price if holding else "",
                estimated_amount="",
                order_type="LIMIT",
                execution_mode="MANUAL_CONFIRM",
                requires_manual_confirm=manual,
                risk_check_passed=True,
                risk_block_reason="",
                source_signal=str(exit_action.get("source_signal") or "exit_signal.csv"),
                explain=f"exit 优先生成卖出草稿。{exit_action.get('explain') or ''}",
            ).to_dict()
        )

    for entry in entry_actions:
        symbol = str(entry.get("etf_code") or "")
        holding = portfolio_by_symbol.get(symbol)
        current_weight = holding.current_weight if holding else 0.0
        target_weight = _ratio(entry.get("target_weight"))
        passed = bool(entry.get("actual_buy")) and not risk_block
        block_reason = str(entry.get("block_reason") or risk_block or "")
        if not entry.get("intended_buy") and not passed:
            continue
        if not passed and not block_reason:
            continue
        execution_mode = "DRAFT" if passed else "MANUAL_CONFIRM"
        if execution_mode not in SAFE_EXECUTION_MODES:
            execution_mode = "DRAFT"
        explain = str(entry.get("explain") or "")
        if not qmt_available:
            explain = f"{explain} qmt_execution 不可用，当前仅保留订单草稿和人工确认说明。".strip()
        intents.append(
            V21OrderIntent(
                trade_date=trade_date,
                etf_code=symbol,
                etf_name=str(entry.get("etf_name") or ""),
                action="DRAFT_BUY" if passed else "BLOCKED_BUY",
                side="BUY",
                target_weight=target_weight if passed else current_weight,
                current_weight=current_weight,
                delta_weight=round(max(target_weight - current_weight, 0.0), 6) if passed else 0.0,
                estimated_price="",
                estimated_amount="",
                order_type="LIMIT",
                execution_mode=execution_mode,
                requires_manual_confirm=True,
                risk_check_passed=passed,
                risk_block_reason=block_reason,
                source_signal=str(entry.get("source_signal") or "entry_signal.csv"),
                explain=explain or qmt_note or "买入订单意图由 V2.1 总控草稿生成。",
            ).to_dict()
        )
    if not intents and qmt_note:
        intents.append(
            V21OrderIntent(
                trade_date=trade_date,
                etf_code="",
                action="NO_ORDER",
                side="",
                execution_mode="DRAFT",
                requires_manual_confirm=True,
                risk_check_passed=False,
                risk_block_reason=qmt_note,
                source_signal="v21_orchestrator",
                explain="没有可执行买卖动作，总控仅写出 QMT 降级说明。",
            ).to_dict()
        )
    return intents


def _learning_sample(
    row: Mapping[str, Any],
    entry_rows: Sequence[Mapping[str, Any]],
    exit_rows: Sequence[Mapping[str, Any]],
) -> TrainingSample:
    symbol = _symbol(row.get("symbol") or row.get("etf_code") or row.get("code"))
    entry = _find_symbol(entry_rows, symbol)
    exit_row = _find_symbol(exit_rows, symbol)
    return TrainingSample(
        trade_date=str(row.get("trade_date") or ""),
        etf_code=symbol,
        etf_name=str(row.get("name") or row.get("etf_name") or ""),
        signal_type="learning",
        market_state=str(entry.get("market_state") or exit_row.get("market_state") or row.get("market_state") or ""),
        sector=str(row.get("sector") or ""),
        entry_action=str(entry.get("buy_action") or row.get("entry_action") or ""),
        exit_action=str(exit_row.get("sell_action") or row.get("exit_action") or ""),
        confidence=entry.get("confidence") or row.get("confidence") or "",
        ml_entry_advice=str(entry.get("ml_entry_advice") or row.get("ml_entry_advice") or "无ML建议"),
        ml_confidence=entry.get("ml_confidence") or row.get("ml_confidence") or 0,
        ml_reason=str(entry.get("ml_reason") or row.get("ml_reason") or "未找到历史校准建议，维持原 entry 判断。"),
        ml_action_suggestion=str(entry.get("ml_action_suggestion") or row.get("ml_action_suggestion") or "NO_ML"),
        trend_maturity=_extract_between(str(entry.get("entry_reason") or ""), "趋势成熟度：", "；"),
        entry_quality=_extract_between(str(entry.get("entry_reason") or ""), "买点质量：", "；"),
        post_924_regime=_post_924(row.get("trade_date")),
        ret_1d=row.get("ret_1d") or "",
        ret_3d=row.get("ret_3d") or "",
        ret_5d=row.get("ret_5d") or "",
        ret_10d=row.get("return_pct") or row.get("ret_10d") or "",
        hindsight_label=str(row.get("hindsight_label") or ""),
        failure_type=str(row.get("failure_attribution") or row.get("failure_type") or ""),
        calibration_suggestion=str(row.get("adjustment") or row.get("calibration_suggestion") or ""),
        explain=str(row.get("lesson") or row.get("explain") or ""),
    )


def _historical_sample(row: Mapping[str, Any]) -> TrainingSample:
    return TrainingSample(
        trade_date=str(row.get("trade_date") or row.get("signal_date") or ""),
        etf_code=_symbol(row.get("etf_code") or row.get("code") or row.get("symbol")),
        etf_name=str(row.get("etf_name") or row.get("name") or ""),
        signal_type=str(row.get("signal_type") or "historical_ml"),
        market_state=str(row.get("market_state") or row.get("affected_market_state") or ""),
        sector=str(row.get("sector") or row.get("affected_sector_state") or ""),
        entry_action=str(row.get("entry_action") or row.get("was_bought") or ""),
        exit_action=str(row.get("exit_action") or ""),
        confidence=row.get("confidence") or "",
        ml_entry_advice=str(row.get("ml_entry_advice") or "无ML建议"),
        ml_confidence=row.get("ml_confidence") or 0,
        ml_reason=str(row.get("ml_reason") or "未找到历史校准建议，维持原 entry 判断。"),
        ml_action_suggestion=str(row.get("ml_action_suggestion") or "NO_ML"),
        trend_maturity=str(row.get("trend_maturity") or ""),
        entry_quality=str(row.get("entry_quality") or row.get("parameter_area") or ""),
        post_924_regime=_post_924(row.get("trade_date") or row.get("signal_date")),
        ret_1d=row.get("ret_1d") or row.get("future_return_1d") or "",
        ret_3d=row.get("ret_3d") or row.get("future_return_3d") or "",
        ret_5d=row.get("ret_5d") or row.get("future_return_5d") or "",
        ret_10d=row.get("ret_10d") or row.get("future_return_10d") or row.get("avg_future_return_10d") or "",
        hindsight_label=str(row.get("hindsight_label") or row.get("auto_label") or ""),
        failure_type=str(row.get("failure_type") or row.get("review_reason") or ""),
        calibration_suggestion=str(row.get("calibration_suggestion") or row.get("suggested_action") or row.get("notes") or ""),
        explain=str(row.get("explain") or row.get("notes") or row.get("suggestion_id") or ""),
    )


def _write_table(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field, "")) for field in fields})


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _resolve_trade_date(
    explicit: str | pd.Timestamp | None,
    *sources: Any,
) -> str:
    if explicit is not None:
        return str(pd.Timestamp(explicit).date())
    for source in sources:
        if source is None:
            continue
        if isinstance(source, Mapping):
            value = source.get("trade_date") or source.get("risk_date")
            if value:
                return str(value)[:10]
        if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
            value = _first_text(source, "trade_date", default="")
            if value:
                return value[:10]
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _decision_explain(
    market_state: str,
    risk: V21RiskGate,
    candidates: Sequence[Mapping[str, Any]],
    buys: Sequence[Mapping[str, Any]],
    exits: Sequence[Mapping[str, Any]],
    high_priority_exit: bool,
) -> str:
    parts = [
        f"今日市场状态为{market_state or '未知'}。",
        f"风险等级为{risk.risk_level}，风险分数{risk.risk_score}。",
    ]
    if risk.freeze_entry:
        parts.append("风险门控已冻结买入，entry 不得进入实际买入。")
    elif high_priority_exit:
        parts.append("exit 出现清仓或风险退出建议，总控优先处理退出并暂停新增买入。")
    else:
        parts.append("风险门控未冻结买入，entry 可在候选池内形成订单草稿。")
    parts.append(f"候选 ETF 数量为{len(candidates)}，实际买入建议数量为{len(buys)}，退出动作数量为{len([item for item in exits if item.get('actual_exit')])}。")
    parts.append("learning 与 historical_ml 仅提供复盘和校准建议，不自动修改当日交易参数。")
    return "".join(parts)


def _candidate_payload(row: Mapping[str, Any], entry_row: Mapping[str, Any] | None = None) -> dict[str, Any]:
    entry_row = entry_row or {}
    return {
        "etf_code": _symbol(row.get("symbol") or row.get("etf_code") or row.get("code")),
        "etf_name": str(row.get("name") or row.get("etf_name") or ""),
        "sector": str(row.get("sector") or ""),
        "rank": row.get("rank") or "",
        "score": row.get("score") or "",
        "ml_entry_advice": str(entry_row.get("ml_entry_advice") or "无ML建议"),
        "ml_confidence": _number(entry_row.get("ml_confidence"), 0),
        "ml_reason": str(entry_row.get("ml_reason") or "未找到历史校准建议，维持原 entry 判断。"),
        "ml_action_suggestion": str(entry_row.get("ml_action_suggestion") or "NO_ML"),
        "ml_observation_notice": ML_OBSERVATION_NOTICE,
        "explain": str(row.get("reason") or row.get("explain") or ""),
    }


def _is_buy_action(action: str) -> bool:
    text = action.strip().lower()
    if not text:
        return False
    blocked = ("观察", "等待", "禁止", "冻结", "暂停", "watch", "wait", "forbid", "blocked")
    if any(token in text for token in blocked):
        return False
    return any(token in text for token in ("买入", "加仓", "buy", "probe", "standard", "add"))


def _is_exit_action(action: str, reduce_ratio: float = 0.0) -> bool:
    text = action.strip().lower()
    if not text:
        return reduce_ratio > 0
    if any(token in text for token in ("持有", "观察", "hold", "watch")) and not any(token in text for token in ("减", "卖", "清", "sell", "reduce", "clear", "exit")):
        return False
    return reduce_ratio > 0 or any(token in text for token in ("清仓", "减仓", "卖出", "退出", "止损", "sell", "reduce", "clear", "exit", "stop"))


def _is_high_priority_exit(row: Mapping[str, Any]) -> bool:
    text = str(row.get("sell_action") or row.get("exit_action") or row.get("action") or "").lower()
    reason = str(row.get("exit_reason") or row.get("explain") or "").lower()
    return any(token in text + reason for token in ("清仓", "风险退出", "止损", "clear", "stop", "risk"))


def _first_text(rows: Sequence[Mapping[str, Any]], key: str, *, default: str = "") -> str:
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _find_symbol(rows: Sequence[Mapping[str, Any]], symbol: str) -> dict[str, Any]:
    for row in rows:
        if _symbol(row.get("symbol") or row.get("etf_code") or row.get("code")) == symbol:
            return dict(row)
    return {}


def _symbol(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text.isdigit() else text


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是", "入选", "selected"}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _number(value: Any, default: Any = 0.0) -> Any:
    if value in (None, ""):
        return default
    try:
        result = float(str(value).strip().rstrip("%"))
        if str(value).strip().endswith("%"):
            result /= 100.0
        if not math.isfinite(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _ratio(value: Any) -> float:
    number = _number(value, 0.0)
    return number / 100.0 if abs(number) > 1 else number


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                return _as_list(parsed)
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in text.replace("、", ",").split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _unique(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in _as_list(value):
            if item and item not in result:
                result.append(item)
    return result


def _join_reason(reasons: Sequence[str]) -> str:
    unique = _unique(reasons)
    return "；".join(unique) if unique else "无"


def _extract_between(text: str, prefix: str, suffix: str) -> str:
    if prefix not in text:
        return ""
    rest = text.split(prefix, 1)[1]
    return rest.split(suffix, 1)[0].strip()


def _post_924(value: Any) -> bool:
    try:
        return pd.Timestamp(value) >= pd.Timestamp("2024-09-24")
    except Exception:  # noqa: BLE001
        return True


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


if __name__ == "__main__":
    run_v21_backend_pipeline()
