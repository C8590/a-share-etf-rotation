from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


CALENDAR_DIR = Path("data") / "calendar"
DEFAULT_CALENDAR_PATH = CALENDAR_DIR / "a_share_trading_calendar.csv"
OUTPUT_AUDIT_PATH = Path("output") / "trading_calendar_audit.csv"
CALENDAR_VERSION = "1.0"
EXCHANGE = "A_SHARE"
AUDIT_COLUMNS = [
    "calendar_file",
    "exists",
    "source",
    "start_date",
    "end_date",
    "row_count",
    "open_day_count",
    "latest_open_day",
    "today",
    "coverage_gap_days",
    "used_fallback",
    "status",
    "reason",
]


class TradingCalendarError(RuntimeError):
    pass


@dataclass
class CalendarLoadResult:
    frame: pd.DataFrame
    source: str
    path: Path
    used_fallback: bool = False
    warning: str = ""


def _calendar_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else DEFAULT_CALENDAR_PATH


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _normalize_calendar_frame(calendar_df: pd.DataFrame, source: str = "local_snapshot") -> pd.DataFrame:
    if calendar_df is None or calendar_df.empty:
        raise TradingCalendarError("trading calendar is empty")
    frame = calendar_df.copy()
    if "date" not in frame.columns:
        first_col = frame.columns[0]
        frame = frame.rename(columns={first_col: "date"})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    if frame.empty:
        raise TradingCalendarError("trading calendar has no valid date rows")
    if "is_open" not in frame.columns:
        frame["is_open"] = True
    frame["is_open"] = frame["is_open"].apply(_bool_value)
    frame["exchange"] = frame.get("exchange", EXCHANGE)
    frame["source"] = frame.get("source", source)
    frame["calendar_version"] = frame.get("calendar_version", CALENDAR_VERSION)
    frame["generated_at"] = frame.get("generated_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    frame["note"] = frame.get("note", "")
    frame = frame.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    return frame[["date", "is_open", "exchange", "source", "calendar_version", "generated_at", "note"]]


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y", "open", "交易", "开市"}


def _akshare_calendar_frame(start_year: int, end_year: int) -> pd.DataFrame:
    try:
        import akshare as ak
    except ImportError as exc:
        raise TradingCalendarError("AKShare is not installed") from exc

    try:
        raw = ak.tool_trade_date_hist_sina()
    except Exception as exc:  # noqa: BLE001
        raise TradingCalendarError(f"AKShare trade calendar failed: {exc}") from exc

    if raw is None or raw.empty:
        raise TradingCalendarError("AKShare trade calendar returned empty data")
    col = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
    dates = pd.to_datetime(raw[col], errors="coerce").dropna().dt.normalize()
    start = pd.Timestamp(year=start_year, month=1, day=1)
    end = pd.Timestamp(year=end_year, month=12, day=31)
    dates = pd.DatetimeIndex(sorted(dates[(dates >= start) & (dates <= end)].unique()))
    if dates.empty:
        raise TradingCalendarError(f"AKShare calendar has no rows for {start_year}-{end_year}")
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return pd.DataFrame(
        {
            "date": dates,
            "is_open": True,
            "exchange": EXCHANGE,
            "source": "akshare.tool_trade_date_hist_sina",
            "calendar_version": CALENDAR_VERSION,
            "generated_at": generated_at,
            "note": "",
        }
    )


def _weekday_fallback_frame(start_date: str | pd.Timestamp, end_date: str | pd.Timestamp) -> pd.DataFrame:
    days = pd.bdate_range(start=pd.Timestamp(start_date), end=pd.Timestamp(end_date))
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return pd.DataFrame(
        {
            "date": days,
            "is_open": True,
            "exchange": EXCHANGE,
            "source": "weekday_fallback",
            "calendar_version": CALENDAR_VERSION,
            "generated_at": generated_at,
            "note": "WARNING: weekday fallback is not an authoritative A-share trading calendar",
        }
    )


def load_local_trading_calendar(path: str | Path | None = None) -> pd.DataFrame:
    calendar_file = _calendar_path(path)
    if not calendar_file.exists():
        raise FileNotFoundError(f"Trading calendar snapshot not found: {calendar_file}")
    try:
        raw = pd.read_csv(calendar_file)
    except Exception as exc:  # noqa: BLE001
        raise TradingCalendarError(f"failed to read trading calendar snapshot: {exc}") from exc
    return _normalize_calendar_frame(raw, source="local_snapshot")


def save_trading_calendar_snapshot(calendar_df: pd.DataFrame, path: str | Path | None = None) -> Path:
    calendar_file = _calendar_path(path)
    calendar_file.parent.mkdir(parents=True, exist_ok=True)
    frame = _normalize_calendar_frame(calendar_df, source="local_snapshot")
    out = frame.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(calendar_file, index=False, encoding="utf-8-sig")
    return calendar_file


def refresh_a_share_trading_calendar(start_year: int | None = None, end_year: int | None = None) -> pd.DataFrame:
    today = pd.Timestamp.today().normalize()
    start = int(start_year or max(2010, today.year - 15))
    end = int(end_year or today.year + 1)
    return _akshare_calendar_frame(start, end)


def _load_calendar_result(
    path: str | Path | None = None,
    *,
    allow_runtime_refresh: bool = True,
    allow_weekday_fallback: bool = False,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
) -> CalendarLoadResult:
    calendar_file = _calendar_path(path)
    try:
        return CalendarLoadResult(load_local_trading_calendar(calendar_file), "local_snapshot", calendar_file)
    except Exception as local_exc:  # noqa: BLE001
        if allow_runtime_refresh:
            try:
                start_year = pd.Timestamp(start_date).year if start_date is not None else None
                end_year = pd.Timestamp(end_date).year if end_date is not None else None
                frame = refresh_a_share_trading_calendar(start_year=start_year, end_year=end_year)
                save_trading_calendar_snapshot(frame, calendar_file)
                return CalendarLoadResult(frame, "akshare_runtime", calendar_file, warning=f"local snapshot unavailable: {local_exc}")
            except Exception as ak_exc:  # noqa: BLE001
                if allow_weekday_fallback:
                    start = start_date or pd.Timestamp.today().normalize() - pd.Timedelta(days=365)
                    end = end_date or pd.Timestamp.today().normalize() + pd.Timedelta(days=365)
                    frame = _weekday_fallback_frame(start, end)
                    warning = f"local snapshot unavailable: {local_exc}; AKShare unavailable: {ak_exc}"
                    return CalendarLoadResult(frame, "weekday_fallback", calendar_file, used_fallback=True, warning=warning)
                raise TradingCalendarError(f"local snapshot unavailable: {local_exc}; AKShare unavailable: {ak_exc}") from ak_exc
        if allow_weekday_fallback:
            start = start_date or pd.Timestamp.today().normalize() - pd.Timedelta(days=365)
            end = end_date or pd.Timestamp.today().normalize() + pd.Timedelta(days=365)
            frame = _weekday_fallback_frame(start, end)
            return CalendarLoadResult(frame, "weekday_fallback", calendar_file, used_fallback=True, warning=str(local_exc))
        raise


def get_trading_days(
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    *,
    path: str | Path | None = None,
    allow_runtime_refresh: bool = True,
    allow_weekday_fallback: bool = False,
) -> pd.DatetimeIndex:
    result = _load_calendar_result(
        path,
        allow_runtime_refresh=allow_runtime_refresh,
        allow_weekday_fallback=allow_weekday_fallback,
        start_date=start_date,
        end_date=end_date,
    )
    frame = result.frame[result.frame["is_open"]].copy()
    if start_date is not None:
        frame = frame[frame["date"] >= pd.Timestamp(start_date).normalize()]
    if end_date is not None:
        frame = frame[frame["date"] <= pd.Timestamp(end_date).normalize()]
    return pd.DatetimeIndex(frame["date"])


def is_trading_day(date: str | pd.Timestamp, **kwargs: Any) -> bool:
    target = pd.Timestamp(date).normalize()
    days = get_trading_days(target, target, **kwargs)
    return bool(len(days) and pd.Timestamp(days[0]).normalize() == target)


def previous_trading_day(date: str | pd.Timestamp, **kwargs: Any) -> pd.Timestamp:
    target = pd.Timestamp(date).normalize()
    days = get_trading_days(end_date=target - pd.Timedelta(days=1), **kwargs)
    if days.empty:
        raise TradingCalendarError(f"no previous trading day before {target.date()}")
    return pd.Timestamp(days[-1]).normalize()


def next_trading_day(date: str | pd.Timestamp, **kwargs: Any) -> pd.Timestamp:
    target = pd.Timestamp(date).normalize()
    days = get_trading_days(start_date=target + pd.Timedelta(days=1), **kwargs)
    if days.empty:
        raise TradingCalendarError(f"no next trading day after {target.date()}")
    return pd.Timestamp(days[0]).normalize()


def latest_trading_day_on_or_before(date: str | pd.Timestamp, **kwargs: Any) -> pd.Timestamp:
    target = pd.Timestamp(date).normalize()
    days = get_trading_days(end_date=target, **kwargs)
    if days.empty:
        raise TradingCalendarError(f"no trading day on or before {target.date()}")
    return pd.Timestamp(days[-1]).normalize()


def validate_trading_calendar_snapshot(path: str | Path | None = None) -> dict[str, Any]:
    calendar_file = _calendar_path(path)
    if not calendar_file.exists():
        return {"valid": False, "reason": f"missing calendar snapshot: {calendar_file}"}
    try:
        frame = load_local_trading_calendar(calendar_file)
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "reason": str(exc)}
    required = {"date", "is_open", "exchange", "source", "calendar_version", "generated_at", "note"}
    if not required.issubset(frame.columns):
        return {"valid": False, "reason": f"missing columns: {', '.join(sorted(required - set(frame.columns)))}"}
    if frame["date"].duplicated().any():
        return {"valid": False, "reason": "duplicate calendar dates"}
    if not frame["date"].is_monotonic_increasing:
        return {"valid": False, "reason": "calendar dates are not ascending"}
    if not frame["is_open"].any():
        return {"valid": False, "reason": "calendar has no open days"}
    return {"valid": True, "reason": "ok"}


