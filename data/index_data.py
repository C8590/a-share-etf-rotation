from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import yaml

from data.trading_calendar import latest_trading_day_on_or_before


INDEX_MAP_COLUMNS = [
    "symbol",
    "etf_name",
    "category",
    "sub_category",
    "tracking_index_name",
    "tracking_index_code",
    "index_source",
    "mapping_method",
    "confidence",
    "requires_manual_review",
    "usable_as_benchmark",
    "notes",
]

INDEX_DATA_COVERAGE_COLUMNS = [
    "tracking_index_code",
    "tracking_index_name",
    "index_source",
    "api_name",
    "source_family",
    "fetch_success",
    "schema_valid",
    "start_date",
    "end_date",
    "row_count",
    "latest_expected_date",
    "end_date_gap_days",
    "missing_required_columns",
    "missing_values_count",
    "duplicate_dates_count",
    "abnormal_return_count",
    "quality_status",
    "usable_as_benchmark",
    "requires_manual_review",
    "failure_reason",
    "notes",
]

INDEX_CACHE_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "index_code",
    "index_name",
    "source",
]

MISSING_MARKERS = {"", "unknown", "missing", "unable_to_confirm", "nan", "none", "nat", "<na>"}
MAPPING_METHODS = {"metadata_exact", "config_manual", "name_inferred", "unable_to_confirm"}
DEFAULT_INDEX_MAP_CONFIG = Path("config") / "index_map.yaml"
DEFAULT_METADATA_PATH = Path("output") / "etf_metadata.csv"
DEFAULT_INDEX_MAP_OUTPUT = Path("output") / "index_map.csv"
DEFAULT_INDEX_COVERAGE_OUTPUT = Path("output") / "index_data_coverage.csv"
DEFAULT_INDEX_CACHE_DIR = Path("data") / "index_cache"
ABNORMAL_RETURN_THRESHOLD = 0.20
DEFAULT_INDEX_UPDATE_CODES = {
    "000015",
    "000300",
    "000688",
    "000852",
    "000905",
    "000932",
    "399006",
    "399975",
    "931865",
}


Fetcher = Callable[[str, str, str | None], pd.DataFrame]
IndexFetcher = Callable[[str, str, str | None, Any], pd.DataFrame]


@dataclass(frozen=True)
class IndexApiCandidate:
    api_name: str
    source_family: str
    fetcher: IndexFetcher
    notes: str = ""


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if pd.isna(value):
        return False
    return str(value).strip().lower() not in MISSING_MARKERS


def _text(value: Any, default: str = "") -> str:
    if not _is_present(value):
        return default
    return str(value).strip()


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _latest_expected_date() -> str:
    try:
        return str(latest_trading_day_on_or_before(pd.Timestamp.today().normalize()).date())
    except Exception:
        return str(pd.Timestamp.today().normalize().date())


def _end_date_gap_days(end_date: str, latest_expected_date: str) -> int:
    end = pd.to_datetime(end_date, errors="coerce")
    expected = pd.to_datetime(latest_expected_date, errors="coerce")
    if pd.isna(end) or pd.isna(expected):
        return 0
    return max(0, int((expected.normalize() - end.normalize()).days))


def _load_yaml(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _manual_config_by_symbol(config_path: str | Path = DEFAULT_INDEX_MAP_CONFIG) -> dict[str, dict[str, Any]]:
    raw = _load_yaml(config_path)
    rows = raw.get("mappings", raw if isinstance(raw, list) else []) or []
    result: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).zfill(6)
        if symbol.strip("0"):
            result[symbol] = item
    return result


def _usable(mapping_method: str, confidence: float, requires_manual_review: bool, tracking_index_code: str) -> bool:
    if mapping_method not in {"metadata_exact", "config_manual"}:
        return False
    if requires_manual_review:
        return False
    if not _is_present(tracking_index_code):
        return False
    return float(confidence) >= 0.80


