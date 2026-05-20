"""V2.1 backend integration contracts.

The contracts in this module describe the controller-level payloads consumed by
the future V2.1 frontend. They intentionally do not change strategy formulas or
the existing V1/V2 module contracts in ``contracts.signal_schema``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SIGNAL_VERSION = "V2.1_BACKEND_INTEGRATION"


DAILY_DECISION_FIELDS = (
    "trade_date",
    "signal_version",
    "market_state",
    "risk_level",
    "risk_score",
    "allow_entry",
    "freeze_entry",
    "manual_takeover_required",
    "selected_sectors",
    "candidate_etfs",
    "actual_buy_etfs",
    "entry_actions",
    "exit_actions",
    "portfolio_actions",
    "learning_summary",
    "historical_ml_summary",
    "order_intent_summary",
    "explain",
    "warnings",
    "fallback_reason",
    "generated_at",
)

RISK_GATE_FIELDS = (
    "trade_date",
    "risk_level",
    "risk_score",
    "freeze_entry",
    "equity_cap_override",
    "manual_takeover_required",
    "affected_sectors",
    "affected_etfs",
    "risk_events",
    "explain",
    "source",
)

TRAINING_SAMPLE_FIELDS = (
    "trade_date",
    "etf_code",
    "etf_name",
    "signal_type",
    "market_state",
    "sector",
    "entry_action",
    "exit_action",
    "confidence",
    "trend_maturity",
    "entry_quality",
    "post_924_regime",
    "ret_1d",
    "ret_3d",
    "ret_5d",
    "ret_10d",
    "hindsight_label",
    "failure_type",
    "calibration_suggestion",
    "explain",
)

ORDER_INTENT_FIELDS = (
    "trade_date",
    "etf_code",
    "etf_name",
    "action",
    "side",
    "target_weight",
    "current_weight",
    "delta_weight",
    "estimated_price",
    "estimated_amount",
    "order_type",
    "execution_mode",
    "requires_manual_confirm",
    "risk_check_passed",
    "risk_block_reason",
    "source_signal",
    "explain",
)

PORTFOLIO_SNAPSHOT_FIELDS = (
    "trade_date",
    "etf_code",
    "etf_name",
    "current_weight",
    "target_weight",
    "cost_price",
    "current_price",
    "pnl",
    "pnl_pct",
    "holding_days",
    "sector",
    "risk_status",
    "exit_action",
    "explain",
)


@dataclass(frozen=True)
class DailyDecision:
    trade_date: str
    signal_version: str = SIGNAL_VERSION
    market_state: str = ""
    risk_level: str = "R0"
    risk_score: int = 0
    allow_entry: bool = True
    freeze_entry: bool = False
    manual_takeover_required: bool = False
    selected_sectors: list[str] = field(default_factory=list)
    candidate_etfs: list[dict[str, Any]] = field(default_factory=list)
    actual_buy_etfs: list[dict[str, Any]] = field(default_factory=list)
    entry_actions: list[dict[str, Any]] = field(default_factory=list)
    exit_actions: list[dict[str, Any]] = field(default_factory=list)
    portfolio_actions: list[dict[str, Any]] = field(default_factory=list)
    learning_summary: list[dict[str, Any]] = field(default_factory=list)
    historical_ml_summary: list[dict[str, Any]] = field(default_factory=list)
    order_intent_summary: list[dict[str, Any]] = field(default_factory=list)
    explain: str = ""
    warnings: list[str] = field(default_factory=list)
    fallback_reason: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {field: asdict(self).get(field, "") for field in DAILY_DECISION_FIELDS}


@dataclass(frozen=True)
class V21RiskGate:
    trade_date: str
    risk_level: str = "R0"
    risk_score: int = 0
    freeze_entry: bool = False
    equity_cap_override: float = 1.0
    manual_takeover_required: bool = False
    affected_sectors: list[str] = field(default_factory=list)
    affected_etfs: list[str] = field(default_factory=list)
    risk_events: list[dict[str, Any]] = field(default_factory=list)
    explain: str = ""
    source: str = "risk_warning"

    def to_dict(self) -> dict[str, Any]:
        return {field: asdict(self).get(field, "") for field in RISK_GATE_FIELDS}


@dataclass(frozen=True)
class TrainingSample:
    trade_date: str
    etf_code: str = ""
    etf_name: str = ""
    signal_type: str = ""
    market_state: str = ""
    sector: str = ""
    entry_action: str = ""
    exit_action: str = ""
    confidence: Any = ""
    trend_maturity: str = ""
    entry_quality: str = ""
    post_924_regime: bool = True
    ret_1d: Any = ""
    ret_3d: Any = ""
    ret_5d: Any = ""
    ret_10d: Any = ""
    hindsight_label: str = ""
    failure_type: str = ""
    calibration_suggestion: str = ""
    explain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {field: asdict(self).get(field, "") for field in TRAINING_SAMPLE_FIELDS}


@dataclass(frozen=True)
class V21OrderIntent:
    trade_date: str
    etf_code: str
    etf_name: str = ""
    action: str = "DRAFT"
    side: str = ""
    target_weight: float = 0.0
    current_weight: float = 0.0
    delta_weight: float = 0.0
    estimated_price: Any = ""
    estimated_amount: Any = ""
    order_type: str = "LIMIT"
    execution_mode: str = "DRAFT"
    requires_manual_confirm: bool = True
    risk_check_passed: bool = True
    risk_block_reason: str = ""
    source_signal: str = ""
    explain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {field: asdict(self).get(field, "") for field in ORDER_INTENT_FIELDS}


@dataclass(frozen=True)
class PortfolioSnapshot:
    trade_date: str
    etf_code: str
    etf_name: str = ""
    current_weight: float = 0.0
    target_weight: float = 0.0
    cost_price: Any = ""
    current_price: Any = ""
    pnl: Any = ""
    pnl_pct: Any = ""
    holding_days: Any = ""
    sector: str = ""
    risk_status: str = ""
    exit_action: str = ""
    explain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {field: asdict(self).get(field, "") for field in PORTFOLIO_SNAPSHOT_FIELDS}