def audit_trading_calendar(
    output_dir: str | Path = "output",
    path: str | Path | None = None,
    today: str | pd.Timestamp | None = None,
    stale_after_days: int = 7,
    allow_runtime_refresh: bool = True,
    allow_weekday_fallback: bool = False,
) -> dict[str, Any]:
    calendar_file = _calendar_path(path)
    today_ts = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp.today().normalize()
    exists_before = calendar_file.exists()
    source = ""
    used_fallback = False
    reason = ""
    status = "unknown"
    frame = pd.DataFrame()

    try:
        result = _load_calendar_result(
            calendar_file,
            allow_runtime_refresh=allow_runtime_refresh,
            allow_weekday_fallback=allow_weekday_fallback,
            start_date=today_ts - pd.Timedelta(days=370),
            end_date=today_ts + pd.Timedelta(days=370),
        )
        frame = result.frame
        source = result.source
        used_fallback = result.used_fallback
        reason = result.warning
    except FileNotFoundError as exc:
        status = "error_missing_calendar"
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001
        status = "error_invalid_calendar"
        reason = str(exc)

    start_date = end_date = latest_open_day = ""
    row_count = open_day_count = coverage_gap_days = 0
    if not frame.empty:
        open_days = frame[frame["is_open"]]["date"]
        start_date = _date_text(frame["date"].min())
        end_date = _date_text(frame["date"].max())
        row_count = int(len(frame))
        open_day_count = int(len(open_days))
        latest_before = open_days[open_days <= today_ts]
        latest_open = pd.Timestamp(latest_before.max()).normalize() if not latest_before.empty else pd.NaT
        latest_open_day = "" if pd.isna(latest_open) else str(latest_open.date())
        last_calendar_open = pd.Timestamp(open_days.max()).normalize() if not open_days.empty else pd.NaT
        if pd.isna(last_calendar_open):
            coverage_gap_days = 0
        else:
            coverage_gap_days = max(0, int((today_ts - last_calendar_open).days))
        if used_fallback or source == "weekday_fallback":
            status = "warning_weekday_fallback"
            reason = reason or "using weekday fallback calendar"
        elif source == "akshare_runtime":
            status = "warning_using_akshare_runtime"
            reason = reason or "local snapshot was generated from AKShare at runtime"
        elif coverage_gap_days > stale_after_days:
            status = "warning_calendar_stale"
            reason = f"calendar latest open day {latest_open_day} is {coverage_gap_days} day(s) behind today {today_ts.date()}"
        elif status == "unknown":
            status = "ok"
            reason = reason or "local trading calendar snapshot is available"

    row = {
        "calendar_file": str(calendar_file),
        "exists": bool(exists_before or calendar_file.exists()),
        "source": source,
        "start_date": start_date,
        "end_date": end_date,
        "row_count": row_count,
        "open_day_count": open_day_count,
        "latest_open_day": latest_open_day,
        "today": str(today_ts.date()),
        "coverage_gap_days": int(coverage_gap_days),
        "used_fallback": bool(used_fallback),
        "status": status,
        "reason": reason,
    }
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row], columns=AUDIT_COLUMNS).to_csv(output_path / "trading_calendar_audit.csv", index=False, encoding="utf-8-sig")
    return row


def summarize_trading_calendar_audit(row: dict[str, Any]) -> dict[str, Any]:
    gap_value = pd.to_numeric(row.get("coverage_gap_days", 0), errors="coerce")
    gap_days = 0 if pd.isna(gap_value) else int(gap_value)
    return {
        "calendar_file": str(row.get("calendar_file", "")),
        "status": str(row.get("status", "unknown")),
        "source": str(row.get("source", "")),
        "start_date": str(row.get("start_date", "")),
        "end_date": str(row.get("end_date", "")),
        "latest_open_day": str(row.get("latest_open_day", "")),
        "coverage_gap_days": gap_days,
        "used_fallback": _bool_value(row.get("used_fallback", False)),
        "reason": str(row.get("reason", "")),
    }
