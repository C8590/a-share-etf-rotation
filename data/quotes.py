from __future__ import annotations

import json
import math
from datetime import datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen

import pandas as pd
from zoneinfo import ZoneInfo

from data.storage import load_etf_data


MARKET_TZ = ZoneInfo("Asia/Shanghai")
QUOTE_CACHE_DIR = Path("data") / "quote_cache"
ACTIVE_PRICE_STATUSES = {"今日实时价", "今日收盘价", "最近交易日收盘价"}
BLOCKING_PRICE_STATUSES = {"昨日价格，需刷新", "价格异常，已停用", "数据不可用"}
_QUOTE_MEMORY: dict[str, dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.now(MARKET_TZ)


def _safe_float(value: Any) -> float | None:
    try:
        if value in ("", None) or pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result):
        return None
    return result


def _request_text(url: str, encoding: str = "utf-8", referer: str = "https://quote.eastmoney.com/") -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": referer,
            "Accept": "*/*",
        },
    )
    return urlopen(req, timeout=3).read().decode(encoding, errors="ignore")


def get_exchange_prefix(etf_code: str) -> dict[str, str]:
    code = str(etf_code).strip().zfill(6)
    if code.startswith("5"):
        exchange = "SH"
        market = "1"
        sina_prefix = "sh"
    elif code.startswith("1"):
        exchange = "SZ"
        market = "0"
        sina_prefix = "sz"
    else:
        raise ValueError(f"无法识别 ETF 交易所前缀：{code}")
    market_code = f"{sina_prefix}{code}"
    return {
        "exchange": exchange,
        "sina_code": market_code,
        "eastmoney_sec_id": f"{market}.{code}",
        "tencent_code": market_code,
    }


def _quote_cache_path(etf_code: str, source: str) -> Path:
    return QUOTE_CACHE_DIR / f"quote_{str(etf_code).zfill(6)}_{source}.json"


