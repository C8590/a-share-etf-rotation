from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


SIGNAL_CASES_FILE = "signal_cases.csv"
V1_V2_COMPARISON_FILE = "v1_v2_comparison.csv"
SIGNAL_CASE_REVIEW_FILE = "signal_case_review.csv"
REGIME_CUTOFF = pd.Timestamp("2024-09-24")
SIGNAL_CASE_KEY_FIELDS = ("trade_date", "etf_code", "signal_version")

SIGNAL_CASE_FIELDS = (
    "trade_date",
    "etf_code",
    "etf_name",
    "signal_version",
    "market_state",
    "level1_sector",
    "level2_sector",
    "sector_rank",
    "etf_rank",
    "momentum_score",
    "acceleration_score",
    "trend_maturity",
    "entry_quality",
    "entry_action",
    "target_weight",
    "confidence",
    "reason",
    "price_valid",
    "price_status",
    "post_924_regime",
    "ret_1d",
    "ret_3d",
    "ret_5d",
    "ret_10d",
    "max_gain_10d",
    "max_drawdown_10d",
    "hindsight_label",
    "hindsight_reason",
)

SIGNAL_CASE_REVIEW_FIELDS = (
    "trade_date",
    "entry_action",
    "market_state",
    "trend_maturity",
    "entry_quality",
    "post_924_regime",
    "sample_count",
    "avg_ret_5d",
    "avg_ret_10d",
    "missed_opportunity_count",
    "correct_observation_count",
    "avoided_chase_risk_count",
    "insufficient_sample_count",
)

V1_V2_COMPARISON_FIELDS = (
    "trade_date",
    "v1_selected_etfs",
    "v2_candidate_etfs",
    "v2_actual_buy_etfs",
    "v2_entry_actions",
    "v2_market_state",
    "v2_selected_sectors",
    "same_as_v1",
    "difference_reason",
    "v2_no_buy_reason",
)

NO_BUY_REASON_LABELS = (
    "置信度不足",
    "趋势成熟度不足",
    "买点质量不足",
    "市场状态限制",
    "价格校验限制",
    "风险限制",
)


@dataclass(frozen=True)
class HindsightConfig:
    missed_ret_5d: float = 0.03
    small_drawdown_10d: float = -0.03
    large_drawdown_10d: float = -0.06
    chase_gain_10d: float = 0.05
    failed_ret_5d: float = -0.03
    min_required_days: int = 5


DEFAULT_HINDSIGHT_CONFIG = HindsightConfig()


def post_924_regime(trade_date: Any) -> str:
    parsed = pd.Timestamp(trade_date)
    return "pre_20240924" if parsed < REGIME_CUTOFF else "post_20240924"


def build_signal_case_rows(
    pre_selection_rows: Sequence[Mapping[str, Any]],
    entry_rows: Sequence[Mapping[str, Any]],
    *,
    signal_version: str = "V2_MODULAR",
    market_data: Mapping[str, pd.DataFrame] | None = None,
    hindsight_config: HindsightConfig = DEFAULT_HINDSIGHT_CONFIG,
) -> list[dict[str, Any]]:
    entry_by_symbol = {_symbol(row.get("symbol")): dict(row) for row in entry_rows}
    market_data = market_data or {}
    rows: list[dict[str, Any]] = []
    for pre_row in pre_selection_rows:
        symbol = _symbol(pre_row.get("symbol"))
        if not symbol:
            continue
        entry_row = entry_by_symbol.get(symbol, {})
        trade_date = _first_text(pre_row, entry_row, key="trade_date")
        entry_reason = _text(entry_row.get("entry_reason"))
        trend_maturity = _extract_after_label(entry_reason, ("趋势成熟度",))
        entry_quality = _extract_after_label(entry_reason, ("买点质量",))
        entry_action = _text(entry_row.get("buy_action"))
        price_valid, price_status = _price_status(entry_row, entry_action)
        reasons = [text for text in (_text(pre_row.get("reason")), entry_reason) if text]
        hindsight = build_hindsight_fields(
            symbol=symbol,
            trade_date=trade_date,
            entry_action=entry_action,
            market_data=market_data,
            config=hindsight_config,
        )
        rows.append(
            {
                "trade_date": trade_date,
                "etf_code": symbol,
                "etf_name": _first_text(pre_row, entry_row, key="name"),
                "signal_version": signal_version,
                "market_state": _first_text(pre_row, entry_row, key="market_state"),
                "level1_sector": _text(pre_row.get("sector")),
                "level2_sector": "",
                "sector_rank": _text(pre_row.get("sector_rank")),
                "etf_rank": _text(pre_row.get("rank")),
                "momentum_score": _text(pre_row.get("score")),
                "acceleration_score": _text(pre_row.get("acceleration_score")),
                "trend_maturity": trend_maturity,
                "entry_quality": entry_quality,
                "entry_action": entry_action,
                "target_weight": _text(entry_row.get("position_size")),
                "confidence": _text(entry_row.get("confidence")),
                "reason": " | ".join(reasons),
                "price_valid": price_valid,
                "price_status": price_status,
                "post_924_regime": post_924_regime(trade_date),
                **hindsight,
            }
        )
    return rows


