from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
ACTION_RESPONSE_FIELDS = ("success", "message", "task_id", "data", "error", "timestamp")


def now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")


def action_response(
    *,
    success: bool,
    message: str,
    task_id: str | None = None,
    data: dict[str, Any] | list[Any] | None = None,
    error: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    return {
        "success": bool(success),
        "message": str(message),
        "task_id": task_id or "",
        "data": data if data is not None else {},
        "error": error or "",
        "timestamp": timestamp or now_iso(),
    }


def format_datetime_shanghai(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return ""
    return parsed.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def format_trade_date(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        text = str(value or "").strip()
        return text[:10] if len(text) >= 10 else ""
    return parsed.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d")


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text[:19])
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed
