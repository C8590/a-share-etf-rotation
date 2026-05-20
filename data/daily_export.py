from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from data.downloader import load_etf_pool
from data.sector_map import apply_sector_mapping
from data.storage import DATA_DIR, load_etf_data, normalize_symbol


ETF_DAILY_COLUMNS = [
    "date",
    "code",
    "name",
    "sector",
    "asset_class",
    "sector_l1",
    "sector_l2",
    "theme",
    "risk_group",
    "aliases",
    "is_defensive",
    "is_broad_market",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
]


def _display_bool(value: object) -> str:
    return "是" if bool(value) else "否"


def _display_aliases(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def build_etf_daily_frame(
    etf_pool: Iterable[dict[str, Any]] | None = None,
    data_dir: str | Path = DATA_DIR,
    start: str | None = None,
    end: str | None = None,
    allow_partial: bool = False,
) -> pd.DataFrame:
    pool = list(etf_pool) if etf_pool is not None else load_etf_pool()
    pool = apply_sector_mapping(pool)
    start_ts = pd.Timestamp(start).normalize() if start else None
    end_ts = pd.Timestamp(end).normalize() if end else None

    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for item in pool:
        symbol = normalize_symbol(item.get("symbol") or item.get("code"))
        if not symbol:
            continue
        try:
            frame = load_etf_data(symbol, data_dir=Path(data_dir), name=str(item.get("name", ""))).reset_index()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{symbol}: {exc}")
            continue

        if start_ts is not None:
            frame = frame[frame["date"] >= start_ts]
        if end_ts is not None:
            frame = frame[frame["date"] <= end_ts]
        if frame.empty:
            continue

        out = frame.copy()
        out["code"] = symbol
        out["name"] = str(item.get("name", ""))
        out["sector"] = str(item.get("sector") or item.get("sector_l2") or "行业未录入")
        out["asset_class"] = str(item.get("asset_class") or "资产类别未录入")
        out["sector_l1"] = str(item.get("sector_l1") or "行业未录入")
        out["sector_l2"] = str(item.get("sector_l2") or "行业未录入")
        out["theme"] = str(item.get("theme") or "主题未录入")
        out["risk_group"] = str(item.get("risk_group") or "风险分组未录入")
        out["aliases"] = _display_aliases(item.get("aliases"))
        out["is_defensive"] = _display_bool(item.get("is_defensive"))
        out["is_broad_market"] = _display_bool(item.get("is_broad_market"))
        frames.append(out[ETF_DAILY_COLUMNS])

    if errors and (not allow_partial or not frames):
        joined = "\n".join(f"- {item}" for item in errors)
        raise RuntimeError(f"生成 ETF 日频导出数据失败：\n{joined}")
    if errors:
        joined = "\n".join(f"- {item}" for item in errors)
        print(f"部分 ETF 本地行情不可用，已跳过这些日频导出记录：\n{joined}")
    if not frames:
        return pd.DataFrame(columns=ETF_DAILY_COLUMNS)

    result = pd.concat(frames, ignore_index=True)
    result["date"] = pd.to_datetime(result["date"]).dt.date.astype(str)
    return result.sort_values(["date", "code"]).reset_index(drop=True)


def write_etf_daily_csv(
    output_path: str | Path = Path("data") / "etf_daily.csv",
    etf_pool: Iterable[dict[str, Any]] | None = None,
    data_dir: str | Path = DATA_DIR,
    start: str | None = None,
    end: str | None = None,
    allow_partial: bool = False,
) -> Path:
    frame = build_etf_daily_frame(
        etf_pool=etf_pool,
        data_dir=data_dir,
        start=start,
        end=end,
        allow_partial=allow_partial,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path