def write_signal_cases(
    pre_selection_rows: Sequence[Mapping[str, Any]],
    entry_rows: Sequence[Mapping[str, Any]],
    output_dir: str | Path = "output",
    *,
    signal_version: str = "V2_MODULAR",
    market_data: Mapping[str, pd.DataFrame] | None = None,
    hindsight_config: HindsightConfig = DEFAULT_HINDSIGHT_CONFIG,
) -> list[dict[str, Any]]:
    new_rows = build_signal_case_rows(
        pre_selection_rows,
        entry_rows,
        signal_version=signal_version,
        market_data=market_data,
        hindsight_config=hindsight_config,
    )
    output_path = Path(output_dir)
    existing_rows = _read_signal_case_rows(output_path / SIGNAL_CASES_FILE)
    rows = merge_signal_case_rows(
        existing_rows,
        new_rows,
        market_data=market_data,
        hindsight_config=hindsight_config,
    )
    _write_csv(output_path / SIGNAL_CASES_FILE, SIGNAL_CASE_FIELDS, rows)
    write_signal_case_review(rows, output_path)
    return rows


def merge_signal_case_rows(
    existing_rows: Sequence[Mapping[str, Any]],
    new_rows: Sequence[Mapping[str, Any]],
    *,
    market_data: Mapping[str, pd.DataFrame] | None = None,
    hindsight_config: HindsightConfig = DEFAULT_HINDSIGHT_CONFIG,
) -> list[dict[str, Any]]:
    """Merge new daily cases into the historical case library.

    The identity of a case is trade_date + etf_code + signal_version. New rows
    replace the same identity from older files, while older identities are kept
    and get a fresh hindsight recalculation when market data is available.
    """

    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in existing_rows:
        normalized = _normalize_case_row(row)
        key = _case_key(normalized)
        if key:
            merged[key] = normalized
    for row in new_rows:
        normalized = _normalize_case_row(row)
        key = _case_key(normalized)
        if key:
            merged[key] = normalized

    refreshed = [_refresh_hindsight(row, market_data or {}, hindsight_config) for row in merged.values()]
    return sorted(refreshed, key=lambda row: (_text(row.get("trade_date")), _text(row.get("etf_code")), _text(row.get("signal_version"))))


def build_v1_v2_comparison_row(
    v1_summary: Mapping[str, Any],
    v2_summary: Mapping[str, Any],
    modular_pipeline: Mapping[str, Any],
) -> dict[str, Any]:
    pre_rows = [dict(row) for row in modular_pipeline.get("pre_selection", []) if isinstance(row, Mapping)]
    entry_rows = [dict(row) for row in modular_pipeline.get("entry", []) if isinstance(row, Mapping)]
    selected_pre_rows = [row for row in pre_rows if _truthy(row.get("selected"))]
    candidate_symbols = [_symbol(row.get("symbol")) for row in selected_pre_rows if _symbol(row.get("symbol"))]
    selected_symbols = set(candidate_symbols)
    actual_buy_symbols = [
        _symbol(row.get("symbol"))
        for row in entry_rows
        if _symbol(row.get("symbol")) in selected_symbols and _is_actual_buy(row)
    ]
    entry_actions = [
        f"{_symbol(row.get('symbol'))}:{_text(row.get('buy_action'))}"
        for row in entry_rows
        if _symbol(row.get("symbol")) in selected_symbols and _text(row.get("buy_action"))
    ]
    v1_symbols = _csv_symbols(v1_summary.get("target_symbols"))
    same_as_v1 = set(v1_symbols) == set(candidate_symbols)
    no_buy_reason = summarize_no_buy_reasons(pre_rows, entry_rows) if not actual_buy_symbols else ""
    difference_reason = "V1/V2 selected ETFs match" if same_as_v1 else _difference_reason(v1_symbols, candidate_symbols, no_buy_reason)
    trade_date = _text(v2_summary.get("effective_signal_date") or v2_summary.get("signal_date") or v1_summary.get("effective_signal_date"))
    return {
        "trade_date": trade_date,
        "v1_selected_etfs": ",".join(v1_symbols) if v1_symbols else "无",
        "v2_candidate_etfs": ",".join(candidate_symbols) if candidate_symbols else "无",
        "v2_actual_buy_etfs": ",".join(actual_buy_symbols) if actual_buy_symbols else "无",
        "v2_entry_actions": " | ".join(entry_actions) if entry_actions else "无",
        "v2_market_state": _text(v2_summary.get("v2_market_state") or v2_summary.get("modular_market_state")),
        "v2_selected_sectors": _text(v2_summary.get("v2_selected_sectors") or v2_summary.get("modular_selected_sectors")),
        "same_as_v1": same_as_v1,
        "difference_reason": difference_reason,
        "v2_no_buy_reason": no_buy_reason,
    }