def _normalize_mapping_row(row: dict[str, Any]) -> dict[str, Any]:
    method = _text(row.get("mapping_method"), "unable_to_confirm")
    if method not in MAPPING_METHODS:
        method = "unable_to_confirm"
    confidence = pd.to_numeric(row.get("confidence", 0.0), errors="coerce")
    confidence_float = 0.0 if pd.isna(confidence) else max(0.0, min(1.0, float(confidence)))
    requires_manual_review = _bool_value(row.get("requires_manual_review", False))
    code = _text(row.get("tracking_index_code"), "unable_to_confirm")
    if method == "unable_to_confirm":
        code = _text(row.get("tracking_index_code"), "unable_to_confirm")
        requires_manual_review = True
        confidence_float = min(confidence_float, 0.0)
    if method == "name_inferred":
        requires_manual_review = True
        confidence_float = min(confidence_float if confidence_float else 0.50, 0.79)
    normalized = {
        "symbol": str(row.get("symbol", "")).zfill(6),
        "etf_name": _text(row.get("etf_name"), _text(row.get("name"))),
        "category": _text(row.get("category"), "unknown"),
        "sub_category": _text(row.get("sub_category"), "unknown"),
        "tracking_index_name": _text(row.get("tracking_index_name"), "unable_to_confirm"),
        "tracking_index_code": code,
        "index_source": _text(row.get("index_source"), "unable_to_confirm"),
        "mapping_method": method,
        "confidence": round(confidence_float, 4),
        "requires_manual_review": bool(requires_manual_review),
        "usable_as_benchmark": _usable(method, confidence_float, requires_manual_review, code),
        "notes": _text(row.get("notes")),
    }
    return {column: normalized.get(column, "") for column in INDEX_MAP_COLUMNS}


def infer_index_candidates(etf_name: str, category: str = "", sub_category: str = "") -> list[dict[str, Any]]:
    text = str(etf_name or "")
    rules = [
        ("沪深300", "000300", ["沪深300"]),
        ("中证500", "000905", ["中证500"]),
        ("中证1000", "000852", ["中证1000"]),
        ("创业板指", "399006", ["创业板ETF", "创业板指"]),
        ("科创50", "000688", ["科创50"]),
        ("上证50", "000016", ["上证50"]),
        ("上证红利", "000015", ["红利ETF", "上证红利"]),
        ("中证主要消费", "000932", ["主要消费"]),
        ("中证全指证券公司", "399975", ["证券ETF", "证券公司"]),
        ("中证全指半导体产品与设备", "931865", ["半导体ETF", "半导体"]),
    ]
    candidates: list[dict[str, Any]] = []
    for name, code, keywords in rules:
        if any(keyword in text for keyword in keywords):
            candidates.append(
                {
                    "tracking_index_name": name,
                    "tracking_index_code": code,
                    "index_source": "name_inference",
                    "mapping_method": "name_inferred",
                    "confidence": 0.60,
                    "requires_manual_review": True,
                    "notes": "candidate inferred from ETF name; not a confirmed benchmark",
                }
            )
    if not candidates:
        candidates.append(
            {
                "tracking_index_name": "unable_to_confirm",
                "tracking_index_code": "unable_to_confirm",
                "index_source": "unconfirmed",
                "mapping_method": "unable_to_confirm",
                "confidence": 0.0,
                "requires_manual_review": True,
                "notes": "no reliable metadata, manual config, or clear name candidate",
            }
        )
    return candidates


