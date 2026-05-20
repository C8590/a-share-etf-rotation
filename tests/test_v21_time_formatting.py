from __future__ import annotations

from api.action_schema import format_datetime_shanghai, format_trade_date


def test_format_datetime_shanghai_uses_display_format_without_iso_raw():
    formatted = format_datetime_shanghai("2026-05-20T21:08:42+08:00")

    assert formatted == "2026-05-20 21:08:42"
    assert "T" not in formatted
    assert "+08:00" not in formatted


def test_format_datetime_shanghai_converts_utc_to_beijing_time():
    assert format_datetime_shanghai("2026-05-20T13:08:42+00:00") == "2026-05-20 21:08:42"


def test_format_trade_date_uses_date_only():
    assert format_trade_date("2026-05-20T21:08:42+08:00") == "2026-05-20"
