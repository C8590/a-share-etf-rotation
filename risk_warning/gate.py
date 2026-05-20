from __future__ import annotations

import copy
import json
from typing import Any, Mapping, MutableMapping

from .models import RiskGate


BUY_ACTION_KEYWORDS = (
    "买入",
    "加仓",
    "新开仓",
    "probe_buy",
    "standard_buy",
    "add_buy",
    "buy",
)
SELL_ACTION_KEYWORDS = (
    "卖出",
    "减仓",
    "止损",
    "退出",
    "清仓",
    "sell",
    "reduce",
    "exit",
    "stop_loss",
)
EMPTY_TEXT = {"无", "none", "None", "N/A", "nan", ""}
WEIGHT_KEYS = (
    "position_size",
    "target_weight",
    "suggested_weight",
    "建议仓位",
    "目标权重",
    "建议权重",
)
BUY_PLAN_KEYS = ("buy_plan", "intraday_execution_plan")
BUY_ACTION_KEYS = ("buy_action", "entry_action", "action", "交易动作")


def gate_from_level(risk_level: str) -> dict[str, Any]:
    level = str(risk_level or "R0").upper()
    return {
        "R0": {"freeze_entry": False, "equity_cap_override": 1.00, "require_manual_review": False, "manual_takeover_required": False},
        "R1": {"freeze_entry": False, "equity_cap_override": 1.00, "require_manual_review": False, "manual_takeover_required": False},
        "R2": {"freeze_entry": False, "equity_cap_override": 0.60, "require_manual_review": False, "manual_takeover_required": False},
        "R3": {"freeze_entry": True, "equity_cap_override": 0.30, "require_manual_review": True, "manual_takeover_required": False},
        "R4": {"freeze_entry": True, "equity_cap_override": 0.00, "require_manual_review": True, "manual_takeover_required": True},
    }.get(level, {"freeze_entry": False, "equity_cap_override": 1.00, "require_manual_review": False, "manual_takeover_required": False})


def apply_risk_gate(raw_signal: Any, risk_gate: Mapping[str, Any] | RiskGate) -> Any:
    """Apply the P0 brake to entry-side signal rows without blocking sell-side actions."""

    gate = risk_gate.to_dict() if isinstance(risk_gate, RiskGate) else dict(risk_gate)
    if isinstance(raw_signal, list):
        rows = [_apply_to_row(row, gate) if isinstance(row, Mapping) else row for row in raw_signal]
        if str(gate.get("risk_level") or "R0").upper() == "R2":
            _cap_entry_rows_total(rows, float(gate.get("equity_cap_override", 1.0) or 0.0))
        return rows
    if isinstance(raw_signal, Mapping):
        return _apply_to_row(raw_signal, gate)
    return raw_signal


def _apply_to_row(row: Mapping[str, Any], gate: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(row))
    level = str(gate.get("risk_level") or "R0").upper()
    freeze = bool(gate.get("freeze_entry"))
    cap = float(gate.get("equity_cap_override", 1.0) or 0.0)
    explain = _risk_action_text(level)

    result.update(
        {
            "risk_warning_level": level,
            "risk_warning_score": gate.get("risk_score", 0),
            "risk_freeze_entry": "是" if freeze else "否",
            "risk_equity_cap_override": cap,
            "risk_require_manual_review": "是" if gate.get("require_manual_review") else "否",
            "risk_manual_takeover_required": "是" if gate.get("manual_takeover_required") else "否",
            "risk_affected_sectors": "、".join(gate.get("affected_sectors", []) or []),
            "risk_warning_explain": gate.get("explain", ""),
        }
    )

    if level in {"R0", "R1"}:
        return result

    if level == "R2":
        _scale_entry_fields(result, cap)
        result["risk_gate_action"] = "风险升高，建议降低仓位并提高买入门槛。"
        return result

    result["risk_gate_action"] = explain
    _freeze_buy_fields(result, explain)
    return result


def _scale_entry_fields(row: MutableMapping[str, Any], cap: float) -> None:
    for key in WEIGHT_KEYS:
        if key in row and _is_buy_context(row):
            row[key] = _scale_number(row.get(key), cap)
    for plan_key in BUY_PLAN_KEYS:
        if plan_key in row:
            row[plan_key] = _cap_plan_json_total(row.get(plan_key), cap)


