"""Right-side dual-layer momentum learning engine.

This module reviews completed trades and writes ``learning_report.csv``.  It is
deliberately standalone: the engine only produces review rows and adjustment
advice, and never mutates live trading parameters.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from contracts.signal_schema import LEARNING_REPORT_FIELDS

OUTPUT_FILE = "learning_report.csv"
REQUIRED_OUTPUT_FIELDS = LEARNING_REPORT_FIELDS
FORWARD_WINDOWS = (1, 3, 5, 10)

FAILURE_ATTRIBUTIONS = (
    "买在尾段",
    "买点太差",
    "市场转防守",
    "同板块集中",
    "频繁换仓",
    "卖早",
    "卖晚",
    "数据或流动性问题",
)

HEALTHY = "健康"
NORMAL = "一般"
WEAK = "较差"
FAILED = "失效"
HEALTH_LEVELS = (HEALTHY, NORMAL, WEAK, FAILED)


@dataclass(frozen=True)
class TradeReview:
    trade_id: str
    symbol: str
    name: str
    trade_date: str
    holding_days: int
    return_pct: float | None
    attribution: str
    lesson: str
    adjustment: str
    source_file: str


class LearningEngine:
    """Produce contract-compliant learning rows for completed trades."""

    def __init__(self, output_file: str = OUTPUT_FILE) -> None:
        self.output_file = output_file
        self.buy_snapshots: dict[str, dict[str, Any]] = {}
        self.sell_snapshots: dict[str, dict[str, Any]] = {}

    def record_buy_snapshot(self, trade_id: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Record a buy-side snapshot for later review."""
        saved = _jsonable_snapshot(snapshot)
        self.buy_snapshots[str(trade_id)] = saved
        return saved

    def record_sell_snapshot(self, trade_id: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Record a sell-side snapshot for later review."""
        saved = _jsonable_snapshot(snapshot)
        self.sell_snapshots[str(trade_id)] = saved
        return saved

    def run(
        self,
        closed_trades: Sequence[Mapping[str, Any]] | None = None,
        output_dir: str | Path | None = "output",
    ) -> list[dict[str, Any]]:
        """Review completed trades and write ``learning_report.csv`` by default.

        ``closed_trades`` may contain English contract-style keys, common Chinese
        portfolio keys, or embedded ``buy_snapshot`` / ``sell_snapshot`` payloads.
        The returned and written rows always match ``REQUIRED_OUTPUT_FIELDS``.
        Pass ``output_dir=None`` to skip writing during tests or dry runs.
        """
        trades = list(closed_trades or [])
        generated_at = datetime.now().isoformat(timespec="seconds")

        reviews = [self._review_trade(trade) for trade in trades]
        health = self.evaluate_strategy_health(reviews)
        attribution_counts = Counter(review.attribution for review in reviews)

        rows = [
            self._to_output_row(review, health, attribution_counts, generated_at)
            for review in reviews
        ]

        if output_dir is not None:
            self._write_report(rows, output_dir)
        return rows

    def evaluate_strategy_health(self, reviews: Sequence[TradeReview]) -> str:
        """Classify strategy health from completed review rows."""
        if not reviews:
            return NORMAL

        returns = [review.return_pct for review in reviews if review.return_pct is not None]
        if not returns:
            return FAILED

        win_rate = sum(value > 0 for value in returns) / len(returns)
        average_return = sum(returns) / len(returns)
        severe_loss_rate = sum(value <= -0.05 for value in returns) / len(returns)
        data_issue_rate = sum(
            review.attribution == "数据或流动性问题" for review in reviews
        ) / len(reviews)

        if win_rate >= 0.6 and average_return >= 0 and severe_loss_rate <= 0.25:
            return HEALTHY
        if win_rate >= 0.45 and average_return >= -0.02 and data_issue_rate <= 0.3:
            return NORMAL
        if win_rate >= 0.25 and average_return >= -0.05 and data_issue_rate <= 0.5:
            return WEAK
        return FAILED

    def _review_trade(self, trade: Mapping[str, Any]) -> TradeReview:
        trade_id = str(_first_present(trade, "trade_id", "交易记录 ID", "交易ID", "id") or "")
        if not trade_id:
            trade_id = _build_trade_id(trade)

        buy_snapshot = {
            **self.buy_snapshots.get(trade_id, {}),
            **_as_mapping(trade.get("buy_snapshot")),
        }
        sell_snapshot = {
            **self.sell_snapshots.get(trade_id, {}),
            **_as_mapping(trade.get("sell_snapshot")),
        }

        symbol = _normal_symbol(_first_present(trade, "symbol", "ETF代码", "ETF浠ｇ爜"))
        name = str(_first_present(trade, "name", "ETF名称", "ETF鍚嶇О") or "")
        buy_date = _date_text(_first_present(trade, "buy_date", "买入日期", "最近买入日期"))
        sell_date = _date_text(_first_present(trade, "sell_date", "卖出日期", "trade_date", "日期"))
        buy_price = _safe_float(_first_present(trade, "buy_price", "成交买入价", "买入价"))
        sell_price = _safe_float(_first_present(trade, "sell_price", "成交卖出价", "卖出价", "成交价格"))
        shares = _safe_float(_first_present(trade, "shares", "成交份额", "持仓份额"), default=0.0)

        holding_days = _holding_days(trade, buy_date, sell_date)
        return_pct = _trade_return(trade, buy_price, sell_price)

        buy_forward = self._forward_returns(
            trade,
            action="buy",
            anchor_date=buy_date,
            anchor_price=buy_price,
        )
        sell_forward = self._forward_returns(
            trade,
            action="sell",
            anchor_date=sell_date,
            anchor_price=sell_price,
        )

        attribution = self._classify_failure(
            trade=trade,
            buy_snapshot=buy_snapshot,
            sell_snapshot=sell_snapshot,
            holding_days=holding_days,
            return_pct=return_pct,
            buy_forward=buy_forward,
            sell_forward=sell_forward,
            buy_price=buy_price,
            sell_price=sell_price,
            shares=shares,
        )

        lesson = self._build_lesson(
            attribution=attribution,
            return_pct=return_pct,
            buy_forward=buy_forward,
            sell_forward=sell_forward,
            buy_snapshot=buy_snapshot,
            sell_snapshot=sell_snapshot,
        )
        adjustment = self._base_adjustment(attribution)
        source_file = str(_first_present(trade, "source_file", "source", "来源文件") or "closed_trades")

        return TradeReview(
            trade_id=trade_id,
            symbol=symbol,
            name=name,
            trade_date=sell_date or buy_date,
            holding_days=holding_days,
            return_pct=return_pct,
            attribution=attribution,
            lesson=lesson,
            adjustment=adjustment,
            source_file=source_file,
        )

    def _forward_returns(
        self,
        trade: Mapping[str, Any],
        action: str,
        anchor_date: str,
        anchor_price: float | None,
    ) -> dict[int, float | None]:
        if anchor_price is None or anchor_price <= 0:
            return {window: None for window in FORWARD_WINDOWS}

        explicit_prices = _first_present(
            trade,
            f"{action}_future_prices",
            f"post_{action}_prices",
            f"{action}_forward_prices",
        )
        prices = _price_sequence(explicit_prices)
        if not prices:
            prices = _prices_after_date(trade.get("price_history"), anchor_date)

        returns: dict[int, float | None] = {}
        for window in FORWARD_WINDOWS:
            price = prices[window - 1] if len(prices) >= window else None
            returns[window] = (price / anchor_price - 1.0) if price is not None else None
        return returns

    def _classify_failure(
        self,
        *,
        trade: Mapping[str, Any],
        buy_snapshot: Mapping[str, Any],
        sell_snapshot: Mapping[str, Any],
        holding_days: int,
        return_pct: float | None,
        buy_forward: Mapping[int, float | None],
        sell_forward: Mapping[int, float | None],
        buy_price: float | None,
        sell_price: float | None,
        shares: float,
    ) -> str:
        if _flagged(trade, "data_issue", "liquidity_issue") or buy_price is None or buy_price <= 0:
            return "数据或流动性问题"
        if sell_price is None or sell_price <= 0 or shares <= 0:
            return "数据或流动性问题"

        if holding_days <= 2 or _safe_float(trade.get("recent_rotation_count"), 0.0) >= 3:
            return "频繁换仓"

        if (
            _safe_float(trade.get("same_sector_count"), 0.0) >= 2
            or _safe_float(trade.get("sector_exposure"), 0.0) >= 0.6
        ):
            return "同板块集中"

        if _is_defensive(_first_present(trade, "market_state_after", "sell_market_state", "market_state")):
            return "市场转防守"

        if _is_late_stage(buy_snapshot) or (
            _safe_float(_first_present(buy_snapshot, "pre_buy_return_20d", "momentum_20d"), 0.0) >= 0.18
            and _min_present(buy_forward.values()) is not None
            and _min_present(buy_forward.values()) < -0.02
        ):
            return "买在尾段"

        if _min_present((buy_forward.get(1), buy_forward.get(3))) is not None and _min_present((buy_forward.get(1), buy_forward.get(3))) <= -0.02:
            return "买点太差"

        best_after_sell = _max_present(sell_forward.values())
        if best_after_sell is not None and best_after_sell >= 0.03:
            return "卖早"

        if return_pct is not None and return_pct <= -0.03:
            return "卖晚"

        return "卖早" if best_after_sell is not None and best_after_sell > 0 else "买点太差"

    def _build_lesson(
        self,
        *,
        attribution: str,
        return_pct: float | None,
        buy_forward: Mapping[int, float | None],
        sell_forward: Mapping[int, float | None],
        buy_snapshot: Mapping[str, Any],
        sell_snapshot: Mapping[str, Any],
    ) -> str:
        trade_result = "交易收益未知" if return_pct is None else f"交易收益{_pct(return_pct)}"
        buy_text = _format_forward("买后收益", buy_forward)
        sell_text = _format_forward("卖后走势", sell_forward)
        buy_snapshot_text = _snapshot_digest("买入快照", buy_snapshot)
        sell_snapshot_text = _snapshot_digest("卖出快照", sell_snapshot)
        return "；".join(
            part
            for part in [
                trade_result,
                f"归因：{attribution}",
                buy_text,
                sell_text,
                buy_snapshot_text,
                sell_snapshot_text,
            ]
            if part
        )

    def _to_output_row(
        self,
        review: TradeReview,
        health: str,
        attribution_counts: Counter[str],
        generated_at: str,
    ) -> dict[str, Any]:
        row = {
            "trade_date": review.trade_date,
            "trade_id": review.trade_id,
            "symbol": review.symbol,
            "name": review.name,
            "holding_days": review.holding_days,
            "return_pct": "" if review.return_pct is None else round(review.return_pct, 6),
            "failure_attribution": review.attribution,
            "lesson": f"{review.lesson}；策略健康度：{health}",
            "adjustment": self._with_health_advice(
                review.adjustment,
                health,
                attribution_counts,
            ),
            "source_file": review.source_file,
            "generated_at": generated_at,
        }
        return {field: row.get(field, "") for field in REQUIRED_OUTPUT_FIELDS}

    def _with_health_advice(
        self,
        adjustment: str,
        health: str,
        attribution_counts: Counter[str],
    ) -> str:
        frequent_issue = attribution_counts.most_common(1)[0][0] if attribution_counts else ""
        parts = [adjustment, f"策略健康度：{health}"]
        if frequent_issue:
            parts.append(f"重点复盘高频问题：{frequent_issue}")
        if health in {WEAK, FAILED}:
            parts.append("建议降低试探仓频率并收紧候选池确认条件")
        parts.append("仅给出建议，不自动修改交易参数")
        return "；".join(part for part in parts if part)

    def _base_adjustment(self, attribution: str) -> str:
        advice = {
            "买在尾段": "提高右侧动量延续确认门槛，避免连续加速后追入",
            "买点太差": "等待回踩或二次确认后再执行标准买入",
            "市场转防守": "增强市场状态过滤，防守期降低开仓优先级",
            "同板块集中": "限制同主题暴露，优先保留强度最高的一档",
            "频繁换仓": "延长最短持有观察期，减少噪声触发的切换",
            "卖早": "对强趋势仓位采用分批止盈或移动止盈观察",
            "卖晚": "强化破位和回撤纪律，避免亏损扩大后才处理",
            "数据或流动性问题": "补充数据质量和成交额过滤，低流动性标的谨慎处理",
        }
        return advice.get(attribution, "保持当前参数，继续积累样本")

    def _write_report(self, rows: Sequence[Mapping[str, Any]], output_dir: str | Path) -> None:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        report_path = output_path / self.output_file
        with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(REQUIRED_OUTPUT_FIELDS))
            writer.writeheader()
            writer.writerows(rows)


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in ("", None):
            return mapping[key]
    return None


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _jsonable_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(snapshot), ensure_ascii=False, default=str))


def _normal_symbol(value: Any) -> str:
    raw = str(value or "").strip()
    return raw.zfill(6) if raw.isdigit() else raw


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in ("", None):
            return default
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date_text(value: Any) -> str:
    if value in ("", None):
        return ""
    if hasattr(value, "date") and callable(value.date):
        return value.date().isoformat()
    return str(value)[:10]


def _holding_days(trade: Mapping[str, Any], buy_date: str, sell_date: str) -> int:
    explicit = _safe_float(_first_present(trade, "holding_days", "持仓天数"), None)
    if explicit is not None:
        return int(explicit)
    if buy_date and sell_date:
        try:
            return max((datetime.fromisoformat(sell_date) - datetime.fromisoformat(buy_date)).days, 0)
        except ValueError:
            return 0
    return 0


def _trade_return(
    trade: Mapping[str, Any],
    buy_price: float | None,
    sell_price: float | None,
) -> float | None:
    explicit = _safe_float(_first_present(trade, "return_pct", "收益率", "交易收益率"), None)
    if explicit is not None:
        return explicit
    if buy_price is None or buy_price <= 0 or sell_price is None:
        return None
    return sell_price / buy_price - 1.0


def _price_sequence(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [_safe_float(item) for _, item in sorted(value.items()) if _safe_float(item) is not None]
    prices: list[float] = []
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, Mapping):
                price = _safe_float(_first_present(item, "close", "price", "收盘价", "成交价格"))
            else:
                price = _safe_float(item)
            if price is not None:
                prices.append(price)
    return prices


def _prices_after_date(value: Any, anchor_date: str) -> list[float]:
    if not anchor_date:
        return []
    rows: list[tuple[str, float]] = []
    if isinstance(value, Mapping):
        for date_value, price_value in value.items():
            price = _safe_float(price_value)
            if price is not None:
                rows.append((_date_text(date_value), price))
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            date_value = _date_text(_first_present(item, "date", "trade_date", "日期"))
            price = _safe_float(_first_present(item, "close", "price", "收盘价", "成交价格"))
            if date_value and price is not None:
                rows.append((date_value, price))
    return [price for date_value, price in sorted(rows) if date_value > anchor_date]


def _flagged(mapping: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = mapping.get(key)
        if value in (True, "true", "True", "是", 1, "1"):
            return True
    return False


def _is_defensive(value: Any) -> bool:
    text = str(value or "").lower()
    return "防" in text or "defense" in text or "defensive" in text


def _is_late_stage(snapshot: Mapping[str, Any]) -> bool:
    stage = str(_first_present(snapshot, "momentum_stage", "stage", "动量阶段") or "").lower()
    return any(token in stage for token in ("late", "tail", "尾", "末"))


def _min_present(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _max_present(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:.2f}%"


def _format_forward(label: str, returns: Mapping[int, float | None]) -> str:
    return f"{label}：" + "，".join(f"{window}日{_pct(returns.get(window))}" for window in FORWARD_WINDOWS)


def _snapshot_digest(label: str, snapshot: Mapping[str, Any]) -> str:
    if not snapshot:
        return ""
    keys = ("market_state", "momentum_stage", "rank", "score", "sector", "reason")
    digest = {key: snapshot[key] for key in keys if key in snapshot and snapshot[key] not in ("", None)}
    if not digest:
        digest = dict(list(snapshot.items())[:4])
    return f"{label}：" + json.dumps(digest, ensure_ascii=False, sort_keys=True)


def _build_trade_id(trade: Mapping[str, Any]) -> str:
    symbol = _normal_symbol(_first_present(trade, "symbol", "ETF代码", "ETF浠ｇ爜"))
    buy_date = _date_text(_first_present(trade, "buy_date", "买入日期", "最近买入日期"))
    sell_date = _date_text(_first_present(trade, "sell_date", "卖出日期", "trade_date", "日期"))
    return "-".join(part for part in (symbol, buy_date, sell_date) if part) or "trade-unknown"
