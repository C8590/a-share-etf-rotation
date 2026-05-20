from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, List, Optional, Set

from .contracts import Action, OrderIntent, PriceType


@dataclass
class RiskRuleResult:
    code: str
    passed: bool
    message: str


@dataclass
class RiskCheckResult:
    passed: bool
    results: List[RiskRuleResult]

    def failed_codes(self) -> List[str]:
        return [r.code for r in self.results if not r.passed]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "results": [r.__dict__ for r in self.results],
            "failed_codes": self.failed_codes(),
        }


@dataclass
class RiskContext:
    """执行前风控上下文。

    这些字段由总控、风险模块、持仓系统、行情系统和订单系统注入。
    """

    account_total_asset: float
    risk_freeze_level: Optional[str] = None  # R3/R4 冻结
    p0_manual_takeover: bool = False
    equity_position_limit: float = 1.0
    single_etf_position_limit: float = 0.25
    sector_exposure_limit: float = 0.40
    current_equity_weight: float = 0.0
    current_single_weight_by_code: Dict[str, float] = field(default_factory=dict)
    current_sector_weight_by_sector: Dict[str, float] = field(default_factory=dict)
    code_to_sector: Dict[str, str] = field(default_factory=dict)
    last_price_by_code: Dict[str, float] = field(default_factory=dict)
    avg_daily_turnover_by_code: Dict[str, float] = field(default_factory=dict)
    min_turnover: float = 20_000_000.0
    max_order_participation: float = 0.10
    max_limit_price_deviation: float = 0.03
    recent_order_keys: Set[str] = field(default_factory=set)
    trading_time_valid: bool = True
    positions_synced: bool = False
    manual_confirm_required: bool = True


class RiskEngine:
    """执行风控。

    任一规则失败，ExecutionService 不允许调用 broker.place_order。
    """

    def check(self, intent: OrderIntent, ctx: RiskContext) -> RiskCheckResult:
        results: List[RiskRuleResult] = []

        def add(code: str, passed: bool, message: str) -> None:
            results.append(RiskRuleResult(code=code, passed=passed, message=message))

        add(
            "risk_freeze_R3_R4",
            ctx.risk_freeze_level not in {"R3", "R4"},
            f"risk_freeze_level={ctx.risk_freeze_level}",
        )
        add("p0_manual_takeover", not ctx.p0_manual_takeover, f"p0_manual_takeover={ctx.p0_manual_takeover}")
        add("valid_quantity", intent.quantity > 0 and intent.quantity % 100 == 0, "ETF/股票首版要求数量为正且按100份/股取整")
        add("trading_time_valid", ctx.trading_time_valid, f"trading_time_valid={ctx.trading_time_valid}")
        add("positions_synced", ctx.positions_synced, f"positions_synced={ctx.positions_synced}")

        if intent.price_type == PriceType.LIMIT:
            add("limit_price_required", intent.limit_price is not None and intent.limit_price > 0, "限价单必须提供正数价格")
        else:
            add("price_type_allowed", intent.price_type in {PriceType.MARKET, PriceType.BEST_5}, f"price_type={intent.price_type}")

        if intent.action == Action.BUY:
            projected_equity_weight = max(ctx.current_equity_weight, intent.target_weight)
            add(
                "equity_position_limit",
                projected_equity_weight <= ctx.equity_position_limit,
                f"projected_equity_weight={projected_equity_weight:.4f}, limit={ctx.equity_position_limit:.4f}",
            )
            current_single = ctx.current_single_weight_by_code.get(intent.code, 0.0)
            projected_single = max(current_single, intent.target_weight)
            add(
                "single_etf_position_limit",
                projected_single <= ctx.single_etf_position_limit,
                f"projected_single_weight={projected_single:.4f}, limit={ctx.single_etf_position_limit:.4f}",
            )
            sector = ctx.code_to_sector.get(intent.code, "UNKNOWN")
            current_sector = ctx.current_sector_weight_by_sector.get(sector, 0.0)
            projected_sector = max(current_sector, current_sector + max(intent.target_weight - current_single, 0.0))
            add(
                "sector_exposure_limit",
                projected_sector <= ctx.sector_exposure_limit,
                f"sector={sector}, projected_sector_weight={projected_sector:.4f}, limit={ctx.sector_exposure_limit:.4f}",
            )

        last_price = ctx.last_price_by_code.get(intent.code)
        if last_price is not None and intent.limit_price is not None:
            deviation = abs(intent.limit_price / last_price - 1)
            add(
                "price_anomaly",
                deviation <= ctx.max_limit_price_deviation,
                f"last_price={last_price}, limit_price={intent.limit_price}, deviation={deviation:.4f}",
            )
        else:
            add("price_anomaly", False, "缺少最新价或限价，无法检查价格异常")

        adv = ctx.avg_daily_turnover_by_code.get(intent.code, 0.0)
        add("turnover_minimum", adv >= ctx.min_turnover, f"avg_daily_turnover={adv:.0f}, min={ctx.min_turnover:.0f}")
        add(
            "order_participation",
            intent.target_amount <= adv * ctx.max_order_participation if adv > 0 else False,
            f"target_amount={intent.target_amount:.2f}, max_allowed={adv * ctx.max_order_participation:.2f}",
        )

        order_key = self.order_key(intent)
        add("duplicate_order", order_key not in ctx.recent_order_keys, f"order_key={order_key}")

        manual_ok = (not ctx.manual_confirm_required and not intent.requires_manual_confirm) or intent.manual_confirmed
        add(
            "manual_confirmation",
            manual_ok,
            f"requires_manual_confirm={intent.requires_manual_confirm}, manual_confirmed={intent.manual_confirmed}",
        )

        passed = all(r.passed for r in results)
        return RiskCheckResult(passed=passed, results=results)

    @staticmethod
    def order_key(intent: OrderIntent) -> str:
        return f"{intent.trade_date}:{intent.action.value}:{intent.code}:{intent.quantity}:{intent.limit_price}"
