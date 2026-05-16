from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ETF_METADATA_COLUMNS = [
    "symbol",
    "name",
    "exchange",
    "asset_class",
    "category",
    "sub_category",
    "fund_company",
    "inception_date",
    "tracking_index_name",
    "tracking_index_code",
    "fund_size",
    "fund_size_date",
    "management_fee",
    "custody_fee",
    "latest_amount",
    "latest_price",
    "is_cross_border",
    "is_commodity",
    "is_bond",
    "is_money_market",
    "is_broad_based",
    "is_industry",
    "is_theme",
    "is_dividend",
    "is_sci_tech",
    "is_chinext",
    "inferred_category",
    "inferred_tags",
    "metadata_source",
    "metadata_updated_at",
    "field_completeness",
    "missing_fields",
    "data_quality_status",
    "notes",
]

ETF_METADATA_COVERAGE_COLUMNS = [
    "field_name",
    "total_count",
    "non_null_count",
    "missing_count",
    "coverage_ratio",
    "source",
    "importance",
    "notes",
]

UNKNOWN = "unknown"
MISSING = "missing"
UNABLE_TO_CONFIRM = "unable_to_confirm"
MISSING_MARKERS = {"", UNKNOWN, MISSING, UNABLE_TO_CONFIRM, "nan", "none", "nat", "<na>"}

FIELD_IMPORTANCE = {
    "symbol": "required",
    "name": "required",
    "exchange": "required",
    "metadata_source": "required",
    "metadata_updated_at": "required",
    "asset_class": "recommended",
    "category": "recommended",
    "sub_category": "recommended",
    "fund_company": "recommended",
    "inception_date": "recommended",
    "tracking_index_name": "recommended",
    "tracking_index_code": "recommended",
    "fund_size": "recommended",
    "fund_size_date": "recommended",
    "management_fee": "recommended",
    "custody_fee": "recommended",
    "latest_amount": "recommended",
    "latest_price": "recommended",
    "is_cross_border": "recommended",
    "is_commodity": "recommended",
    "is_bond": "recommended",
    "is_money_market": "recommended",
    "is_broad_based": "recommended",
    "is_industry": "recommended",
    "is_theme": "recommended",
    "is_dividend": "recommended",
    "is_sci_tech": "recommended",
    "is_chinext": "recommended",
    "inferred_category": "optional",
    "inferred_tags": "optional",
    "field_completeness": "optional",
    "missing_fields": "optional",
    "data_quality_status": "optional",
    "notes": "optional",
}

SOURCE_BY_FIELD = {
    "symbol": "akshare.fund_etf_spot_em",
    "name": "akshare.fund_etf_spot_em",
    "exchange": "symbol_prefix",
    "category": "akshare.fund_etf_spot_em",
    "sub_category": "akshare.fund_name_em",
    "latest_amount": "akshare.fund_etf_spot_em",
    "latest_price": "akshare.fund_etf_spot_em",
    "inferred_category": "name_inference",
    "inferred_tags": "name_inference",
}


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _exchange(symbol: str) -> str:
    symbol = str(symbol).zfill(6)
    if symbol.startswith(("5", "6")):
        return "SH"
    if symbol.startswith(("0", "1", "2", "3")):
        return "SZ"
    return UNKNOWN


def _pick_column(frame: pd.DataFrame, aliases: list[str]) -> str | None:
    columns = {str(col).strip(): col for col in frame.columns}
    for alias in aliases:
        if alias in columns:
            return columns[alias]
    lower_columns = {str(col).strip().lower(): col for col in frame.columns}
    for alias in aliases:
        found = lower_columns.get(alias.lower())
        if found is not None:
            return found
    return None


def _text(value: Any, default: str = UNKNOWN) -> str:
    if value is None:
        return default
    if pd.isna(value):
        return default
    text = str(value).strip()
    return text if text else default


def _number(value: Any) -> Any:
    parsed = pd.to_numeric(value, errors="coerce")
    return "" if pd.isna(parsed) else float(parsed)


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return UNKNOWN if pd.isna(parsed) else str(parsed.date())


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if pd.isna(value):
        return False
    text = str(value).strip().lower()
    return text not in MISSING_MARKERS


def _source_notes(fetch_errors: list[str] | None = None) -> str:
    errors = [item for item in (fetch_errors or []) if item]
    base = "real fields come only from source columns; inferred fields are name-derived and not authoritative"
    return base if not errors else base + "; fetch_warnings=" + " | ".join(errors)


