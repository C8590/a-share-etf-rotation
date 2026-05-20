from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import yaml

from .event_store import RiskEventStore
from .models import (
    RISK_LEVEL_FLOOR,
    RISK_LEVEL_ORDER,
    RISK_LEVEL_SCORE,
    SCORE_LEVEL_BANDS,
    RiskEvent,
    RiskGate,
    as_list,
    normalize_symbol,
    parse_date,
)


DEFAULT_CONFIG_PATH = Path("config") / "risk_warning.yaml"
DEFAULT_OUTPUT_DIR = Path("output")


def load_risk_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def calculate_next_day_risk(
    risk_date: str | date | None = None,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    event_store: RiskEventStore | None = None,
    events: Sequence[RiskEvent] | None = None,
    current_position_path: str | Path = "config/current_position.yaml",
    portfolio_path: str | Path = "data/portfolio.csv",
    universe_path: str | Path = "config/etf_universe.yaml",
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> RiskGate:
    parsed_date = _resolve_risk_date(risk_date)
    config = load_risk_config(config_path)
    store = event_store or RiskEventStore()
    all_events = list(events) if events is not None else store.load_events()
    active_events = [event for event in all_events if event.is_effective_on(parsed_date)]

    overnight_score, overnight_note = _overnight_risk(config)
    event_score, event_note = _event_risk(active_events)
    market_score, market_note = _market_fragility(config, Path(output_dir))
    portfolio_score, portfolio_note = _portfolio_exposure(
        active_events,
        current_position_path=Path(current_position_path),
        portfolio_path=Path(portfolio_path),
        universe_path=Path(universe_path),
    )

    raw_score = (
        0.30 * overnight_score
        + 0.30 * event_score
        + 0.25 * market_score
        + 0.15 * portfolio_score
    )
    risk_score = int(round(min(max(raw_score, 0), 100)))
    level = _level_from_score(risk_score)
    forced_level = _forced_level(active_events)
    if RISK_LEVEL_ORDER[forced_level] > RISK_LEVEL_ORDER[level]:
        level = forced_level
    risk_score = max(risk_score, RISK_LEVEL_FLOOR[level])

    gate = _gate_values(level)
    if any(event.expected_duration == "unknown" for event in active_events):
        gate["require_manual_review"] = True if level in {"R2", "R3", "R4"} else gate["require_manual_review"]

    affected_sectors = _unique(
        sector
        for event in active_events
        for sector in event.affected_sectors
    )
    explain = _build_explain(
        level=level,
        risk_score=risk_score,
        active_events=active_events,
        notes=[overnight_note, event_note, market_note, portfolio_note],
        gate=gate,
    )

    return RiskGate(
        risk_date=parsed_date.isoformat(),
        risk_score=risk_score,
        risk_level=level,
        overnight_risk=overnight_score,
        event_risk=event_score,
        market_fragility=market_score,
        portfolio_exposure=portfolio_score,
        affected_sectors=affected_sectors,
        freeze_entry=bool(gate["freeze_entry"]),
        equity_cap_override=float(gate["equity_cap_override"]),
        require_manual_review=bool(gate["require_manual_review"]),
        manual_takeover_required=bool(gate["manual_takeover_required"]),
        explain=explain,
        active_events=[event.to_dict() for event in active_events],
    )


def write_risk_outputs(gate: RiskGate, output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> None:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = gate.to_dict()
    (out_dir / "risk_gate.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_payload = dict(payload)
    csv_payload["affected_sectors"] = "、".join(payload.get("affected_sectors", []))
    csv_payload["active_events"] = json.dumps(payload.get("active_events", []), ensure_ascii=False)
    pd.DataFrame([csv_payload]).to_csv(out_dir / "risk_warning_next_day.csv", index=False, encoding="utf-8-sig")


def _resolve_risk_date(value: str | date | None) -> date:
    if value is None:
        return datetime.now().date()
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError("risk_date must use YYYY-MM-DD format")
    return parsed


def _overnight_risk(config: Mapping[str, Any]) -> tuple[int, str]:
    overnight = config.get("overnight_risk", {}) if isinstance(config, Mapping) else {}
    if not isinstance(overnight, Mapping) or not overnight.get("enabled", False):
        return 0, "未配置隔夜外部冲击，隔夜风险按 0 处理。"
    score = _clip_score(overnight.get("score", 0))
    explain = str(overnight.get("explain") or "已读取人工配置的隔夜风险。")
    return score, explain


def _event_risk(events: Sequence[RiskEvent]) -> tuple[int, str]:
    if not events:
        return 0, "当前没有生效中的外部风险事件。"
    base = max(RISK_LEVEL_SCORE[event.risk_level] for event in events)
    if any(event.risk_level == "R4" for event in events):
        return 100, "存在 R4/P0 风险事件，事件风险直接按最高分处理。"
    concentration_bonus = min(max(len(events) - 1, 0) * 10, 25)
    score = _clip_score(base + concentration_bonus)
    return score, f"当前有 {len(events)} 个生效风险事件，按最高等级并叠加事件集中度计分。"


def _market_fragility(config: Mapping[str, Any], output_dir: Path) -> tuple[int, str]:
    configured = config.get("market_fragility", {}) if isinstance(config, Mapping) else {}
    if isinstance(configured, Mapping) and "score" in configured:
        return _clip_score(configured.get("score", 30)), str(configured.get("explain") or "已读取人工配置的市场脆弱度。")

    state = _read_market_state(output_dir)
    if not state:
        return 30, "未读取到完整市场脆弱度数据，按中性偏低风险处理。"
    normalized = str(state).strip().lower()
    if any(token in normalized for token in ("attack", "up", "上行", "进攻", "攻")):
        return 20, "市场状态偏上行，市场脆弱度按低位处理。"
    if any(token in normalized for token in ("defense", "down", "下行", "防御", "守")):
        return 70, "市场状态偏防御或下行，市场脆弱度上调。"
    if any(token in normalized for token in ("balanced", "neutral", "震荡", "均衡")):
        return 45, "市场状态偏震荡，市场脆弱度按中性处理。"
    return 30, "市场状态无法稳定识别，按中性偏低风险处理。"


def _read_market_state(output_dir: Path) -> str:
    for filename in ("pre_selection_result.csv", "entry_signal.csv", "compare_signal.csv"):
        path = output_dir / filename
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, dtype=str).fillna("")
        except Exception:
            continue
        for col in ("market_state", "v2_market_state", "modular_market_state", "market_phase"):
            if col in frame.columns and not frame.empty:
                text = str(frame.iloc[0].get(col) or "").strip()
                if text:
                    return text
    return ""


def _portfolio_exposure(
    events: Sequence[RiskEvent],
    *,
    current_position_path: Path,
    portfolio_path: Path,
    universe_path: Path,
) -> tuple[int, str]:
    holdings = _load_holdings(current_position_path, portfolio_path)
    if not holdings:
        return 0, "未读取到当前持仓，组合暴露按 0 处理。"
    if not events:
        return 20, "当前有持仓但没有生效风险事件，组合暴露按基础持仓风险处理。"

    affected_assets = {normalize_symbol(asset) for event in events for asset in event.affected_assets}
    affected_sectors = {sector for event in events for sector in event.affected_sectors}
    universe = _load_universe(universe_path)
    if "全市场" in affected_sectors:
        return 80, "风险事件影响全市场，按当前权益持仓暴露上调。"

    total = max(len(holdings), 1)
    matched = 0
    for holding in holdings:
        symbol = normalize_symbol(holding.get("symbol"))
        info = universe.get(symbol, {})
        searchable = " ".join(
            str(part)
            for part in (
                symbol,
                holding.get("name", ""),
                info.get("name", ""),
                info.get("sector", ""),
                info.get("theme", ""),
                info.get("category", ""),
                info.get("asset_class", ""),
            )
        )
        if symbol in affected_assets or any(sector and sector in searchable for sector in affected_sectors):
            matched += 1

    if matched <= 0:
        return 20, "当前持仓与生效风险事件未发现明显重合，组合暴露按低位处理。"
    ratio = matched / total
    if ratio >= 0.67:
        return 90, "主要持仓与受影响方向存在重合，组合暴露风险较高。"
    if ratio >= 0.34:
        return 70, "部分持仓与受影响方向存在重合，组合暴露风险上调。"
    return 50, "少量持仓与受影响方向存在重合，组合暴露按中等风险处理。"


def _load_holdings(current_position_path: Path, portfolio_path: Path) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []
    if current_position_path.exists():
        try:
            raw = yaml.safe_load(current_position_path.read_text(encoding="utf-8")) or {}
            if not raw.get("current_empty"):
                for item in raw.get("holdings", []) or []:
                    symbol = normalize_symbol(item.get("symbol") or item.get("ETF代码"))
                    shares = _safe_float(item.get("shares", item.get("持仓份额", 0)))
                    if symbol and shares > 0:
                        holdings.append({"symbol": symbol, "name": str(item.get("name") or item.get("ETF名称") or "")})
        except Exception:
            pass
    if holdings or not portfolio_path.exists():
        return holdings
    try:
        frame = pd.read_csv(portfolio_path, dtype=str).fillna("")
    except Exception:
        return holdings
    symbol_cols = ("symbol", "ETF代码", "ETF浠ｇ爜")
    name_cols = ("name", "ETF名称", "ETF鍚嶇О")
    shares_cols = ("shares", "持仓份额", "鎸佷粨浠介")
    for _, row in frame.iterrows():
        symbol = normalize_symbol(_first_present(row, symbol_cols))
        shares = _safe_float(_first_present(row, shares_cols))
        if symbol and shares > 0:
            holdings.append({"symbol": symbol, "name": str(_first_present(row, name_cols) or "")})
    return holdings


def _load_universe(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    rows = raw.get("etfs", []) if isinstance(raw, Mapping) else []
    return {normalize_symbol(row.get("symbol")): dict(row) for row in rows if isinstance(row, Mapping)}


def _level_from_score(score: int) -> str:
    for threshold, level in SCORE_LEVEL_BANDS:
        if score >= threshold:
            return level
    return "R0"


def _forced_level(events: Sequence[RiskEvent]) -> str:
    level = "R0"
    for event in events:
        if RISK_LEVEL_ORDER[event.risk_level] > RISK_LEVEL_ORDER[level]:
            level = event.risk_level
    return level


def _gate_values(level: str) -> dict[str, Any]:
    return {
        "R0": {"freeze_entry": False, "equity_cap_override": 1.00, "require_manual_review": False, "manual_takeover_required": False},
        "R1": {"freeze_entry": False, "equity_cap_override": 1.00, "require_manual_review": False, "manual_takeover_required": False},
        "R2": {"freeze_entry": False, "equity_cap_override": 0.60, "require_manual_review": False, "manual_takeover_required": False},
        "R3": {"freeze_entry": True, "equity_cap_override": 0.30, "require_manual_review": True, "manual_takeover_required": False},
        "R4": {"freeze_entry": True, "equity_cap_override": 0.00, "require_manual_review": True, "manual_takeover_required": True},
    }[level]


def _build_explain(
    *,
    level: str,
    risk_score: int,
    active_events: Sequence[RiskEvent],
    notes: Sequence[str],
    gate: Mapping[str, Any],
) -> str:
    level_text = {
        "R0": "当前风险等级为 R0 正常，策略可以按原有日频动量规则运行。",
        "R1": "当前风险等级为 R1 轻微扰动，策略正常运行但前端需要提示风险。",
        "R2": "当前风险等级为 R2 谨慎，系统建议提高 entry 门槛并降低权益仓位上限。",
        "R3": "当前风险等级为 R3 高风险，普通权益新开仓暂停，保留减仓、卖出、止损和退出动作。",
        "R4": "P0 风险预警：entry 已冻结，建议人工接管。",
    }[level]
    event_titles = "；".join(event.title or event.event_type for event in active_events[:3])
    unconfirmed = [event for event in active_events if not event.manual_confirmed]
    parts = [f"{level_text} 风险分数 {risk_score}。"]
    if event_titles:
        parts.append(f"生效事件：{event_titles}。")
    if unconfirmed:
        parts.append("其中存在尚未人工确认的事件，系统按谨慎原则纳入预警。")
    if gate.get("freeze_entry"):
        parts.append("风险门控只冻结新买入、新加仓和普通 entry，不阻断已有持仓的风险降低动作。")
    parts.extend(note for note in notes if note)
    return _clean_natural_text(" ".join(parts))


def _clip_score(value: Any) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        score = 0
    return min(max(score, 0), 100)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None) or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_present(row: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in row and str(row.get(key) or "").strip():
            return row.get(key)
    return ""


def _unique(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in as_list(value):
            if item not in result:
                result.append(item)
    return result


def _clean_natural_text(text: str) -> str:
    banned = ("true", "false", "None", "NaN", "traceback")
    result = str(text)
    replacements = {"true": "是", "false": "否", "None": "无", "NaN": "无", "traceback": "异常详情"}
    for token in banned:
        result = result.replace(token, replacements[token])
        result = result.replace(token.upper(), replacements[token])
        result = result.replace(token.capitalize(), replacements[token])
    return result