def _write_quote_cache(etf_code: str, source: str, payload: dict[str, Any]) -> None:
    QUOTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = {
        "etf_code": str(etf_code).zfill(6),
        "price": payload.get("latest_price"),
        "quote_date": payload.get("quote_date"),
        "quote_time": payload.get("quote_time"),
        "source": source,
        "cache_created_at": _now().isoformat(timespec="seconds"),
        "payload": payload,
    }
    _quote_cache_path(etf_code, source).write_text(json.dumps(cached, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _base_quote(code: str, source: str, error: str = "") -> dict[str, Any]:
    return {
        "code": str(code).zfill(6),
        "name": "",
        "latest_price": None,
        "prev_close": None,
        "open": None,
        "high": None,
        "low": None,
        "change": None,
        "pct_change": None,
        "volume": None,
        "amount": None,
        "quote_date": "",
        "quote_time": "",
        "source": source,
        "status": "数据不可用",
        "error": error,
    }


def get_etf_quote_from_eastmoney(etf_code: str) -> dict[str, Any]:
    code = str(etf_code).zfill(6)
    try:
        prefix = get_exchange_prefix(code)
        fields = "f57,f58,f43,f44,f45,f46,f47,f48,f60,f170,f169,f86"
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={prefix['eastmoney_sec_id']}&fields={fields}"
        raw = _request_text(url, referer="https://quote.eastmoney.com/")
        data = (json.loads(raw).get("data") or {}) if raw else {}
        if str(data.get("f57", "")).zfill(6) != code:
            return _base_quote(code, "东方财富", "返回代码不一致")
        ts = pd.to_datetime(data.get("f86"), unit="s", errors="coerce")
        quote_date = ts.date().isoformat() if not pd.isna(ts) else ""
        quote_time = ts.time().replace(microsecond=0).isoformat() if not pd.isna(ts) else ""

        def price(field: str) -> float | None:
            value = _safe_float(data.get(field))
            return None if value is None else value / 1000.0

        latest = price("f43")
        prev_close = price("f60")
        change = price("f169")
        payload = {
            "code": code,
            "name": str(data.get("f58") or ""),
            "latest_price": latest,
            "prev_close": prev_close,
            "open": price("f46"),
            "high": price("f44"),
            "low": price("f45"),
            "change": change,
            "pct_change": _safe_float(data.get("f170")),
            "volume": _safe_float(data.get("f47")),
            "amount": _safe_float(data.get("f48")),
            "quote_date": quote_date,
            "quote_time": quote_time,
            "source": "东方财富",
            "status": "",
            "error": "",
        }
        _write_quote_cache(code, "eastmoney", payload)
        return payload
    except Exception as exc:  # noqa: BLE001
        return _base_quote(code, "东方财富", str(exc))


def get_etf_quote_from_sina(etf_code: str) -> dict[str, Any]:
    code = str(etf_code).zfill(6)
    try:
        market_code = get_exchange_prefix(code)["sina_code"]
        url = f"https://hq.sinajs.cn/list={market_code}"
        raw = _request_text(url, encoding="gbk", referer="https://finance.sina.com.cn/")
        payload = raw.split("=", 1)[1].strip().strip(";").strip('"')
        parts = payload.split(",")
        if len(parts) < 32 or not parts[0]:
            return _base_quote(code, "新浪", "返回字段不足")
        latest = _safe_float(parts[3])
        prev_close = _safe_float(parts[2])
        change = latest - prev_close if latest is not None and prev_close is not None else None
        pct_change = change / prev_close * 100 if change is not None and prev_close else None
        result = {
            "code": code,
            "name": parts[0],
            "latest_price": latest,
            "prev_close": prev_close,
            "open": _safe_float(parts[1]),
            "high": _safe_float(parts[4]),
            "low": _safe_float(parts[5]),
            "change": change,
            "pct_change": pct_change,
            "volume": _safe_float(parts[8]) if len(parts) > 8 else None,
            "amount": _safe_float(parts[9]) if len(parts) > 9 else None,
            "quote_date": parts[30],
            "quote_time": parts[31],
            "source": "新浪",
            "status": "",
            "error": "",
        }
        _write_quote_cache(code, "sina", result)
        return result
    except Exception as exc:  # noqa: BLE001
        return _base_quote(code, "新浪", str(exc))


def get_etf_quote_from_tencent(etf_code: str) -> dict[str, Any]:
    code = str(etf_code).zfill(6)
    try:
        market_code = get_exchange_prefix(code)["tencent_code"]
        url = f"https://qt.gtimg.cn/q={market_code}"
        raw = _request_text(url, encoding="gbk", referer="https://gu.qq.com/")
        payload = raw.split("=", 1)[1].strip().strip(";").strip('"')
        parts = payload.split("~")
        if len(parts) < 40 or str(parts[2]).zfill(6) != code:
            return _base_quote(code, "腾讯", "返回字段不足或代码不一致")
        ts_text = parts[30]
        quote_date = f"{ts_text[0:4]}-{ts_text[4:6]}-{ts_text[6:8]}" if len(ts_text) >= 8 else ""
        quote_time = f"{ts_text[8:10]}:{ts_text[10:12]}:{ts_text[12:14]}" if len(ts_text) >= 14 else ""
        latest = _safe_float(parts[3])
        prev_close = _safe_float(parts[4])
        result = {
            "code": code,
            "name": parts[1],
            "latest_price": latest,
            "prev_close": prev_close,
            "open": _safe_float(parts[5]),
            "high": _safe_float(parts[33]),
            "low": _safe_float(parts[34]),
            "change": _safe_float(parts[31]),
            "pct_change": _safe_float(parts[32]),
            "volume": _safe_float(parts[36]) if len(parts) > 36 else None,
            "amount": _safe_float(parts[37]) if len(parts) > 37 else None,
            "quote_date": quote_date,
            "quote_time": quote_time,
            "source": "腾讯",
            "status": "",
            "error": "",
        }
        _write_quote_cache(code, "tencent", result)
        return result
    except Exception as exc:  # noqa: BLE001
        return _base_quote(code, "腾讯", str(exc))


def _parse_sina_payload(code: str, payload: str) -> dict[str, Any]:
    parts = payload.split(",")
    if len(parts) < 32 or not parts[0]:
        return _base_quote(code, "新浪", "返回字段不足")
    latest = _safe_float(parts[3])
    prev_close = _safe_float(parts[2])
    change = latest - prev_close if latest is not None and prev_close is not None else None
    pct_change = change / prev_close * 100 if change is not None and prev_close else None
    return {
        "code": code,
        "name": parts[0],
        "latest_price": latest,
        "prev_close": prev_close,
        "open": _safe_float(parts[1]),
        "high": _safe_float(parts[4]),
        "low": _safe_float(parts[5]),
        "change": change,
        "pct_change": pct_change,
        "volume": _safe_float(parts[8]) if len(parts) > 8 else None,
        "amount": _safe_float(parts[9]) if len(parts) > 9 else None,
        "quote_date": parts[30],
        "quote_time": parts[31],
        "source": "新浪",
        "status": "",
        "error": "",
    }


def _parse_tencent_payload(code: str, payload: str) -> dict[str, Any]:
    parts = payload.split("~")
    if len(parts) < 40 or str(parts[2]).zfill(6) != code:
        return _base_quote(code, "腾讯", "返回字段不足或代码不一致")
    ts_text = parts[30]
    quote_date = f"{ts_text[0:4]}-{ts_text[4:6]}-{ts_text[6:8]}" if len(ts_text) >= 8 else ""
    quote_time = f"{ts_text[8:10]}:{ts_text[10:12]}:{ts_text[12:14]}" if len(ts_text) >= 14 else ""
    latest = _safe_float(parts[3])
    prev_close = _safe_float(parts[4])
    return {
        "code": code,
        "name": parts[1],
        "latest_price": latest,
        "prev_close": prev_close,
        "open": _safe_float(parts[5]),
        "high": _safe_float(parts[33]),
        "low": _safe_float(parts[34]),
        "change": _safe_float(parts[31]),
        "pct_change": _safe_float(parts[32]),
        "volume": _safe_float(parts[36]) if len(parts) > 36 else None,
        "amount": _safe_float(parts[37]) if len(parts) > 37 else None,
        "quote_date": quote_date,
        "quote_time": quote_time,
        "source": "腾讯",
        "status": "",
        "error": "",
    }


def _batch_sina_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    result = {code: _base_quote(code, "新浪", "批量请求未返回") for code in codes}
    try:
        market_codes = ",".join(get_exchange_prefix(code)["sina_code"] for code in codes)
        raw = _request_text(f"https://hq.sinajs.cn/list={market_codes}", encoding="gbk", referer="https://finance.sina.com.cn/")
        for line in raw.splitlines():
            if "=" not in line:
                continue
            left, right = line.split("=", 1)
            market_code = left.rsplit("_", 1)[-1]
            code = market_code[-6:]
            if code in result:
                result[code] = _parse_sina_payload(code, right.strip().strip(";").strip('"'))
                _write_quote_cache(code, "sina", result[code])
    except Exception as exc:  # noqa: BLE001
        result = {code: _base_quote(code, "新浪", str(exc)) for code in codes}
    return result


def _batch_tencent_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    result = {code: _base_quote(code, "腾讯", "批量请求未返回") for code in codes}
    try:
        market_codes = ",".join(get_exchange_prefix(code)["tencent_code"] for code in codes)
        raw = _request_text(f"https://qt.gtimg.cn/q={market_codes}", encoding="gbk", referer="https://gu.qq.com/")
        for line in raw.splitlines():
            if "=" not in line:
                continue
            left, right = line.split("=", 1)
            market_code = left.rsplit("_", 1)[-1]
            code = market_code[-6:]
            if code in result:
                result[code] = _parse_tencent_payload(code, right.strip().strip(";").strip('"'))
                _write_quote_cache(code, "tencent", result[code])
    except Exception as exc:  # noqa: BLE001
        result = {code: _base_quote(code, "腾讯", str(exc)) for code in codes}
    return result


def get_latest_daily_close(etf_code: str, data_dir: str | Path = Path("data") / "cache") -> dict[str, Any]:
    code = str(etf_code).zfill(6)
    try:
        frame = load_etf_data(code, data_dir=Path(data_dir)).reset_index()
    except Exception as exc:  # noqa: BLE001
        return {"code": code, "source": "历史日线", "error": str(exc)}
    if frame.empty:
        return {"code": code, "source": "历史日线", "error": "本地日线为空"}
    frame = frame.sort_values("date")
    latest = frame.iloc[-1]
    previous = frame.iloc[-2] if len(frame) >= 2 else latest
    return {
        "code": code,
        "name": str(latest.get("name") or ""),
        "latest_price": _safe_float(latest.get("close")),
        "today_close": _safe_float(latest.get("close")),
        "prev_close": _safe_float(previous.get("close")),
        "open": _safe_float(latest.get("open")),
        "high": _safe_float(latest.get("high")),
        "low": _safe_float(latest.get("low")),
        "volume": _safe_float(latest.get("volume")),
        "amount": _safe_float(latest.get("amount")),
        "quote_date": pd.Timestamp(latest.get("date")).date().isoformat(),
        "quote_time": "15:00:00",
        "source": "历史日线",
        "daily_latest_close": _safe_float(latest.get("close")),
        "daily_latest_date": pd.Timestamp(latest.get("date")).date().isoformat(),
        "daily_prev_close": _safe_float(previous.get("close")),
        "error": "",
    }


def _is_trading_day(day: datetime | pd.Timestamp | None = None) -> bool:
    current = pd.Timestamp(day or _now())
    return current.weekday() < 5


def _is_trading_time(current: datetime | None = None) -> bool:
    now = current or _now()
    return _is_trading_day(now) and time(9, 30) <= now.time() <= time(15, 0)


def classify_price_status(quote_date: str, quote_time: str, market_calendar: Any = None) -> str:
    del market_calendar
    today = _now().date()
    try:
        parsed = pd.Timestamp(quote_date)
    except Exception:
        return "数据不可用"
    if pd.isna(parsed):
        return "数据不可用"
    qdate = parsed.date()
    if qdate == today:
        return "今日实时价" if _is_trading_time() else "今日收盘价"
    if qdate < today and _is_trading_day(_now()):
        return "昨日价格，需刷新"
    return "最近交易日收盘价"


def _daily_frame_from_info(daily: dict[str, Any]) -> pd.DataFrame:
    if not daily or daily.get("error"):
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "date": daily.get("quote_date"),
                "open": daily.get("open"),
                "high": daily.get("high"),
                "low": daily.get("low"),
                "close": daily.get("latest_price"),
            }
        ]
    )