def build_index_map(
    metadata: pd.DataFrame | None = None,
    *,
    metadata_path: str | Path = DEFAULT_METADATA_PATH,
    config_path: str | Path = DEFAULT_INDEX_MAP_CONFIG,
    symbols: str | list[str] | None = None,
    max_count: int | None = None,
) -> pd.DataFrame:
    if metadata is None:
        meta_path = Path(metadata_path)
        if not meta_path.exists():
            metadata = pd.DataFrame(columns=["symbol", "name", "category", "sub_category", "tracking_index_name", "tracking_index_code", "metadata_source"])
        else:
            metadata = pd.read_csv(meta_path, dtype={"symbol": str, "tracking_index_code": str}, encoding="utf-8-sig").fillna("")
    frame = metadata.copy().fillna("")
    if symbols:
        requested = [item.strip().zfill(6) for item in symbols.split(",")] if isinstance(symbols, str) else [str(item).zfill(6) for item in symbols]
        requested = [item for item in requested if item]
        frame = frame[frame["symbol"].astype(str).str.zfill(6).isin(requested)].copy()
    manual = _manual_config_by_symbol(config_path)
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        symbol = str(record.get("symbol", "")).zfill(6)
        base = {
            "symbol": symbol,
            "etf_name": _text(record.get("name")),
            "category": _text(record.get("category"), "unknown"),
            "sub_category": _text(record.get("sub_category"), "unknown"),
        }
        metadata_name = _text(record.get("tracking_index_name"))
        metadata_code = _text(record.get("tracking_index_code"))
        if _is_present(metadata_name) and _is_present(metadata_code):
            row = {
                **base,
                "tracking_index_name": metadata_name,
                "tracking_index_code": metadata_code,
                "index_source": _text(record.get("metadata_source"), "metadata"),
                "mapping_method": "metadata_exact",
                "confidence": 1.0,
                "requires_manual_review": False,
                "notes": "tracking index confirmed by ETF metadata source",
            }
        elif symbol in manual:
            item = manual[symbol]
            row = {
                **base,
                "tracking_index_name": _text(item.get("tracking_index_name"), "unable_to_confirm"),
                "tracking_index_code": _text(item.get("tracking_index_code"), "unable_to_confirm"),
                "index_source": _text(item.get("index_source"), "config/index_map.yaml"),
                "mapping_method": _text(item.get("mapping_method"), "config_manual"),
                "confidence": item.get("confidence", 0.0),
                "requires_manual_review": item.get("requires_manual_review", False),
                "notes": _text(item.get("notes")),
            }
        else:
            inferred = infer_index_candidates(base["etf_name"], base["category"], base["sub_category"])[0]
            row = {**base, **inferred}
        rows.append(_normalize_mapping_row(row))
    result = pd.DataFrame(rows, columns=INDEX_MAP_COLUMNS).sort_values(["usable_as_benchmark", "symbol"], ascending=[False, True]).reset_index(drop=True)
    if max_count is not None and int(max_count) > 0:
        result = result.head(int(max_count)).copy()
    return result.reset_index(drop=True)


def _pick_column(frame: pd.DataFrame, aliases: list[str]) -> str | None:
    columns = {str(col).strip(): col for col in frame.columns}
    for alias in aliases:
        if alias in columns:
            return columns[alias]
    lower = {str(col).strip().lower(): col for col in frame.columns}
    for alias in aliases:
        found = lower.get(alias.lower())
        if found is not None:
            return found
    return None


def _index_symbol_for_em(index_code: str) -> str:
    code = str(index_code).strip()
    if code.startswith("399"):
        return "sz" + code
    if code.startswith("9"):
        return "csi" + code
    return "sh" + code


def _fetch_stock_zh_index_hist_csindex(index_code: str, start_date: str, end_date: str | None, ak_module: Any) -> pd.DataFrame:
    return ak_module.stock_zh_index_hist_csindex(
        symbol=str(index_code).strip().zfill(6),
        start_date=start_date,
        end_date=end_date or datetime.now().strftime("%Y%m%d"),
    )


def _fetch_index_zh_a_hist(index_code: str, start_date: str, end_date: str | None, ak_module: Any) -> pd.DataFrame:
    return ak_module.index_zh_a_hist(
        symbol=str(index_code).strip().zfill(6),
        period="daily",
        start_date=start_date,
        end_date=end_date or datetime.now().strftime("%Y%m%d"),
    )


def _fetch_stock_zh_index_daily_em(index_code: str, start_date: str, end_date: str | None, ak_module: Any) -> pd.DataFrame:
    return ak_module.stock_zh_index_daily_em(
        symbol=_index_symbol_for_em(index_code),
        start_date=start_date,
        end_date=end_date or datetime.now().strftime("%Y%m%d"),
    )


INDEX_API_CANDIDATES = [
    IndexApiCandidate("akshare.stock_zh_index_hist_csindex", "csindex", _fetch_stock_zh_index_hist_csindex, "preferred formal path after ETF-GAP-006A diagnostics"),
    IndexApiCandidate("akshare.index_zh_a_hist", "eastmoney", _fetch_index_zh_a_hist, "EastMoney candidate retained as fallback"),
    IndexApiCandidate("akshare.stock_zh_index_daily_em", "eastmoney", _fetch_stock_zh_index_daily_em, "EastMoney daily kline fallback"),
]


def fetch_index_history(
    tracking_index_code: str,
    *,
    start_date: str = "20190101",
    end_date: str | None = None,
    ak_module: Any | None = None,
) -> tuple[pd.DataFrame, str]:
    code = str(tracking_index_code).strip()
    if not _is_present(code):
        raise ValueError("tracking_index_code is unable_to_confirm")
    if ak_module is None:
        import akshare as ak_module

    errors: list[str] = []
    for candidate in INDEX_API_CANDIDATES:
        try:
            raw = candidate.fetcher(code, start_date, end_date, ak_module)
            return raw, candidate.api_name
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{candidate.api_name} failed: {exc}")
    raise RuntimeError(" | ".join(errors))