def fetch_etf_metadata(source: str = "akshare", ak_module: Any | None = None) -> pd.DataFrame:
    if source != "akshare":
        raise ValueError("ETF metadata currently supports source='akshare' only")
    if ak_module is None:
        import akshare as ak_module

    fetch_errors: list[str] = []
    try:
        spot = ak_module.fund_etf_spot_em()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"failed to fetch akshare.fund_etf_spot_em: {exc}") from exc

    try:
        names = ak_module.fund_name_em()
    except Exception as exc:  # noqa: BLE001
        fetch_errors.append(f"fund_name_em failed: {exc}")
        names = pd.DataFrame()

    raw = spot.copy()
    code_col = _pick_column(raw, ["代码", "基金代码", "symbol"])
    if code_col is None:
        raise ValueError(f"fund_etf_spot_em missing code column; available={list(raw.columns)}")
    raw["_symbol"] = raw[code_col].astype(str).str.zfill(6)
    if not names.empty:
        name_code_col = _pick_column(names, ["基金代码", "代码", "symbol"])
        if name_code_col is not None:
            names = names.copy()
            names["_symbol"] = names[name_code_col].astype(str).str.zfill(6)
            keep_cols = ["_symbol"]
            for alias in ["基金类型", "基金简称"]:
                col = _pick_column(names, [alias])
                if col is not None and col not in keep_cols:
                    keep_cols.append(col)
            raw = raw.merge(names[keep_cols].drop_duplicates("_symbol"), on="_symbol", how="left", suffixes=("", "_fund_name"))
        else:
            fetch_errors.append("fund_name_em missing fund code column")
    raw.attrs["metadata_source"] = "akshare.fund_etf_spot_em" + (";akshare.fund_name_em" if not names.empty else "")
    raw.attrs["fetch_errors"] = fetch_errors
    return raw


def infer_etf_tags_from_name(name: str) -> dict[str, Any]:
    text = str(name)
    tags: list[str] = []

    def add(tag: str, keywords: list[str]) -> None:
        if any(key in text for key in keywords):
            tags.append(tag)

    add("cross_border", ["纳指", "纳斯达克", "标普", "恒生", "港股", "日经", "德国", "法国", "QDII", "海外", "中韩", "沙特"])
    add("commodity", ["黄金", "豆粕", "有色金属期货", "商品", "原油", "油气", "能源化工"])
    add("bond", ["债", "国开", "政金", "转债", "城投"])
    add("money_market", ["货币", "添益", "日利", "保证金", "现金", "快线", "快钱", "理财"])
    add("broad_based", ["沪深300", "中证500", "中证1000", "中证2000", "上证50", "A500", "中证A", "科创50", "创业板", "深证"])
    add("industry", ["证券", "银行", "军工", "半导体", "芯片", "医药", "医疗", "消费", "新能源", "光伏", "传媒", "有色", "基建", "养殖", "石油"])
    add("theme", ["人工智能", "机器人", "低空", "云计算", "大数据", "央企", "国企", "ESG", "高股息"])
    add("dividend", ["红利", "高股息"])
    add("sci_tech", ["科创", "科技", "芯片", "半导体", "人工智能"])
    add("chinext", ["创业板", "创成长"])

    ordered = list(dict.fromkeys(tags))
    if "money_market" in ordered:
        category = "money_market"
    elif "bond" in ordered:
        category = "bond"
    elif "commodity" in ordered:
        category = "commodity"
    elif "cross_border" in ordered:
        category = "cross_border"
    elif "broad_based" in ordered:
        category = "broad_based"
    elif "industry" in ordered:
        category = "industry"
    elif "theme" in ordered:
        category = "theme"
    else:
        category = UNKNOWN
    return {"inferred_category": category, "inferred_tags": ";".join(ordered)}


