from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Mapping


class EventType(str, Enum):
    TRADE_WAR = "trade_war"
    SANCTION = "sanction"
    EXPORT_CONTROL = "export_control"
    FX_SHOCK = "fx_shock"
    GEOPOLITICAL_CONFLICT = "geopolitical_conflict"
    OVERSEAS_CRASH = "overseas_crash"
    REGULATION_SHOCK = "regulation_shock"
    LIQUIDITY_RISK = "liquidity_risk"
    OTHER = "other"


class RiskLevel(str, Enum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class EventStatus(str, Enum):
    WATCH = "watch"
    ACTIVE = "active"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    IGNORED = "ignored"


class ExpectedDuration(str, Enum):
    ONE_DAY = "1d"
    THREE_DAYS = "3d"
    ONE_WEEK = "1w"
    TWO_WEEKS = "2w"
    UNKNOWN = "unknown"


ACTIVE_STATUSES = {EventStatus.WATCH.value, EventStatus.ACTIVE.value}
RISK_LEVEL_SCORE = {"R0": 0, "R1": 25, "R2": 50, "R3": 75, "R4": 100}
RISK_LEVEL_FLOOR = {"R0": 0, "R1": 20, "R2": 40, "R3": 60, "R4": 80}
RISK_LEVEL_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}
SCORE_LEVEL_BANDS = ((80, "R4"), (60, "R3"), (40, "R2"), (20, "R1"), (0, "R0"))
DURATION_TRADING_DAYS = {"1d": 1, "3d": 3, "1w": 5, "2w": 10, "unknown": 3}


def parse_date(value: Any) -> date | None:
    if value in ("", None):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text.isdigit() else text


def as_list(value: Any) -> list[str]:
    if value in ("", None):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.replace("，", ",").split(",") if item.strip()]


def _add_business_days(start: date, days: int) -> date:
    current = start
    remaining = max(int(days), 0)
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def inferred_expire_date(event_date: date, expected_duration: str) -> date:
    days = DURATION_TRADING_DAYS.get(str(expected_duration or "unknown"), 3)
    return _add_business_days(event_date, days)


def natural_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


@dataclass(frozen=True)
class RiskEvent:
    event_date: date
    event_type: str
    title: str
    description: str = ""
    source: str = "manual"
    risk_level: str = "R0"
    affected_assets: list[str] = field(default_factory=list)
    affected_sectors: list[str] = field(default_factory=list)
    expected_duration: str = "unknown"
    status: str = "watch"
    expire_date: date | None = None
    manual_confirmed: bool = False
    explain: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RiskEvent":
        event_date = parse_date(data.get("event_date"))
        if event_date is None:
            raise ValueError("risk event requires event_date in YYYY-MM-DD format")
        event_type = str(data.get("event_type") or EventType.OTHER.value).strip()
        if event_type not in {item.value for item in EventType}:
            raise ValueError(f"unsupported risk event_type: {event_type}")
        risk_level = str(data.get("risk_level") or "R0").strip().upper()
        if risk_level not in RISK_LEVEL_SCORE:
            raise ValueError(f"unsupported risk_level: {risk_level}")
        status = str(data.get("status") or EventStatus.WATCH.value).strip().lower()
        if status not in {item.value for item in EventStatus}:
            raise ValueError(f"unsupported risk event status: {status}")
        duration = str(data.get("expected_duration") or ExpectedDuration.UNKNOWN.value).strip().lower()
        if duration not in {item.value for item in ExpectedDuration}:
            duration = ExpectedDuration.UNKNOWN.value
        return cls(
            event_date=event_date,
            event_type=event_type,
            title=str(data.get("title") or "").strip(),
            description=str(data.get("description") or "").strip(),
            source=str(data.get("source") or "manual").strip(),
            risk_level=risk_level,
            affected_assets=[normalize_symbol(item) for item in as_list(data.get("affected_assets"))],
            affected_sectors=as_list(data.get("affected_sectors")),
            expected_duration=duration,
            status=status,
            expire_date=parse_date(data.get("expire_date")),
            manual_confirmed=natural_bool(data.get("manual_confirmed")),
            explain=str(data.get("explain") or "").strip(),
        )

    @property
    def effective_expire_date(self) -> date:
        return self.expire_date or inferred_expire_date(self.event_date, self.expected_duration)

    def is_effective_on(self, risk_date: date) -> bool:
        if self.status not in ACTIVE_STATUSES:
            return False
        if self.risk_level == RiskLevel.R0.value:
            return False
        if self.event_date > risk_date:
            return False
        return risk_date <= self.effective_expire_date

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_date": self.event_date.isoformat(),
            "event_type": self.event_type,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "risk_level": self.risk_level,
            "affected_assets": list(self.affected_assets),
            "affected_sectors": list(self.affected_sectors),
            "expected_duration": self.expected_duration,
            "status": self.status,
            "expire_date": self.expire_date.isoformat() if self.expire_date else "",
            "manual_confirmed": self.manual_confirmed,
            "explain": self.explain,
        }


@dataclass(frozen=True)
class RiskGate:
    risk_date: str
    risk_score: int
    risk_level: str
    overnight_risk: int
    event_risk: int
    market_fragility: int
    portfolio_exposure: int
    affected_sectors: list[str]
    freeze_entry: bool
    equity_cap_override: float
    require_manual_review: bool
    manual_takeover_required: bool
    explain: str
    active_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_date": self.risk_date,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "overnight_risk": self.overnight_risk,
            "event_risk": self.event_risk,
            "market_fragility": self.market_fragility,
            "portfolio_exposure": self.portfolio_exposure,
            "affected_sectors": list(self.affected_sectors),
            "freeze_entry": self.freeze_entry,
            "equity_cap_override": self.equity_cap_override,
            "require_manual_review": self.require_manual_review,
            "manual_takeover_required": self.manual_takeover_required,
            "explain": self.explain,
            "active_events": list(self.active_events),
        }
