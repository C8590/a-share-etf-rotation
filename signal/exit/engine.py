"""Right-side double-momentum exit engine.

This module owns the standalone exit model. It intentionally does not wire into
``signal.daily_signal`` so the new exit layer can be tested and evolved without
changing the current daily workflow.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from contracts.signal_schema import BuyAction, EXIT_SIGNAL_FIELDS, MarketState, SellAction

OUTPUT_FILE = "exit_signal.csv"
DEFAULT_OUTPUT_DIR = Path("output")
REQUIRED_OUTPUT_FIELDS = EXIT_SIGNAL_FIELDS

DEFENSIVE_STATES = {MarketState.DEFENSE.value, "defense", "defensive", "防守"}
QUALIFIED_BUY_ACTIONS = {
    BuyAction.PROBE_BUY.value,
    BuyAction.STANDARD_BUY.value,
    BuyAction.ADD_BUY.value,
    "试探买入",
    "标准买入",
    "加强买入",
    "probe_buy",
    "standard_buy",
    "add_buy",
}


@dataclass(frozen=True)
class ExitConfig:
    """Tunable thresholds for the exit model."""

    cooldown_days: int = 5
    data_abnormal_cooldown_days: int = 10
    max_drawdown_pct: float = 0.08
    hard_drawdown_pct: float = 0.12
    trend_break_buffer_pct: float = 0.01
    consecutive_negative_acceleration_days: int = 2
    sector_rank_drop: int = 3
    sector_breadth_drop_pct: float = 0.15
    replacement_score_gap: float = 15.0
    replacement_min_buy_quality: float = 0.65


@dataclass(frozen=True)
class ExitDecision:
    action: str
    reduce_ratio: float
    cooldown_days: int
    reason: str


class ExitEngine:
    """Produce ``exit_signal.csv`` rows for current holdings.

    The engine accepts dictionary-like inputs so callers can pass records from
    position files, quote snapshots, rank tables, or future signal modules. The
    output schema is fixed by ``contracts.signal_schema.EXIT_SIGNAL_FIELDS``.
    """

    def __init__(self, config: ExitConfig | None = None) -> None:
        self.config = config or ExitConfig()

    def run(
        self,
        holdings: Sequence[Mapping[str, Any]] | None = None,
        output_dir: str | Path | None = None,
        *,
        market_context: Mapping[str, Any] | None = None,
        candidates: Sequence[Mapping[str, Any]] | None = None,
        trade_date: str | None = None,
        source_file: str = "current_position",
        generated_at: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return rows that match ``REQUIRED_OUTPUT_FIELDS`` and write the CSV.

        Parameters are optional to keep this module easy to connect later.
        Missing numeric inputs make the model conservative: it will hold or
        cautiously hold instead of inventing a sell signal. When ``output_dir``
        is omitted, the contract file is written to ``output/exit_signal.csv``.
        """

        holdings = list(holdings or [])
        market_context = market_context or {}
        generated_at = generated_at or _now_iso()
        trade_date = trade_date or _first_value(holdings, "trade_date") or str(market_context.get("trade_date") or generated_at[:10])

        rows = [
            self._build_row(
                holding=holding,
                market_context=market_context,
                candidates=candidates or (),
                trade_date=trade_date,
                source_file=str(holding.get("source_file") or source_file),
                generated_at=generated_at,
            )
            for holding in holdings
        ]

        self.write_csv(rows, output_dir or DEFAULT_OUTPUT_DIR)

        return rows

    def write_csv(self, rows: Sequence[Mapping[str, Any]], output_dir: str | Path) -> Path:
        """Write ``exit_signal.csv`` using the exact contract field order."""

        output_path = Path(output_dir) / OUTPUT_FILE
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(REQUIRED_OUTPUT_FIELDS), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in REQUIRED_OUTPUT_FIELDS})
        return output_path

    def _build_row(
        self,
        *,
        holding: Mapping[str, Any],
        market_context: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]],
        trade_date: str,
        source_file: str,
        generated_at: str,
    ) -> dict[str, Any]:
        market_state = _text(holding.get("market_state") or market_context.get("market_state") or MarketState.BALANCED.value)
        sell_price = _price(holding, ("sell_price", "current_price", "latest_price", "price", "close"))
        decision = self.evaluate_holding(holding, market_context=market_context, candidates=candidates)

        return {
            "trade_date": trade_date,
            "symbol": _text(holding.get("symbol")),
            "name": _text(holding.get("name") or holding.get("symbol")),
            "market_state": market_state,
            "sell_action": decision.action,
            "sell_price": "" if sell_price is None else round(sell_price, 4),
            "reduce_ratio": decision.reduce_ratio,
            "cool_down_days": decision.cooldown_days,
            "exit_reason": decision.reason,
            "source_file": source_file,
            "generated_at": generated_at,
        }

    def evaluate_holding(
        self,
        holding: Mapping[str, Any],
        *,
        market_context: Mapping[str, Any] | None = None,
        candidates: Sequence[Mapping[str, Any]] | None = None,
    ) -> ExitDecision:
        """Evaluate one holding and return the highest-priority exit decision."""

        market_context = market_context or {}
        candidates = candidates or ()
        cooldown_remaining = _int_value(holding, "cooldown_remaining", "cooldown_days_remaining", "cool_down_days_remaining")
        if cooldown_remaining > 0:
            return ExitDecision(
                SellAction.COOL_DOWN.value,
                0.0,
                cooldown_remaining,
                f"仍处于冷却期，剩余 {cooldown_remaining} 天，暂不重新评估卖出或换入。",
            )

        data_problem = self._data_abnormal_reason(holding)
        if data_problem:
            return ExitDecision(
                SellAction.CLEAR.value,
                1.0,
                self.config.data_abnormal_cooldown_days,
                f"数据异常退出：{data_problem}，先清仓并进入冷却，避免用失真的价格继续决策。",
            )

        risk_decision = self._risk_exit(holding, market_context)
        if risk_decision is not None:
            return risk_decision

        decay_reasons = self._trend_decay_reasons(holding, market_context)
        replacement = self._replacement_candidate(holding, candidates)
        if replacement is not None and decay_reasons:
            name = _text(replacement.get("name") or replacement.get("symbol"))
            gap = _float_value(replacement.get("score")) - _float_value(holding.get("current_score"), holding.get("score"), default=0.0)
            return ExitDecision(
                SellAction.CLEAR.value,
                1.0,
                self.config.cooldown_days,
                f"机会替换退出：新候选{name}评分高出当前持仓 {gap:.1f} 分，买点质量合格且不属于同板块重复；当前还出现{';'.join(decay_reasons)}。",
            )
        if replacement is not None:
            name = _text(replacement.get("name") or replacement.get("symbol"))
            gap = _float_value(replacement.get("score")) - _float_value(holding.get("current_score"), holding.get("score"), default=0.0)
            return ExitDecision(
                SellAction.REDUCE_HALF.value,
                0.5,
                0,
                f"机会替换退出：新候选{name}评分高出当前持仓 {gap:.1f} 分，买点质量合格且不属于同板块重复，先减仓一半给更强机会腾挪仓位。",
            )

        if len(decay_reasons) >= 2:
            return ExitDecision(
                SellAction.REDUCE_HALF.value,
                0.5,
                0,
                f"趋势衰减退出：{';'.join(decay_reasons)}，双层动量共振减弱，减仓一半观察。",
            )
        if len(decay_reasons) == 1:
            return ExitDecision(
                SellAction.REDUCE_ONE_THIRD.value,
                1.0 / 3.0,
                0,
                f"趋势衰减退出：{decay_reasons[0]}，先减仓三分之一，避免把正常回撤误判成彻底反转。",
            )

        if _is_weak_market(holding.get("market_state") or market_context.get("market_state")):
            return ExitDecision(
                SellAction.CAUTIOUS_HOLD.value,
                0.0,
                0,
                "市场处于防守状态，但个体价格未触发回撤或趋势线风险，谨慎持有并等待右侧确认。",
            )

        return ExitDecision(
            SellAction.HOLD.value,
            0.0,
            0,
            "持仓趋势、板块动量和替换机会均未触发退出条件，继续持有。",
        )

    def _risk_exit(self, holding: Mapping[str, Any], market_context: Mapping[str, Any]) -> ExitDecision | None:
        current_price = _price(holding, ("current_price", "latest_price", "price", "close", "sell_price"))
        peak_price = _price(holding, ("peak_price", "highest_price", "recent_high"))
        trend_line = _price(holding, ("trend_line", "ma20", "ma50", "ma60", "moving_average"))
        drawdown_pct = _drawdown_pct(current_price, peak_price)
        market_state = holding.get("market_state") or market_context.get("market_state")

        if drawdown_pct is not None and drawdown_pct >= self.config.hard_drawdown_pct:
            return ExitDecision(
                SellAction.CLEAR.value,
                1.0,
                self.config.cooldown_days,
                f"风险退出：个体从阶段高点回撤 {drawdown_pct:.1%}，超过硬止损阈值，清仓并进入冷却。",
            )

        if current_price is not None and trend_line is not None and current_price < trend_line * (1 - self.config.trend_break_buffer_pct):
            return ExitDecision(
                SellAction.CLEAR.value,
                1.0,
                self.config.cooldown_days,
                f"风险退出：价格 {current_price:.4f} 跌破趋势线 {trend_line:.4f}，右侧趋势被破坏，清仓等待重新走强。",
            )

        if drawdown_pct is not None and drawdown_pct >= self.config.max_drawdown_pct:
            return ExitDecision(
                SellAction.REDUCE_HALF.value,
                0.5,
                0,
                f"风险退出：个体回撤 {drawdown_pct:.1%} 已超过容忍区间，先减仓一半控制净值波动。",
            )

        if _is_weak_market(market_state):
            if drawdown_pct is not None and drawdown_pct >= self.config.max_drawdown_pct * 0.6:
                return ExitDecision(
                    SellAction.REDUCE_HALF.value,
                    0.5,
                    0,
                    f"风险退出：市场转防守且个体已回撤 {drawdown_pct:.1%}，减仓一半降低系统性风险。",
                )
            return ExitDecision(
                SellAction.CAUTIOUS_HOLD.value,
                0.0,
                0,
                "风险退出观察：市场转防守，但持仓尚未跌破趋势或出现大回撤，转为谨慎持有。",
            )

        return None

    def _trend_decay_reasons(self, holding: Mapping[str, Any], market_context: Mapping[str, Any]) -> list[str]:
        reasons: list[str] = []
        negative_days = _negative_acceleration_days(holding)
        if negative_days >= self.config.consecutive_negative_acceleration_days:
            reasons.append(f"加速度连续 {negative_days} 天转负")

        prev_rank = _int_value(holding, "prev_sector_rank", "previous_sector_rank")
        current_rank = _int_value(holding, "sector_rank", "current_sector_rank")
        if prev_rank > 0 and current_rank > 0 and current_rank - prev_rank >= self.config.sector_rank_drop:
            reasons.append(f"板块排名从第 {prev_rank} 名降至第 {current_rank} 名")

        prev_breadth = _float_value(holding.get("prev_sector_breadth"), market_context.get("prev_sector_breadth"), default=math.nan)
        current_breadth = _float_value(holding.get("sector_breadth"), market_context.get("sector_breadth"), default=math.nan)
        if _is_number(prev_breadth) and _is_number(current_breadth) and prev_breadth - current_breadth >= self.config.sector_breadth_drop_pct:
            reasons.append(f"板块广度从 {prev_breadth:.0%} 降至 {current_breadth:.0%}")

        return reasons

    def _replacement_candidate(
        self,
        holding: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any] | None:
        current_symbol = _text(holding.get("symbol"))
        current_sector = _text(holding.get("sector"))
        current_score = _float_value(holding.get("current_score"), holding.get("score"), default=0.0)
        valid: list[Mapping[str, Any]] = []

        for candidate in candidates:
            if _text(candidate.get("symbol")) == current_symbol:
                continue
            if current_sector and _text(candidate.get("sector")) == current_sector:
                continue
            if not _candidate_buy_quality_passed(candidate, self.config.replacement_min_buy_quality):
                continue
            score = _float_value(candidate.get("score"), default=math.nan)
            if not _is_number(score) or score - current_score < self.config.replacement_score_gap:
                continue
            valid.append(candidate)

        if not valid:
            return None
        return max(valid, key=lambda item: _float_value(item.get("score"), default=0.0))

    def _data_abnormal_reason(self, holding: Mapping[str, Any]) -> str:
        explicit_abnormal = _bool_value(holding.get("data_abnormal"), default=False)
        quality_passed = _bool_value(holding.get("data_quality_passed"), default=True)
        liquidity_ok = _bool_value(holding.get("liquidity_ok"), default=True)
        price = _price(holding, ("current_price", "latest_price", "price", "close", "sell_price"))

        if explicit_abnormal:
            return _text(holding.get("data_warning") or holding.get("quality_warning") or "行情被标记为异常")
        if not quality_passed:
            return _text(holding.get("data_warning") or holding.get("quality_warning") or "数据质量未通过")
        if not liquidity_ok:
            return _text(holding.get("liquidity_warning") or "流动性不足")
        if price is None or price <= 0:
            return "参考卖出价格缺失或非正数"
        return ""


