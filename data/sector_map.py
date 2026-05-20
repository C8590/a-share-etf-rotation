from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import warnings

import yaml

from data.storage import normalize_symbol


DEFAULT_SECTOR_MAP_PATH = Path("config") / "etf_sector_map.yaml"
SECTOR_FIELDS = (
    "asset_class",
    "sector",
    "sector_l1",
    "sector_l2",
    "theme",
    "risk_group",
    "aliases",
    "is_defensive",
    "is_broad_market",
)
UNKNOWN_CLASSIFICATION = {
    "asset_class": "资产类别未录入",
    "sector": "行业未录入",
    "sector_l1": "行业未录入",
    "sector_l2": "行业未录入",
    "theme": "主题未录入",
    "risk_group": "风险分组未录入",
    "aliases": [],
    "is_defensive": False,
    "is_broad_market": False,
}
UNKNOWN_TEXT_VALUES = {
    "名称未录入",
    "资产类别未录入",
    "行业未录入",
    "主题未录入",
    "风险分组未录入",
}


@dataclass(frozen=True)
class SectorMappingQuality:
    row_count: int
    unknown_count: int
    missing_sector_count: int
    sector_equals_name_count: int
    sector_l2_counts: dict[str, int]
    warnings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.missing_sector_count == 0 and self.sector_equals_name_count == 0


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _aliases(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    if not text:
        return []
    return [item.strip() for item in text.replace("，", ",").split(",") if item.strip()]


def _normalise_record(raw: dict[str, Any]) -> dict[str, Any]:
    code = normalize_symbol(raw.get("code") or raw.get("symbol") or "")
    if not code:
        raise ValueError(f"ETF sector map row missing code: {raw}")

    sector_l1 = _clean_text(raw.get("sector_l1")) or _clean_text(raw.get("sector")) or UNKNOWN_CLASSIFICATION["sector_l1"]
    sector_l2 = _clean_text(raw.get("sector_l2")) or _clean_text(raw.get("sector")) or UNKNOWN_CLASSIFICATION["sector_l2"]
    sector = _clean_text(raw.get("sector")) or sector_l2
    return {
        "code": code,
        "symbol": code,
        "name": _clean_text(raw.get("name")) or "名称未录入",
        "asset_class": _clean_text(raw.get("asset_class")) or UNKNOWN_CLASSIFICATION["asset_class"],
        "sector": sector,
        "sector_l1": sector_l1,
        "sector_l2": sector_l2,
        "theme": _clean_text(raw.get("theme")) or UNKNOWN_CLASSIFICATION["theme"],
        "risk_group": _clean_text(raw.get("risk_group")) or sector_l2 or UNKNOWN_CLASSIFICATION["risk_group"],
        "aliases": _aliases(raw.get("aliases")),
        "is_defensive": _bool_value(raw.get("is_defensive")),
        "is_broad_market": _bool_value(raw.get("is_broad_market")),
    }


def load_etf_sector_map(path: str | Path = DEFAULT_SECTOR_MAP_PATH) -> dict[str, dict[str, Any]]:
    map_path = Path(path)
    if not map_path.exists():
        return {}
    with map_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    rows = raw.get("etfs", [])
    if not isinstance(rows, list):
        raise ValueError(f"{map_path} must contain an etfs list")

    records: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError(f"{map_path} contains a non-object ETF row: {item}")
        record = _normalise_record(item)
        records[record["code"]] = record
    return records


def unknown_sector_record(symbol: object, name: str = "") -> dict[str, Any]:
    code = normalize_symbol(symbol)
    record = {"code": code, "symbol": code, "name": _clean_text(name) or "名称未录入"}
    record.update(UNKNOWN_CLASSIFICATION)
    return record


def lookup_sector_mapping(
    symbol: object,
    sector_map_path: str | Path = DEFAULT_SECTOR_MAP_PATH,
    name: str = "",
) -> dict[str, Any]:
    code = normalize_symbol(symbol)
    if not code:
        return unknown_sector_record("", name)
    return load_etf_sector_map(sector_map_path).get(code) or unknown_sector_record(code, name)


def apply_sector_mapping(
    items: Iterable[dict[str, Any]],
    sector_map_path: str | Path = DEFAULT_SECTOR_MAP_PATH,
    warn_unknown: bool = True,
) -> list[dict[str, Any]]:
    mapping = load_etf_sector_map(sector_map_path)
    enriched: list[dict[str, Any]] = []
    unknown: list[str] = []

    for item in items:
        out = dict(item)
        symbol = normalize_symbol(out.get("symbol") or out.get("code") or "")
        name = _clean_text(out.get("name"))
        record = mapping.get(symbol) if mapping else None
        if record is None:
            record = unknown_sector_record(symbol, name)
            if symbol:
                unknown.append(symbol)

        out["code"] = symbol
        out["symbol"] = symbol
        if not _clean_text(out.get("name")) or out.get("name") in UNKNOWN_TEXT_VALUES:
            out["name"] = record["name"]
        for field in SECTOR_FIELDS:
            out[field] = record[field]
        if not _clean_text(out.get("tracking_index")):
            out["tracking_index"] = record["theme"]
        enriched.append(out)

    if unknown and warn_unknown:
        unique_unknown = sorted(set(unknown))
        preview = "、".join(unique_unknown[:10])
        suffix = "" if len(unique_unknown) <= 10 else f"，共 {len(unique_unknown)} 个"
        warnings.warn(f"ETF 行业映射缺失 {len(unique_unknown)} 个代码：{preview}{suffix}", UserWarning, stacklevel=2)
    return enriched


def _is_unknown(row: dict[str, Any]) -> bool:
    return any(_clean_text(row.get(field)) in UNKNOWN_TEXT_VALUES for field in ("asset_class", "sector_l1", "sector_l2", "theme"))


def validate_sector_mapping(
    items: Iterable[dict[str, Any]],
    unknown_warning_threshold: int = 0,
) -> SectorMappingQuality:
    rows = [dict(item) for item in items]
    missing_sector_count = 0
    sector_equals_name_count = 0
    unknown_count = 0
    sector_l2_counter: Counter[str] = Counter()
    notes: list[str] = []

    for row in rows:
        name = _clean_text(row.get("name"))
        sector_l1 = _clean_text(row.get("sector_l1"))
        sector_l2 = _clean_text(row.get("sector_l2"))
        if not sector_l1 or not sector_l2:
            missing_sector_count += 1
        if sector_l2 and name and sector_l2 == name:
            sector_equals_name_count += 1
        if _is_unknown(row):
            unknown_count += 1
        if sector_l2:
            sector_l2_counter[sector_l2] += 1

    if missing_sector_count:
        notes.append(f"有 {missing_sector_count} 条记录缺少行业映射")
    if sector_equals_name_count:
        notes.append(f"有 {sector_equals_name_count} 条记录的行业字段等于 ETF 名称")
    if unknown_count > unknown_warning_threshold:
        notes.append(f"有 {unknown_count} 条 ETF 映射未录入")

    return SectorMappingQuality(
        row_count=len(rows),
        unknown_count=unknown_count,
        missing_sector_count=missing_sector_count,
        sector_equals_name_count=sector_equals_name_count,
        sector_l2_counts=dict(sorted(sector_l2_counter.items())),
        warnings=tuple(notes),
    )