def validate_quote_price(etf_code: str, quote: dict[str, Any], daily_df: pd.DataFrame | None = None) -> dict[str, Any]:
    code = str(etf_code).zfill(6)
    reasons: list[str] = []
    if str(quote.get("code", "")).zfill(6) != code:
        reasons.append("行情返回代码不一致")
    latest = _safe_float(quote.get("latest_price"))
    prev_close = _safe_float(quote.get("prev_close"))
    high = _safe_float(quote.get("high"))
    low = _safe_float(quote.get("low"))
    if latest is None or latest <= 0:
        reasons.append("最新价为空或小于等于 0")
    if latest is not None and high is not None and low is not None and high >= low and not (low <= latest <= high):
        reasons.append("最新价不在当日最高价和最低价之间")
    if latest is not None and prev_close and abs(latest / prev_close - 1) > 0.20:
        reasons.append("最新价相对昨收偏离超过 20%")

    quote_date = str(quote.get("quote_date") or "")
    price_status = classify_price_status(quote_date, str(quote.get("quote_time") or ""))
    if price_status == "昨日价格，需刷新":
        reasons.append("报价日期不是今天")

    if daily_df is not None and not daily_df.empty and latest is not None:
        frame = daily_df.copy()
        if "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            same_day = frame[frame["date"].dt.date == pd.Timestamp(quote_date).date()] if quote_date else pd.DataFrame()
            if not same_day.empty:
                daily_close = _safe_float(same_day.iloc[-1].get("close"))
                if daily_close and abs(latest / daily_close - 1) > 0.01:
                    reasons.append("实时价与今日日线收盘价差异超过 1%")

    valid = not reasons or reasons == ["报价日期不是今天"]
    if any(reason != "报价日期不是今天" for reason in reasons):
        price_status = "价格异常，已停用"
        valid = False
    elif "报价日期不是今天" in reasons:
        valid = False

    frontend = {
        "今日实时价": f"当前价格来自今日行情，报价时间 {quote.get('quote_time') or '未知'}，可用于模拟盘计算。",
        "今日收盘价": f"当前价格来自今日行情，报价时间 {quote.get('quote_time') or '未知'}，可用于模拟盘计算。",
        "最近交易日收盘价": "当前价格来自最近完整交易日收盘价，可用于非交易日模拟盘参考。",
        "昨日价格，需刷新": "当前价格不是今日行情，仅作参考。今日买入/卖出计划暂停生成。",
        "价格异常，已停用": "行情源返回价格异常，系统已停用该价格，等待人工确认。",
        "数据不可用": "行情不可用，等待刷新或人工确认。",
    }.get(price_status, "行情状态待确认。")
    return {
        "valid": bool(valid),
        "price_status": price_status,
        "frontend_message": frontend,
        "debug_message": "；".join(reasons) if reasons else "校验通过",
        "reasons": reasons,
    }