def _freeze_buy_fields(row: MutableMapping[str, Any], message: str) -> None:
    buy_context = _is_buy_context(row)
    for key in ("suggested_buy", "v2_actual_buy_etfs", "target_symbols"):
        if key in row and _has_buy_text(row.get(key)):
            row[key] = "无"
    for key in ("buy_share_advice", "skipped_buy_advice", "no_action_reason", "operation_reason"):
        if key in row:
            row[key] = message
    for key in BUY_ACTION_KEYS:
        if key in row and _is_buy_action(row.get(key)):
            row[key] = message
    for key in WEIGHT_KEYS:
        if key in row and buy_context:
            row[key] = _replace_number(row.get(key), 0.0)
    for plan_key in BUY_PLAN_KEYS:
        if plan_key in row:
            row[plan_key] = _freeze_plan_json(row.get(plan_key), message)


def _cap_entry_rows_total(rows: list[Any], cap: float) -> None:
    buy_rows: list[MutableMapping[str, Any]] = [
        row for row in rows if isinstance(row, MutableMapping) and _is_buy_context(row)
    ]
    weighted: list[tuple[MutableMapping[str, Any], str, float]] = []
    for row in buy_rows:
        key, value = _first_weight(row)
        if key and value is not None:
            weighted.append((row, key, value))
    total = sum(value for _, _, value in weighted)
    if total <= cap or total <= 0:
        return
    ratio = cap / total
    for row, key, value in weighted:
        row[key] = _replace_number(row.get(key), round(value * ratio, 4))


def _cap_plan_json_total(value: Any, cap: float) -> Any:
    plan = _load_plan(value)
    if plan is None:
        return value
    weighted: list[tuple[MutableMapping[str, Any], str, float]] = []
    for item in plan:
        if isinstance(item, MutableMapping) and _plan_item_is_buy(item):
            key, number = _first_weight(item)
            if key and number is not None:
                weighted.append((item, key, number))
    total = sum(number for _, _, number in weighted)
    if total > cap and total > 0:
        ratio = cap / total
        for item, key, number in weighted:
            item[key] = _replace_number(item.get(key), round(number * ratio, 4))
    else:
        for item, key, number in weighted:
            item[key] = _replace_number(item.get(key), min(number, cap))
    return json.dumps(plan, ensure_ascii=False)


def _freeze_plan_json(value: Any, message: str) -> Any:
    plan = _load_plan(value)
    if plan is None:
        return value
    frozen: list[dict[str, Any]] = []
    for item in plan:
        if not isinstance(item, Mapping):
            continue
        new_item = dict(item)
        if _plan_item_is_buy(new_item):
            for key in BUY_ACTION_KEYS:
                if key in new_item:
                    new_item[key] = message
            for key in (*WEIGHT_KEYS, "建议买入份额", "预计买入金额", "今日建议买入金额"):
                if key in new_item:
                    new_item[key] = _replace_number(new_item.get(key), 0.0)
            new_item["risk_gate_reason"] = message
        frozen.append(new_item)
    return json.dumps(frozen, ensure_ascii=False)


def _risk_action_text(level: str) -> str:
    if level == "R4":
        return "P0 风险预警，entry 已冻结，建议人工接管。"
    return "风险高，暂停普通买入，等待人工复核。"


def _is_buy_context(row: Mapping[str, Any]) -> bool:
    return any(_is_buy_action(row.get(key)) for key in (*BUY_ACTION_KEYS, "final_signal")) or _has_buy_text(
        row.get("suggested_buy")
    )


def _plan_item_is_buy(item: Mapping[str, Any]) -> bool:
    if any(_is_buy_action(item.get(key)) for key in BUY_ACTION_KEYS):
        return True
    return any(key in item for key in ("建议买入份额", "预计买入金额", "今日建议买入金额", "buy_price"))


def _is_buy_action(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if any(keyword in text for keyword in SELL_ACTION_KEYWORDS):
        return False
    return any(keyword.lower() in text for keyword in BUY_ACTION_KEYWORDS)


def _has_buy_text(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in EMPTY_TEXT)


def _scale_number(value: Any, cap: float) -> Any:
    number = _number_value(value)
    if number is None:
        return value
    return _replace_number(value, min(max(number, 0.0), cap))


def _first_weight(row: Mapping[str, Any]) -> tuple[str, float | None]:
    for key in WEIGHT_KEYS:
        if key in row:
            number = _number_value(row.get(key))
            if number is not None:
                return key, number
    return "", None


def _number_value(value: Any) -> float | None:
    if value in ("", None):
        return None
    text = str(value).strip()
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100
        return float(text)
    except (TypeError, ValueError):
        return None


def _replace_number(original: Any, value: float) -> Any:
    text = str(original or "").strip()
    if text.endswith("%"):
        return f"{value:.0%}"
    if isinstance(original, int):
        return int(round(value))
    return round(float(value), 4)


def _load_plan(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return copy.deepcopy(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None