def normalize_index_history(
    raw: pd.DataFrame,
    *,
    index_code: str,
    index_name: str = "",
    source: str = "",
) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise ValueError(f"{index_code} index data is empty")
    date_col = _pick_column(raw, ["date", "日期", "trade_date"])
    open_col = _pick_column(raw, ["open", "开盘", "开盘价"])
    high_col = _pick_column(raw, ["high", "最高", "最高价"])
    low_col = _pick_column(raw, ["low", "最低", "最低价"])
    close_col = _pick_column(raw, ["close", "收盘", "收盘价"])
    volume_col = _pick_column(raw, ["volume", "成交量", "vol"])
    amount_col = _pick_column(raw, ["amount", "成交额", "成交金额"])
    missing = [
        name
        for name, col in {
            "date": date_col,
            "open": open_col,
            "high": high_col,
            "low": low_col,
            "close": close_col,
            "volume": volume_col,
            "amount": amount_col,
        }.items()
        if col is None
    ]
    if missing:
        raise ValueError(f"{index_code} index data missing required columns: {', '.join(missing)}")
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[date_col], errors="coerce"),
            "open": pd.to_numeric(raw[open_col], errors="coerce"),
            "high": pd.to_numeric(raw[high_col], errors="coerce"),
            "low": pd.to_numeric(raw[low_col], errors="coerce"),
            "close": pd.to_numeric(raw[close_col], errors="coerce"),
            "volume": pd.to_numeric(raw[volume_col], errors="coerce"),
            "amount": pd.to_numeric(raw[amount_col], errors="coerce"),
            "index_code": str(index_code).strip(),
            "index_name": index_name,
            "source": source,
        }
    )
    return out.sort_values("date").reset_index(drop=True)[INDEX_CACHE_COLUMNS]


def validate_index_history(frame: pd.DataFrame, *, latest_expected_date: str | None = None) -> dict[str, Any]:
    expected = latest_expected_date or _latest_expected_date()
    missing_columns = [column for column in INDEX_CACHE_COLUMNS if column not in frame.columns]
    if missing_columns:
        return {
            "fetch_success": False,
            "schema_valid": False,
            "start_date": "",
            "end_date": "",
            "row_count": 0,
            "latest_expected_date": expected,
            "end_date_gap_days": 0,
            "missing_required_columns": ";".join(missing_columns),
            "missing_values_count": 0,
            "duplicate_dates_count": 0,
            "abnormal_return_count": 0,
            "quality_status": "failed",
            "failure_reason": f"missing required columns: {', '.join(missing_columns)}",
        }
    work = frame.copy()
    dates = pd.to_datetime(work["date"], errors="coerce")
    required = ["date", "open", "high", "low", "close", "volume", "amount", "index_code", "index_name", "source"]
    missing_values = int(work[required].isna().any(axis=1).sum() + (work[required].astype(str).apply(lambda col: col.str.strip().eq("")).any(axis=1)).sum())
    duplicates = int(dates.duplicated().sum())
    close = pd.to_numeric(work["close"], errors="coerce")
    abnormal = int((close.pct_change().abs() > ABNORMAL_RETURN_THRESHOLD).sum())
    start_date = _date_text(dates.min())
    end_date = _date_text(dates.max())
    reasons: list[str] = []
    if dates.isna().any():
        reasons.append("unparseable dates")
    if missing_values:
        reasons.append("missing values")
    if duplicates:
        reasons.append("duplicate dates")
    if abnormal:
        reasons.append("abnormal returns")
    status = "failed" if dates.isna().any() or missing_values else ("warning" if duplicates or abnormal else "ok")
    return {
        "fetch_success": status != "failed",
        "schema_valid": not dates.isna().any() and missing_values == 0,
        "start_date": start_date,
        "end_date": end_date,
        "row_count": int(len(work)),
        "latest_expected_date": expected,
        "end_date_gap_days": _end_date_gap_days(end_date, expected),
        "missing_required_columns": "",
        "missing_values_count": missing_values,
        "duplicate_dates_count": duplicates,
        "abnormal_return_count": abnormal,
        "quality_status": status,
        "failure_reason": "; ".join(reasons),
    }