def normalize_etf_metadata(raw: pd.DataFrame, *, source: str = "akshare", updated_at: str | None = None) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise ValueError("ETF metadata source returned empty data")

    code_col = _pick_column(raw, ["_symbol", "代码", "基金代码", "symbol"])
    name_col = _pick_column(raw, ["名称", "基金简称", "name"])
    price_col = _pick_column(raw, ["最新价", "latest_price"])
    amount_col = _pick_column(raw, ["成交额", "latest_amount", "spot_amount"])
    fund_type_col = _pick_column(raw, ["基金类型", "sub_category"])
    source_text = str(raw.attrs.get("metadata_source") or source)
    notes = _source_notes(raw.attrs.get("fetch_errors", []))
    checked_at = updated_at or _now_text()

    rows: list[dict[str, Any]] = []
    for record in raw.to_dict("records"):
        symbol = _text(record.get(code_col), MISSING).zfill(6)
        name = _text(record.get(name_col), MISSING)
        inferred = infer_etf_tags_from_name(name)
        row = {
            "symbol": symbol,
            "name": name,
            "exchange": _exchange(symbol),
            "asset_class": UNKNOWN,
            "category": "ETF",
            "sub_category": _text(record.get(fund_type_col), UNKNOWN) if fund_type_col else UNKNOWN,
            "fund_company": UNABLE_TO_CONFIRM,
            "inception_date": UNABLE_TO_CONFIRM,
            "tracking_index_name": UNABLE_TO_CONFIRM,
            "tracking_index_code": UNABLE_TO_CONFIRM,
            "fund_size": UNABLE_TO_CONFIRM,
            "fund_size_date": UNABLE_TO_CONFIRM,
            "management_fee": UNABLE_TO_CONFIRM,
            "custody_fee": UNABLE_TO_CONFIRM,
            "latest_amount": _number(record.get(amount_col)) if amount_col else "",
            "latest_price": _number(record.get(price_col)) if price_col else "",
            "is_cross_border": UNKNOWN,
            "is_commodity": UNKNOWN,
            "is_bond": UNKNOWN,
            "is_money_market": UNKNOWN,
            "is_broad_based": UNKNOWN,
            "is_industry": UNKNOWN,
            "is_theme": UNKNOWN,
            "is_dividend": UNKNOWN,
            "is_sci_tech": UNKNOWN,
            "is_chinext": UNKNOWN,
            "inferred_category": inferred["inferred_category"],
            "inferred_tags": inferred["inferred_tags"],
            "metadata_source": source_text,
            "metadata_updated_at": checked_at,
            "field_completeness": 0.0,
            "missing_fields": "",
            "data_quality_status": "warning",
            "notes": notes,
        }
        rows.append(row)

    frame = pd.DataFrame(rows, columns=ETF_METADATA_COLUMNS)
    frame = frame.drop_duplicates("symbol", keep="first").sort_values("symbol").reset_index(drop=True)
    return validate_etf_metadata(frame)


def validate_etf_metadata(frame: pd.DataFrame) -> pd.DataFrame:
    missing_columns = [column for column in ETF_METADATA_COLUMNS if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"ETF metadata missing columns: {', '.join(missing_columns)}")

    result = frame.copy()
    required_for_row = ["symbol", "name", "exchange", "metadata_source", "metadata_updated_at"]
    measured_fields = [
        column
        for column in ETF_METADATA_COLUMNS
        if column not in {"field_completeness", "missing_fields", "data_quality_status", "notes"}
    ]
    for idx, row in result.iterrows():
        missing_required = [column for column in required_for_row if not _is_present(row.get(column))]
        missing_measured = [column for column in measured_fields if not _is_present(row.get(column))]
        completeness = (len(measured_fields) - len(missing_measured)) / len(measured_fields) if measured_fields else 0.0
        status = "failed" if missing_required else ("warning" if missing_measured else "passed")
        result.at[idx, "field_completeness"] = round(float(completeness), 4)
        result.at[idx, "missing_fields"] = ";".join(missing_measured)
        result.at[idx, "data_quality_status"] = status
    return result[ETF_METADATA_COLUMNS]


def build_etf_metadata_coverage(frame: pd.DataFrame) -> list[dict[str, Any]]:
    total = int(len(frame))
    rows: list[dict[str, Any]] = []
    for field in ETF_METADATA_COLUMNS:
        if field in {"field_completeness", "missing_fields", "data_quality_status", "notes"}:
            continue
        non_null = int(frame[field].map(_is_present).sum()) if field in frame.columns else 0
        missing = max(0, total - non_null)
        ratio = 0.0 if total == 0 else round(non_null / total, 4)
        importance = FIELD_IMPORTANCE.get(field, "optional")
        source = SOURCE_BY_FIELD.get(field, "not_available_current_source")
        note = ""
        if source == "name_inference":
            note = "derived from ETF name; do not treat as authoritative metadata"
        elif source == "not_available_current_source":
            note = "current source does not confirm this field; keep unknown/unable_to_confirm"
        rows.append(
            {
                "field_name": field,
                "total_count": total,
                "non_null_count": non_null,
                "missing_count": missing,
                "coverage_ratio": ratio,
                "source": source,
                "importance": importance,
                "notes": note,
            }
        )
    return rows