def write_v1_v2_comparison(
    v1_summary: Mapping[str, Any],
    v2_summary: Mapping[str, Any],
    modular_pipeline: Mapping[str, Any],
    output_dir: str | Path = "output",
) -> dict[str, Any]:
    row = build_v1_v2_comparison_row(v1_summary, v2_summary, modular_pipeline)
    _write_csv(Path(output_dir) / V1_V2_COMPARISON_FILE, V1_V2_COMPARISON_FIELDS, [row])
    return row


def build_hindsight_fields(
    *,
    symbol: str,
    trade_date: Any,
    entry_action: str,
    market_data: Mapping[str, pd.DataFrame],
    config: HindsightConfig = DEFAULT_HINDSIGHT_CONFIG,
) -> dict[str, Any]:
    prices = _future_close_prices(symbol, trade_date, market_data)
    empty = {
        "ret_1d": "",
        "ret_3d": "",
        "ret_5d": "",
        "ret_10d": "",
        "max_gain_10d": "",
        "max_drawdown_10d": "",
        "hindsight_label": "样本不足",
        "hindsight_reason": "后续行情不足，后验统计暂不判断。",
    }
    if prices is None or len(prices) <= config.min_required_days:
        return empty

    base = float(prices.iloc[0])
    if base <= 0:
        return empty

    future = prices.iloc[1:]
    values: dict[str, Any] = {}
    for days in (1, 3, 5, 10):
        values[f"ret_{days}d"] = _format_ratio(_horizon_return(prices, days))

    window_10d = future.head(10)
    if window_10d.empty:
        max_gain_10d = None
        max_drawdown_10d = None
    else:
        max_gain_10d = float(window_10d.max() / base - 1.0)
        max_drawdown_10d = float(window_10d.min() / base - 1.0)

    ret_5d = _horizon_return(prices, 5)
    ret_10d = _horizon_return(prices, 10)
    label, reason = classify_hindsight(
        entry_action=entry_action,
        ret_5d=ret_5d,
        ret_10d=ret_10d,
        max_gain_10d=max_gain_10d,
        max_drawdown_10d=max_drawdown_10d,
        config=config,
    )
    values.update(
        {
            "max_gain_10d": _format_ratio(max_gain_10d),
            "max_drawdown_10d": _format_ratio(max_drawdown_10d),
            "hindsight_label": label,
            "hindsight_reason": reason,
        }
    )
    return values


def classify_hindsight(
    *,
    entry_action: Any,
    ret_5d: float | None,
    ret_10d: float | None,
    max_gain_10d: float | None,
    max_drawdown_10d: float | None,
    config: HindsightConfig = DEFAULT_HINDSIGHT_CONFIG,
) -> tuple[str, str]:
    if ret_5d is None or max_drawdown_10d is None:
        return "样本不足", "后续行情不足，后验统计暂不判断。"

    action = _text(entry_action)
    is_watch = _is_watch_action(action)
    if max_gain_10d is not None and max_gain_10d >= config.chase_gain_10d and max_drawdown_10d <= config.large_drawdown_10d:
        return "追高风险被避免", "未来 10 日先出现明显浮盈但随后回撤较大，观察避免了追高风险。"
    if is_watch and ret_5d >= config.missed_ret_5d and max_drawdown_10d >= config.small_drawdown_10d:
        return "可能错过机会", "观察后 5 日收益明显为正，且 10 日内最大回撤较小。"
    if is_watch and (ret_5d <= 0 or max_drawdown_10d <= config.large_drawdown_10d):
        return "观察正确", "观察后收益不佳或 10 日内回撤较大，谨慎处理有效。"
    if ret_5d <= config.failed_ret_5d:
        return "信号失败", "后验 5 日收益明显为负。"
    return "观察正确" if is_watch else "信号失败", "后验表现未达到可能错过机会标准。"


