from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Any

import pandas as pd
import yaml


RAW_UNIVERSE_PATH = Path("output") / "etf_universe_raw.csv"
SNAPSHOT_PATH = Path("output") / "etf_universe_snapshot.csv"
CONFIG_PATH = Path("config") / "etf_universe.yaml"
UNIVERSE_CACHE_DIR = Path("data") / "universe"
UNIVERSE_CACHE_PATH = UNIVERSE_CACHE_DIR / "etf_universe.csv"
UNIVERSE_META_PATH = UNIVERSE_CACHE_DIR / "etf_universe_meta.json"
UNIVERSE_REFRESH_DAYS = 7

ASSET_CLASSES = ("A股股票", "债券", "商品", "跨境", "货币", "其他")


def normalize_symbol(symbol: object) -> str:
    text = str(symbol or "").strip().upper()
    if not text:
        return ""
    if text.startswith(("SH", "SZ")):
        text = text[2:]
    if "." in text:
        text = text.split(".", 1)[0]
    match = re.search(r"\d{6}", text)
    if match:
        return match.group(0)
    digits = re.sub(r"\D", "", text)
    return digits.zfill(6)[-6:] if digits else ""


def _read_filters(config_path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("filters", {}) or {}


def _exchange(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    if symbol.startswith(("5", "6")):
        return "SH"
    if symbol.startswith(("0", "1", "2", "3")):
        return "SZ"
    return ""


def classify_asset(name: str) -> str:
    text = str(name)
    if any(key in text for key in ["货币", "添益", "日利", "保证金", "现金", "快线", "快钱", "理财"]):
        return "货币"
    if any(key in text for key in ["债", "国开", "政金", "转债", "城投"]):
        return "债券"
    if any(key in text for key in ["黄金", "豆粕", "有色金属期货", "商品", "原油", "油气", "能源化工", "饲料"]):
        return "商品"
    if any(
        key in text
        for key in [
            "纳指",
            "纳斯达克",
            "标普",
            "恒生",
            "港股",
            "香港",
            "日经",
            "德国",
            "法国",
            "中概",
            "亚太",
            "东南亚",
            "沙特",
            "QDII",
            "海外",
            "中韩",
        ]
    ):
        return "跨境"
    if "ETF" in text:
        return "A股股票"
    return "其他"


def infer_category(name: str, asset_class: str) -> str:
    text = str(name)
    if asset_class != "A股股票":
        return asset_class
    broad = ["沪深300", "中证500", "中证1000", "科创", "创业板", "上证50", "A500", "中证A", "上证综指", "深证"]
    style = ["红利", "价值", "成长", "低波", "央企", "国企", "ESG"]
    if any(key in text for key in broad):
        return "宽基"
    if any(key in text for key in style):
        return "风格"
    return "行业主题"


def infer_tracking_index(name: str) -> str:
    text = str(name)
    for suffix in ["ETF联接", "ETF"]:
        text = text.replace(suffix, "")
    for brand in ["华夏", "易方达", "华泰柏瑞", "南方", "国泰", "华宝", "广发", "富国", "博时", "嘉实", "天弘", "银华", "汇添富"]:
        text = text.replace(brand, "")
    return text.strip() or str(name)


def _pick(frame: pd.DataFrame, name: str, pos: int) -> str:
    if name in frame.columns:
        return name
    return str(frame.columns[pos])


def fetch_market_etf_universe(output_path: str | Path = RAW_UNIVERSE_PATH) -> pd.DataFrame:
    try:
        import akshare as ak
    except ImportError as exc:
        raise ImportError("AKShare is not installed; run pip install -r requirements.txt") from exc

    raw = ak.fund_etf_spot_em()
    code_col = _pick(raw, "代码", 0)
    name_col = _pick(raw, "名称", 1)
    amount_col = _pick(raw, "成交额", 8)
    data_date_col = "数据日期" if "数据日期" in raw.columns else None
    update_col = "更新时间" if "更新时间" in raw.columns else None

    frame = pd.DataFrame(
        {
            "symbol": raw[code_col].map(normalize_symbol),
            "name": raw[name_col].astype(str),
            "exchange": raw[code_col].map(normalize_symbol).map(_exchange),
            "spot_amount": pd.to_numeric(raw[amount_col], errors="coerce"),
            "spot_date": pd.to_datetime(raw[data_date_col], errors="coerce").dt.date.astype(str) if data_date_col else "",
            "spot_updated_at": raw[update_col].astype(str) if update_col else "",
        }
    )
    frame["asset_class"] = frame["name"].map(classify_asset)
    frame["category"] = frame.apply(lambda row: infer_category(row["name"], row["asset_class"]), axis=1)
    frame["tracking_index"] = frame["name"].map(infer_tracking_index)
    frame["listing_date"] = ""
    frame["latest_date"] = ""
    frame["avg_amount_20"] = pd.NA
    frame["data_rows"] = pd.NA
    frame["is_active"] = frame["spot_amount"].fillna(0) > 0
    frame["filter_reason"] = ""
    frame["universe_source"] = "akshare.fund_etf_spot_em"
    frame["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    frame = frame.drop_duplicates("symbol", keep="first").sort_values(["asset_class", "symbol"]).reset_index(drop=True)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    _write_universe_cache(frame)
    return frame


def _write_universe_cache(frame: pd.DataFrame) -> None:
    import json

    UNIVERSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(UNIVERSE_CACHE_PATH, index=False, encoding="utf-8-sig")
    meta = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "akshare.fund_etf_spot_em",
        "count": int(len(frame)),
    }
    UNIVERSE_META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_is_stale(max_age_days: int = UNIVERSE_REFRESH_DAYS) -> bool:
    if not UNIVERSE_CACHE_PATH.exists() or not UNIVERSE_META_PATH.exists():
        return True
    try:
        import json

        meta = json.loads(UNIVERSE_META_PATH.read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(str(meta.get("updated_at", "")))
    except Exception:
        return True
    return datetime.now() - updated_at > timedelta(days=max_age_days)


def load_market_etf_universe(refresh: bool = False, max_age_days: int = UNIVERSE_REFRESH_DAYS) -> pd.DataFrame:
    if refresh:
        try:
            return fetch_market_etf_universe()
        except Exception as exc:  # noqa: BLE001
            if UNIVERSE_CACHE_PATH.exists():
                print(f"Warning: failed to refresh ETF universe, using cached universe: {exc}")
                return pd.read_csv(UNIVERSE_CACHE_PATH, dtype={"symbol": str})
            if RAW_UNIVERSE_PATH.exists():
                print(f"Warning: failed to refresh ETF universe, using legacy cached raw universe: {exc}")
                frame = pd.read_csv(RAW_UNIVERSE_PATH, dtype={"symbol": str})
                _write_universe_cache(frame)
                return frame
            raise
    if UNIVERSE_CACHE_PATH.exists() and not _cache_is_stale(max_age_days):
        return pd.read_csv(UNIVERSE_CACHE_PATH, dtype={"symbol": str})
    if UNIVERSE_CACHE_PATH.exists():
        return pd.read_csv(UNIVERSE_CACHE_PATH, dtype={"symbol": str})
    if RAW_UNIVERSE_PATH.exists():
        frame = pd.read_csv(RAW_UNIVERSE_PATH, dtype={"symbol": str})
        _write_universe_cache(frame)
        return frame
    return fetch_market_etf_universe()


def core_11_symbols(config_path: str | Path = CONFIG_PATH) -> set[str]:
    path = Path(config_path)
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    symbols = ((raw.get("presets") or {}).get("core_11") or {}).get("symbols") or []
    return {normalize_symbol(symbol) for symbol in symbols}


def universe_records(
    preset: str = "a_share_equity",
    refresh: bool = False,
    config_path: str | Path = CONFIG_PATH,
) -> list[dict[str, str]]:
    frame = load_market_etf_universe(refresh=refresh)
    if preset in {"full_market", "full_universe"}:
        selected = frame.copy()
    elif preset == "core_11":
        selected = frame[frame["symbol"].isin(core_11_symbols(config_path))].copy()
    else:
        selected = frame[frame["asset_class"].eq("A股股票")].copy()
    selected = selected.sort_values(["spot_amount", "symbol"], ascending=[False, True])
    return [
        {
            "symbol": normalize_symbol(row["symbol"]),
            "name": str(row["name"]),
            "exchange": str(row.get("exchange", "")),
            "asset_class": str(row.get("asset_class", "")),
            "category": str(row.get("category", "")),
            "theme": str(row.get("category", "")),
            "sector": str(row.get("tracking_index", "")),
            "tracking_index": str(row.get("tracking_index", "")),
            "listing_date": str(row.get("listing_date", "")),
            "latest_date": str(row.get("latest_date", "")),
            "avg_amount_20": str(row.get("avg_amount_20", "")),
            "data_rows": str(row.get("data_rows", "")),
            "is_active": str(row.get("is_active", "")),
            "filter_reason": str(row.get("filter_reason", "")),
        }
        for _, row in selected.iterrows()
    ]


def write_universe_snapshot(raw: pd.DataFrame, coverage: pd.DataFrame | None = None, path: str | Path = SNAPSHOT_PATH) -> pd.DataFrame:
    snapshot = raw.copy()
    if coverage is not None and not coverage.empty:
        cols = [
            "symbol",
            "start_date",
            "latest_date",
            "avg_amount_20",
            "data_rows",
            "is_active",
            "filter_reason",
            "success",
            "failure_reason",
        ]
        existing = [col for col in cols if col in coverage.columns]
        merged = coverage[existing].copy()
        if "start_date" in merged.columns:
            merged = merged.rename(columns={"start_date": "listing_date"})
        snapshot = snapshot.drop(columns=[col for col in merged.columns if col in snapshot.columns and col != "symbol"], errors="ignore")
        snapshot = snapshot.merge(merged, on="symbol", how="left")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_csv(out, index=False, encoding="utf-8-sig")
    return snapshot


def build_universe_stage_counts(
    raw: pd.DataFrame,
    coverage: pd.DataFrame,
    rankings: pd.DataFrame,
    min_trading_days: int | None = None,
    min_avg_amount: float | None = None,
    min_data_completeness: float | None = None,
) -> dict[str, int]:
    filters = _read_filters()
    min_trading_days = int(min_trading_days if min_trading_days is not None else filters.get("min_trading_days", 120))
    min_avg_amount = float(min_avg_amount if min_avg_amount is not None else filters.get("min_avg_amount", 20_000_000))
    min_data_completeness = float(min_data_completeness if min_data_completeness is not None else filters.get("min_data_completeness", 0.95))

    raw_total = int(len(raw))
    a_share = raw[raw["asset_class"].eq("A股股票")] if "asset_class" in raw.columns else pd.DataFrame()
    cov = coverage.copy()
    if not cov.empty and "asset_class" in cov.columns:
        cov = cov[cov["asset_class"].eq("A股股票")]
    if not cov.empty:
        rows = pd.to_numeric(cov["data_rows"] if "data_rows" in cov.columns else cov.get("rows", 0), errors="coerce").fillna(0)
        listed = cov[rows >= min_trading_days]
    else:
        listed = pd.DataFrame()
    if not listed.empty:
        avg_amount = pd.to_numeric(listed["avg_amount_20"] if "avg_amount_20" in listed.columns else 0, errors="coerce").fillna(0)
        amount = listed[avg_amount >= min_avg_amount]
    else:
        amount = pd.DataFrame()
    if not amount.empty:
        if "data_completeness" in amount.columns:
            completeness = pd.to_numeric(amount["data_completeness"], errors="coerce").fillna(0)
        else:
            completeness = pd.Series(1.0, index=amount.index)
        complete = amount[completeness >= min_data_completeness]
    else:
        complete = pd.DataFrame()
    return {
        "raw_total": raw_total,
        "a_share_equity_total": int(len(a_share)),
        "listed_pass_count": int(len(listed)),
        "amount_pass_count": int(len(amount)),
        "completeness_pass_count": int(len(complete)),
        "ranked_count": int(len(rankings)),
    }