def _candidate_buy_quality_passed(candidate: Mapping[str, Any], min_quality: float) -> bool:
    action = _text(candidate.get("buy_action") or candidate.get("entry_action") or candidate.get("action"))
    if action in QUALIFIED_BUY_ACTIONS:
        return True
    if _bool_value(candidate.get("buy_quality_passed"), default=False):
        return True
    quality = _float_value(candidate.get("buy_quality"), candidate.get("entry_quality"), candidate.get("confidence"), default=math.nan)
    return _is_number(quality) and quality >= min_quality


def _negative_acceleration_days(holding: Mapping[str, Any]) -> int:
    explicit = _int_value(holding, "acceleration_negative_days", "negative_acceleration_days")
    if explicit > 0:
        return explicit

    series = holding.get("acceleration_series")
    if not isinstance(series, Sequence) or isinstance(series, (str, bytes)):
        acceleration = _float_value(holding.get("acceleration"), default=math.nan)
        return 1 if _is_number(acceleration) and acceleration < 0 else 0

    count = 0
    for value in reversed(series):
        numeric = _float_value(value, default=math.nan)
        if _is_number(numeric) and numeric < 0:
            count += 1
        else:
            break
    return count


def _drawdown_pct(current_price: float | None, peak_price: float | None) -> float | None:
    if current_price is None or peak_price is None or peak_price <= 0:
        return None
    return max(0.0, (peak_price - current_price) / peak_price)


def _price(row: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    value = _float_value(*(row.get(key) for key in keys), default=math.nan)
    if not _is_number(value):
        return None
    return value


def _first_value(rows: Sequence[Mapping[str, Any]], key: str) -> str:
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _float_value(*values: Any, default: float = 0.0) -> float:
    for value in values:
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isnan(number):
            return number
    return default


def _int_value(row: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _bool_value(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是", "通过", "正常"}
    return bool(value)


def _is_number(value: float) -> bool:
    return not math.isnan(value) and math.isfinite(value)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _is_weak_market(value: Any) -> bool:
    return _text(value).strip().lower() in DEFENSIVE_STATES


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