def _source_price(quote: dict[str, Any]) -> float | None:
    price = _safe_float(quote.get("latest_price"))
    return price if price and price > 0 else None


def _select_consensus_quote(quotes: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    usable = [item for item in quotes if _source_price(item) is not None and item.get("quote_date")]
    if not usable:
        return None, []
    prices = [_source_price(item) for item in usable]
    prices = [price for price in prices if price is not None]
    median = float(pd.Series(prices).median())
    conflicts = [f"{item['source']}={_source_price(item)}" for item in usable if _source_price(item) and abs(_source_price(item) / median - 1) > 0.01]
    if conflicts:
        return None, [f"多行情源价格冲突超过 1%：{'; '.join(conflicts)}"]
    priority = {"东方财富": 0, "新浪": 1, "腾讯": 2}
    usable = sorted(usable, key=lambda item: priority.get(str(item.get("source")), 99))
    return usable[0], []


def _validate_daily_history_alignment(result: dict[str, Any], daily: dict[str, Any]) -> tuple[bool, str]:
    quote_date = result.get("quote_date")
    daily_date = daily.get("daily_latest_date") or daily.get("quote_date")
    if not quote_date or not daily_date:
        return True, ""
    try:
        qdate = pd.Timestamp(quote_date).date()
        ddate = pd.Timestamp(daily_date).date()
    except Exception:
        return True, ""
    daily_close = _safe_float(daily.get("daily_latest_close") or daily.get("latest_price"))
    prev_close = _safe_float(result.get("prev_close"))
    if ddate < qdate and daily_close and prev_close and abs(daily_close / prev_close - 1) > 0.01:
        return False, f"本地日线最新收盘价 {daily_close} 与实时昨收 {prev_close} 差异超过 1%，日线可能未复权或未更新"
    return True, ""


def get_etf_quote(etf_code: str, data_dir: str | Path = Path("data") / "cache") -> dict[str, Any]:
    code = str(etf_code).zfill(6)
    cache_key = f"{code}:{Path(data_dir)}"
    if cache_key in _QUOTE_MEMORY:
        return _QUOTE_MEMORY[cache_key]
    daily = get_latest_daily_close(code, data_dir=data_dir)
    source_quotes = [
        _base_quote(code, "东方财富", "东方财富接口本次未调用，使用新浪和腾讯交叉校验"),
        get_etf_quote_from_sina(code),
        get_etf_quote_from_tencent(code),
    ]
    selected, conflicts = _select_consensus_quote(source_quotes)
    if selected is None:
        if conflicts:
            result = _base_quote(code, "多行情源", "；".join(conflicts))
            result.update(
                {
                    "status": "价格异常，已停用",
                    "price_status": "价格异常，已停用",
                    "valid": False,
                    "frontend_message": "行情源返回价格异常，系统已停用该价格，等待人工确认。",
                    "debug_message": "；".join(conflicts),
                }
            )
        else:
            result = dict(daily)
            result.setdefault("latest_price", daily.get("latest_price"))
            result["source"] = "历史日线"
            validation = validate_quote_price(code, result, _daily_frame_from_info(daily))
            result.update(validation)
    else:
        result = dict(selected)
        validation = validate_quote_price(code, result, _daily_frame_from_info(daily))
        result.update(validation)

    price_status = str(result.get("price_status") or result.get("status") or "数据不可用")
    if price_status == "价格异常，已停用":
        result["latest_price"] = None
    result["status"] = price_status
    result["price_status"] = price_status
    result["price_actionable"] = price_status in ACTIVE_PRICE_STATUSES and bool(result.get("valid", False))
    result["sources"] = {item["source"]: item for item in source_quotes}
    result["daily"] = daily
    history_valid, history_message = _validate_daily_history_alignment(result, daily)
    result["daily_history_valid"] = history_valid
    result["daily_history_message"] = history_message
    result["debug"] = build_quote_debug_info(code, result)
    _QUOTE_MEMORY[cache_key] = result
    return result


def get_etf_quotes(etf_codes: list[str] | set[str], data_dir: str | Path = Path("data") / "cache") -> dict[str, dict[str, Any]]:
    codes = sorted({str(code).zfill(6) for code in etf_codes if str(code).strip()})
    output: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for code in codes:
        cache_key = f"{code}:{Path(data_dir)}"
        if cache_key in _QUOTE_MEMORY:
            output[code] = _QUOTE_MEMORY[cache_key]
        else:
            missing.append(code)
    if not missing:
        return output

    sina_quotes = _batch_sina_quotes(missing)
    tencent_quotes = _batch_tencent_quotes(missing)
    for code in missing:
        daily = get_latest_daily_close(code, data_dir=data_dir)
        source_quotes = [
            _base_quote(code, "东方财富", "东方财富接口本次未调用，使用新浪和腾讯交叉校验"),
            sina_quotes.get(code, _base_quote(code, "新浪", "批量请求未返回")),
            tencent_quotes.get(code, _base_quote(code, "腾讯", "批量请求未返回")),
        ]
        selected, conflicts = _select_consensus_quote(source_quotes)
        if selected is None:
            if conflicts:
                result = _base_quote(code, "多行情源", "；".join(conflicts))
                result.update(
                    {
                        "status": "价格异常，已停用",
                        "price_status": "价格异常，已停用",
                        "valid": False,
                        "frontend_message": "行情源返回价格异常，系统已停用该价格，等待人工确认。",
                        "debug_message": "；".join(conflicts),
                    }
                )
            else:
                result = dict(daily)
                result.setdefault("latest_price", daily.get("latest_price"))
                result["source"] = "历史日线"
                result.update(validate_quote_price(code, result, _daily_frame_from_info(daily)))
        else:
            result = dict(selected)
            result.update(validate_quote_price(code, result, _daily_frame_from_info(daily)))
        price_status = str(result.get("price_status") or result.get("status") or "数据不可用")
        if price_status == "价格异常，已停用":
            result["latest_price"] = None
        result["status"] = price_status
        result["price_status"] = price_status
        result["price_actionable"] = price_status in ACTIVE_PRICE_STATUSES and bool(result.get("valid", False))
        result["sources"] = {item["source"]: item for item in source_quotes}
        result["daily"] = daily
        history_valid, history_message = _validate_daily_history_alignment(result, daily)
        result["daily_history_valid"] = history_valid
        result["daily_history_message"] = history_message
        result["debug"] = build_quote_debug_info(code, result)
        cache_key = f"{code}:{Path(data_dir)}"
        _QUOTE_MEMORY[cache_key] = result
        output[code] = result
    return output


def get_current_price_for_portfolio(etf_code: str, data_dir: str | Path = Path("data") / "cache") -> dict[str, Any]:
    return get_etf_quote(etf_code, data_dir=data_dir)


def build_quote_debug_info(etf_code: str, quote: dict[str, Any] | None = None) -> dict[str, Any]:
    code = str(etf_code).zfill(6)
    data = quote or get_etf_quote(code)
    sources = data.get("sources") or {}
    daily = data.get("daily") or {}
    return {
        "ETF代码": code,
        "ETF名称": data.get("name") or daily.get("name") or "",
        "系统使用价格": data.get("latest_price"),
        "系统价格状态": data.get("price_status") or data.get("status"),
        "系统价格来源": data.get("source"),
        "报价日期": data.get("quote_date"),
        "报价时间": data.get("quote_time"),
        "东方财富价格": (sources.get("东方财富") or {}).get("latest_price"),
        "新浪价格": (sources.get("新浪") or {}).get("latest_price"),
        "腾讯价格": (sources.get("腾讯") or {}).get("latest_price"),
        "日线最新收盘价": daily.get("daily_latest_close") or daily.get("latest_price"),
        "昨日收盘价": data.get("prev_close") or daily.get("daily_prev_close"),
        "校验结果": "通过" if data.get("price_actionable") else "未通过",
        "异常原因": "；".join(item for item in [data.get("debug_message") or data.get("error") or "", data.get("daily_history_message") or ""] if item),
    }