def write_etf_metadata(
    frame: pd.DataFrame,
    *,
    metadata_path: str | Path = "output/etf_metadata.csv",
    coverage_path: str | Path = "output/etf_metadata_coverage.csv",
) -> tuple[Path, Path]:
    metadata_output = Path(metadata_path)
    coverage_output = Path(coverage_path)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    coverage_output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(metadata_output, index=False, encoding="utf-8-sig")
    pd.DataFrame(build_etf_metadata_coverage(frame), columns=ETF_METADATA_COVERAGE_COLUMNS).to_csv(
        coverage_output,
        index=False,
        encoding="utf-8-sig",
    )
    return metadata_output, coverage_output


def summarize_etf_metadata(
    metadata_path: str | Path = "output/etf_metadata.csv",
    coverage_path: str | Path = "output/etf_metadata_coverage.csv",
    example_limit: int = 10,
) -> dict[str, Any]:
    meta_path = Path(metadata_path)
    cov_path = Path(coverage_path)
    if not meta_path.exists() or not cov_path.exists():
        return {
            "status": "not_run",
            "etf_metadata_report": str(meta_path),
            "etf_metadata_coverage_report": str(cov_path),
            "total_etfs": 0,
            "required_field_coverage": {},
            "recommended_field_coverage": {},
            "missing_required_fields": [],
            "low_coverage_fields": [],
            "metadata_source": "",
            "top_examples": [],
        }
    metadata = pd.read_csv(meta_path, dtype={"symbol": str}, encoding="utf-8-sig").fillna("")
    coverage = pd.read_csv(cov_path, dtype=str, encoding="utf-8-sig").fillna("")
    if metadata.empty or coverage.empty:
        return {
            "status": "not_run",
            "etf_metadata_report": str(meta_path),
            "etf_metadata_coverage_report": str(cov_path),
            "total_etfs": 0,
            "required_field_coverage": {},
            "recommended_field_coverage": {},
            "missing_required_fields": [],
            "low_coverage_fields": [],
            "metadata_source": "",
            "top_examples": [],
        }

    coverage["coverage_ratio_num"] = pd.to_numeric(coverage["coverage_ratio"], errors="coerce").fillna(0.0)
    required = coverage[coverage["importance"].eq("required")]
    recommended = coverage[coverage["importance"].eq("recommended")]
    low = coverage[(coverage["importance"].isin(["required", "recommended"])) & (coverage["coverage_ratio_num"] < 0.8)]
    missing_required = required[required["coverage_ratio_num"] < 1.0]["field_name"].astype(str).tolist()
    examples = metadata.sort_values("field_completeness").head(example_limit)[
        ["symbol", "name", "field_completeness", "missing_fields", "data_quality_status", "inferred_category", "inferred_tags"]
    ].to_dict("records")
    return {
        "status": "ok",
        "etf_metadata_report": str(meta_path),
        "etf_metadata_coverage_report": str(cov_path),
        "total_etfs": int(len(metadata)),
        "required_field_coverage": {str(row["field_name"]): float(row["coverage_ratio_num"]) for _, row in required.iterrows()},
        "recommended_field_coverage": {str(row["field_name"]): float(row["coverage_ratio_num"]) for _, row in recommended.iterrows()},
        "missing_required_fields": missing_required,
        "low_coverage_fields": low["field_name"].astype(str).tolist(),
        "metadata_source": ";".join(sorted(set(metadata["metadata_source"].astype(str)))),
        "top_examples": examples,
    }


def update_etf_metadata(
    *,
    source: str = "akshare",
    max_count: int | None = None,
    dry_run: bool = False,
    output_dir: str | Path = "output",
    ak_module: Any | None = None,
) -> tuple[pd.DataFrame, Path, Path]:
    raw = fetch_etf_metadata(source=source, ak_module=ak_module)
    if max_count is not None and int(max_count) > 0:
        attrs = dict(raw.attrs)
        raw = raw.head(int(max_count)).copy()
        raw.attrs.update(attrs)
    metadata = normalize_etf_metadata(raw, source=source)
    output = Path(output_dir)
    suffix = "_preview" if dry_run else ""
    metadata_path = output / f"etf_metadata{suffix}.csv"
    coverage_path = output / f"etf_metadata_coverage{suffix}.csv"
    write_etf_metadata(metadata, metadata_path=metadata_path, coverage_path=coverage_path)
    return metadata, metadata_path, coverage_path