def write_index_map(frame: pd.DataFrame, path: str | Path = DEFAULT_INDEX_MAP_OUTPUT) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame[INDEX_MAP_COLUMNS].to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def write_index_history(frame: pd.DataFrame, *, index_code: str, cache_dir: str | Path = DEFAULT_INDEX_CACHE_DIR) -> Path:
    output_dir = Path(cache_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{str(index_code).strip()}.csv"
    frame[INDEX_CACHE_COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
    return path


def write_index_data_coverage(rows: list[dict[str, Any]], path: str | Path = DEFAULT_INDEX_COVERAGE_OUTPUT) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=INDEX_DATA_COVERAGE_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def _missing_required_columns_from_error(reason: str) -> str:
    marker = "missing required columns:"
    if marker not in str(reason):
        return ""
    return str(reason).split(marker, 1)[1].strip().replace(", ", ";")


def _failure_coverage_row(
    mapping: dict[str, Any],
    reason: str,
    latest_expected_date: str,
    *,
    api_name: str = "",
    source_family: str = "",
    fetch_success: bool = False,
    schema_valid: bool = False,
    row_count: int = 0,
    start_date: str = "",
    end_date: str = "",
    missing_required_columns: str = "",
    missing_values_count: int = 0,
    duplicate_dates_count: int = 0,
    abnormal_return_count: int = 0,
    notes: str = "",
) -> dict[str, Any]:
    quality_status = "failed"
    return {
        "tracking_index_code": mapping.get("tracking_index_code", ""),
        "tracking_index_name": mapping.get("tracking_index_name", ""),
        "index_source": api_name or mapping.get("index_source", ""),
        "api_name": api_name,
        "source_family": source_family,
        "fetch_success": bool(fetch_success),
        "schema_valid": bool(schema_valid),
        "start_date": start_date,
        "end_date": end_date,
        "row_count": int(row_count),
        "latest_expected_date": latest_expected_date,
        "end_date_gap_days": _end_date_gap_days(end_date, latest_expected_date),
        "missing_required_columns": missing_required_columns,
        "missing_values_count": int(missing_values_count),
        "duplicate_dates_count": int(duplicate_dates_count),
        "abnormal_return_count": int(abnormal_return_count),
        "quality_status": quality_status,
        "usable_as_benchmark": False,
        "requires_manual_review": True,
        "failure_reason": reason,
        "notes": notes,
    }


def _source_family_for_api(api_name: str) -> str:
    candidate = next((item for item in INDEX_API_CANDIDATES if item.api_name == api_name), None)
    return candidate.source_family if candidate else "unknown"


def _attempt_index_fetch(
    code: str,
    *,
    start_date: str,
    end_date: str | None,
    ak_module: Any | None,
    candidates: list[IndexApiCandidate] | None = None,
) -> tuple[pd.DataFrame | None, IndexApiCandidate | None, list[str], int]:
    if ak_module is None:
        import akshare as ak_module
    selected = candidates or INDEX_API_CANDIDATES
    failures: list[str] = []
    eastmoney_failures = 0
    for candidate in selected:
        try:
            raw = candidate.fetcher(code, start_date, end_date, ak_module)
            if raw is None or raw.empty:
                failures.append(f"{candidate.api_name} failed: candidate returned empty data")
                if candidate.source_family == "eastmoney":
                    eastmoney_failures += 1
                continue
            return raw, candidate, failures, eastmoney_failures
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{candidate.api_name} failed: {exc}")
            if candidate.source_family == "eastmoney":
                eastmoney_failures += 1
    return None, None, failures, eastmoney_failures


def _coverage_notes(attempt_failures: list[str], *, eastmoney_failures: int, extra: str = "") -> str:
    parts = []
    if eastmoney_failures:
        parts.append(f"eastmoney_failures={eastmoney_failures}")
    if attempt_failures:
        parts.append("attempt_failures=" + " | ".join(attempt_failures))
    if extra:
        parts.append(extra)
    return "; ".join(parts)


def update_index_data(
    *,
    max_count: int = 50,
    symbols: str | list[str] | None = None,
    dry_run: bool = False,
    metadata_path: str | Path = DEFAULT_METADATA_PATH,
    config_path: str | Path = DEFAULT_INDEX_MAP_CONFIG,
    output_dir: str | Path = "output",
    cache_dir: str | Path = DEFAULT_INDEX_CACHE_DIR,
    fetcher: Fetcher | None = None,
    ak_module: Any | None = None,
    candidates: list[IndexApiCandidate] | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
    index_map = build_index_map(metadata_path=metadata_path, config_path=config_path, symbols=symbols, max_count=max_count)
    suffix = "_preview" if dry_run else ""
    map_path = write_index_map(index_map, output / f"index_map{suffix}.csv")
    expected = _latest_expected_date()
    usable = index_map[index_map["usable_as_benchmark"].astype(bool)].copy()
    if symbols is None:
        usable = usable[usable["tracking_index_code"].astype(str).str.strip().isin(DEFAULT_INDEX_UPDATE_CODES)].copy()
    coverage_rows: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for mapping in usable.to_dict("records"):
        code = str(mapping["tracking_index_code"]).strip()
        if code in seen_codes:
            continue
        seen_codes.add(code)
        attempt_failures: list[str] = []
        eastmoney_failures = 0
        raw: pd.DataFrame | None = None
        api_name = ""
        source_family = ""
        try:
            if fetcher is not None:
                raw = fetcher(code, "20190101", None)
                attrs = getattr(raw, "attrs", {})
                source = _text(attrs.get("source"), _text(attrs.get("api_name"), "mock.fetcher"))
                api_name = _text(attrs.get("api_name"), source)
                source_family = _text(attrs.get("source_family"), _source_family_for_api(api_name))
            else:
                raw, candidate, attempt_failures, eastmoney_failures = _attempt_index_fetch(
                    code,
                    start_date="20190101",
                    end_date=None,
                    ak_module=ak_module,
                    candidates=candidates,
                )
                if raw is None or candidate is None:
                    preferred = (candidates or INDEX_API_CANDIDATES)[0]
                    reason = " | ".join(attempt_failures) or "all index source candidates failed"
                    coverage_rows.append(
                        _failure_coverage_row(
                            mapping,
                            reason,
                            expected,
                            api_name=preferred.api_name,
                            source_family=preferred.source_family,
                            notes=_coverage_notes(attempt_failures, eastmoney_failures=eastmoney_failures),
                        )
                    )
                    continue
                source = candidate.api_name
                api_name = candidate.api_name
                source_family = candidate.source_family
            normalized = normalize_index_history(raw, index_code=code, index_name=str(mapping["tracking_index_name"]), source=source)
            quality = validate_index_history(normalized, latest_expected_date=expected)
            schema_valid = bool(quality.get("schema_valid")) and int(quality.get("missing_values_count") or 0) == 0
            quality_status = str(quality.get("quality_status", "failed"))
            usable_as_benchmark = bool(
                quality["fetch_success"]
                and schema_valid
                and quality_status in {"ok", "warning"}
                and mapping["usable_as_benchmark"]
            )
            row = {
                "tracking_index_code": code,
                "tracking_index_name": mapping["tracking_index_name"],
                "index_source": source,
                "api_name": api_name,
                "source_family": source_family,
                **quality,
                "fetch_success": True,
                "schema_valid": schema_valid,
                "usable_as_benchmark": usable_as_benchmark,
                "requires_manual_review": not usable_as_benchmark,
                "notes": _coverage_notes(attempt_failures, eastmoney_failures=eastmoney_failures),
            }
            if row["usable_as_benchmark"] and not dry_run:
                write_index_history(normalized, index_code=code, cache_dir=cache_dir)
            coverage_rows.append({column: row.get(column, "") for column in INDEX_DATA_COVERAGE_COLUMNS})
        except Exception as exc:  # noqa: BLE001
            raw_rows = int(len(raw)) if isinstance(raw, pd.DataFrame) else 0
            coverage_rows.append(
                _failure_coverage_row(
                    mapping,
                    str(exc),
                    expected,
                    api_name=str(api_name),
                    source_family=str(source_family),
                    fetch_success=isinstance(raw, pd.DataFrame) and not raw.empty,
                    schema_valid=False,
                    row_count=raw_rows,
                    missing_required_columns=_missing_required_columns_from_error(str(exc)),
                    notes=_coverage_notes(attempt_failures, eastmoney_failures=eastmoney_failures),
                )
            )
    coverage_path = write_index_data_coverage(coverage_rows, output / f"index_data_coverage{suffix}.csv")
    return index_map, coverage_rows, map_path, coverage_path


def summarize_index_data(
    *,
    index_map_path: str | Path = DEFAULT_INDEX_MAP_OUTPUT,
    coverage_path: str | Path = DEFAULT_INDEX_COVERAGE_OUTPUT,
    example_limit: int = 10,
) -> dict[str, Any]:
    map_path = Path(index_map_path)
    cov_path = Path(coverage_path)
    empty = {
        "status": "not_run",
        "index_map_report": str(map_path),
        "index_data_coverage_report": str(cov_path),
        "total_index_mappings": 0,
        "index_cache_written_count": 0,
        "usable_benchmark_count": 0,
        "manual_review_required_count": 0,
        "fetch_success_count": 0,
        "fetch_failed_count": 0,
        "csindex_success_count": 0,
        "eastmoney_failure_count": 0,
        "schema_invalid_count": 0,
        "low_coverage_indexes": [],
        "top_examples": [],
    }
    if not map_path.exists() or not cov_path.exists():
        return empty
    index_map = pd.read_csv(map_path, dtype={"symbol": str, "tracking_index_code": str}, encoding="utf-8-sig").fillna("")
    coverage = pd.read_csv(cov_path, dtype={"tracking_index_code": str}, encoding="utf-8-sig").fillna("")
    if index_map.empty:
        return empty
    manual = index_map["requires_manual_review"].astype(str).str.lower().isin(["true", "1", "yes"])
    fetch_success = coverage["fetch_success"].astype(str).str.lower().isin(["true", "1", "yes"]) if "fetch_success" in coverage.columns else pd.Series(False, index=coverage.index)
    schema_valid = coverage["schema_valid"].astype(str).str.lower().isin(["true", "1", "yes"]) if "schema_valid" in coverage.columns else fetch_success.copy()
    coverage_usable = coverage["usable_as_benchmark"].astype(str).str.lower().isin(["true", "1", "yes"]) if "usable_as_benchmark" in coverage.columns else pd.Series(False, index=coverage.index)
    review_required = coverage["requires_manual_review"].astype(str).str.lower().isin(["true", "1", "yes"]) if "requires_manual_review" in coverage.columns else ~coverage_usable
    source_family = coverage.get("source_family", pd.Series("", index=coverage.index)).astype(str)
    gap = pd.to_numeric(coverage.get("end_date_gap_days", pd.Series(0, index=coverage.index)), errors="coerce").fillna(0)
    low_coverage = coverage[(~fetch_success) | (gap > 7)] if not coverage.empty else coverage
    notes = coverage.get("notes", pd.Series("", index=coverage.index)).astype(str)
    eastmoney_failure_count = 0
    for text in notes:
        marker = "eastmoney_failures="
        if marker not in text:
            continue
        value = text.split(marker, 1)[1].split(";", 1)[0].strip()
        parsed = pd.to_numeric(value, errors="coerce")
        if not pd.isna(parsed):
            eastmoney_failure_count += int(parsed)
    examples = index_map.head(example_limit)[
        ["symbol", "etf_name", "tracking_index_name", "tracking_index_code", "mapping_method", "confidence", "requires_manual_review", "usable_as_benchmark"]
    ].to_dict("records")
    return {
        "status": "ok",
        "index_map_report": str(map_path),
        "index_data_coverage_report": str(cov_path),
        "total_index_mappings": int(len(index_map)),
        "index_cache_written_count": int(coverage_usable.sum()) if not coverage.empty else 0,
        "usable_benchmark_count": int(coverage_usable.sum()) if not coverage.empty else 0,
        "manual_review_required_count": int(review_required.sum()) if not coverage.empty else int(manual.sum()),
        "fetch_success_count": int(fetch_success.sum()),
        "fetch_failed_count": int((~fetch_success).sum()) if not coverage.empty else 0,
        "csindex_success_count": int((coverage_usable & source_family.eq("csindex")).sum()) if not coverage.empty else 0,
        "eastmoney_failure_count": int(eastmoney_failure_count),
        "schema_invalid_count": int((~schema_valid).sum()) if not coverage.empty else 0,
        "low_coverage_indexes": low_coverage.get("tracking_index_code", pd.Series(dtype=str)).astype(str).tolist(),
        "top_examples": examples,
    }