def build_signal_case_review_rows(case_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str, str], list[Mapping[str, Any]]] = {}
    for row in case_rows:
        key = (
            _text(row.get("trade_date")),
            _text(row.get("entry_action")),
            _text(row.get("market_state")),
            _text(row.get("trend_maturity")),
            _text(row.get("entry_quality")),
            _text(row.get("post_924_regime")),
        )
        groups.setdefault(key, []).append(row)

    rows: list[dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        labels = Counter(_text(item.get("hindsight_label")) for item in items)
        rows.append(
            {
                "trade_date": key[0],
                "entry_action": key[1],
                "market_state": key[2],
                "trend_maturity": key[3],
                "entry_quality": key[4],
                "post_924_regime": key[5],
                "sample_count": len(items),
                "avg_ret_5d": _format_ratio(_average_numeric(item.get("ret_5d") for item in items)),
                "avg_ret_10d": _format_ratio(_average_numeric(item.get("ret_10d") for item in items)),
                "missed_opportunity_count": labels["可能错过机会"],
                "correct_observation_count": labels["观察正确"],
                "avoided_chase_risk_count": labels["追高风险被避免"],
                "insufficient_sample_count": labels["样本不足"],
            }
        )
    return rows


def write_signal_case_review(case_rows: Sequence[Mapping[str, Any]], output_dir: str | Path = "output") -> list[dict[str, Any]]:
    rows = build_signal_case_review_rows(case_rows)
    _write_csv(Path(output_dir) / SIGNAL_CASE_REVIEW_FILE, SIGNAL_CASE_REVIEW_FIELDS, rows)
    return rows


def summarize_no_buy_reasons(
    pre_selection_rows: Sequence[Mapping[str, Any]],
    entry_rows: Sequence[Mapping[str, Any]],
) -> str:
    selected_symbols = {_symbol(row.get("symbol")) for row in pre_selection_rows if _truthy(row.get("selected"))}
    scoped_entries = [row for row in entry_rows if not selected_symbols or _symbol(row.get("symbol")) in selected_symbols]
    counts: Counter[str] = Counter()
    for row in scoped_entries:
        if _is_actual_buy(row):
            continue
        for reason in _classify_no_buy_reason(row):
            counts[reason] += 1
    if not counts and not selected_symbols:
        counts["市场状态限制"] += 1
    ordered = [f"{label}:{counts[label]}" for label in NO_BUY_REASON_LABELS if counts[label]]
    return " | ".join(ordered) if ordered else "无"


def _classify_no_buy_reason(row: Mapping[str, Any]) -> set[str]:
    text = " ".join(_text(row.get(key)) for key in ("buy_action", "entry_reason", "reason", "market_state", "price_status"))
    confidence = _float(row.get("confidence"))
    labels: set[str] = set()
    if confidence is not None and confidence < 0.5:
        labels.add("置信度不足")
    if any(token in text for token in ("启动", "过热", "趋势成熟度", "maturity", "未站上", "趋势")):
        labels.add("趋势成熟度不足")
    if any(token in text for token in ("买点质量", "追高", "回踩", "冲高", "等待", "pullback")):
        labels.add("买点质量不足")
    if any(token in text for token in ("防守", "market_state", "market state", "禁止买入")):
        labels.add("市场状态限制")
    if any(token in text for token in ("价格", "price", "quote", "行情", "price_status")):
        labels.add("价格校验限制")
    if any(token in text for token in ("风险", "回撤", "risk", "止损", "冷却")):
        labels.add("风险限制")
    if not labels:
        labels.add("置信度不足")
    return labels


def _difference_reason(v1_symbols: list[str], v2_symbols: list[str], no_buy_reason: str) -> str:
    if not v1_symbols and v2_symbols:
        return "V1 empty while V2 has candidates"
    if v1_symbols and not v2_symbols:
        return "V2 has no selected candidates; likely filtered by market, trend, or data rules"
    if no_buy_reason:
        return f"V1/V2 candidate sets differ; V2 no-buy reasons: {no_buy_reason}"
    return "V1/V2 candidate sets differ"


def _price_status(row: Mapping[str, Any], entry_action: str) -> tuple[str, str]:
    buy_price = _text(row.get("buy_price"))
    if buy_price:
        return "true", "available"
    if _action_text_is_buy(entry_action):
        return "false", "no_price_for_buy"
    return "true", "not_required"


def _is_actual_buy(row: Mapping[str, Any]) -> bool:
    action = _text(row.get("buy_action") or row.get("entry_action"))
    size = _float(row.get("position_size") or row.get("target_weight"))
    return _action_text_is_buy(action) and (size is None or size > 0)


def _action_text_is_buy(action: str) -> bool:
    lowered = action.lower()
    if any(token in lowered for token in ("buy", "probe", "standard", "add")):
        return "forbid" not in lowered
    return "买入" in action and "禁止" not in action


def _extract_after_label(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:：]\s*([^;；|,，]+)", text)
        if match:
            return match.group(1).strip()
    return ""


def _future_close_prices(
    symbol: str,
    trade_date: Any,
    market_data: Mapping[str, pd.DataFrame],
) -> pd.Series | None:
    frame = market_data.get(symbol)
    if frame is None:
        frame = market_data.get(str(symbol).zfill(6))
    if frame is None or frame.empty:
        return None
    data = _normalize_market_frame(frame)
    if data.empty or "close" not in data.columns:
        return None
    trade_ts = pd.Timestamp(trade_date).normalize()
    prices = pd.to_numeric(data.loc[data.index >= trade_ts, "close"], errors="coerce").dropna()
    return prices if not prices.empty else None


def _normalize_market_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    if "date" in data.columns:
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        data = data.dropna(subset=["date"]).set_index("date")
    else:
        data.index = pd.to_datetime(data.index, errors="coerce")
        data = data[~pd.isna(data.index)]
    return data.sort_index()


def _horizon_return(prices: pd.Series, days: int) -> float | None:
    if len(prices) <= days:
        return None
    base = float(prices.iloc[0])
    if base <= 0:
        return None
    return float(prices.iloc[days] / base - 1.0)


def _format_ratio(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}"


def _is_watch_action(value: Any) -> bool:
    text = _text(value).lower()
    return any(token in text for token in ("观察", "瑙傚療", "watch"))


def _average_numeric(values: Any) -> float | None:
    numbers: list[float] = []
    for value in values:
        parsed = _float(value)
        if parsed is not None:
            numbers.append(parsed)
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _read_signal_case_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        return []
    return [_normalize_case_row(row) for row in frame.to_dict("records")]


def _normalize_case_row(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {field: _text(row.get(field)) for field in SIGNAL_CASE_FIELDS}
    normalized["etf_code"] = _symbol(normalized.get("etf_code"))
    if not normalized["signal_version"]:
        normalized["signal_version"] = "V2_MODULAR"
    if normalized["trade_date"]:
        try:
            normalized["trade_date"] = pd.Timestamp(normalized["trade_date"]).date().isoformat()
        except Exception:
            pass
    if normalized["trade_date"] and not normalized["post_924_regime"]:
        normalized["post_924_regime"] = post_924_regime(normalized["trade_date"])
    return normalized


def _case_key(row: Mapping[str, Any]) -> tuple[str, str, str] | None:
    trade_date = _text(row.get("trade_date"))
    symbol = _symbol(row.get("etf_code"))
    version = _text(row.get("signal_version")) or "V2_MODULAR"
    if not trade_date or not symbol:
        return None
    return trade_date, symbol, version


def _refresh_hindsight(
    row: Mapping[str, Any],
    market_data: Mapping[str, pd.DataFrame],
    hindsight_config: HindsightConfig,
) -> dict[str, Any]:
    refreshed = dict(row)
    hindsight = build_hindsight_fields(
        symbol=_symbol(row.get("etf_code")),
        trade_date=row.get("trade_date"),
        entry_action=_text(row.get("entry_action")),
        market_data=market_data,
        config=hindsight_config,
    )
    if _text(hindsight.get("hindsight_label")) == "样本不足" and _text(row.get("hindsight_label")) not in {"", "样本不足"}:
        return refreshed
    refreshed.update(hindsight)
    return refreshed


def _csv_symbols(value: Any) -> list[str]:
    text = _text(value)
    if not text or text in {"无", "空仓", "N/A", "nan", "None"}:
        return []
    return [_symbol(item) for item in text.split(",") if _symbol(item)]


def _first_text(*rows: Mapping[str, Any], key: str) -> str:
    for row in rows:
        text = _text(row.get(key))
        if text:
            return text
    return ""


def _symbol(value: Any) -> str:
    text = _text(value)
    return text.zfill(6) if text.isdigit() else text


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return str(value).strip()


def _float(value: Any) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "y", "是", "selected", "入选"}
