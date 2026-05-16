from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from data.downloader import load_etf_pool, normalize_source_frame
from data.quality import RETURN_WARNING_THRESHOLD, analyze_single_etf
from data.schema import PRICE_CACHE_REQUIRED_COLUMNS
from data.trading_calendar import latest_trading_day_on_or_before


MAX_SOURCE_EVAL_COUNT = 20
DEFAULT_SOURCE_EVAL_SYMBOLS = [
    "510300",
    "510500",
    "159915",
    "588000",
    "510880",
    "512880",
    "512100",
    "512480",
    "159928",
    "159032",
    "516840",
    "560320",
]

SOURCE_AUDIT_COLUMNS = [
    "symbol",
    "name",
    "source_candidate",
    "api_name",
    "adjust",
    "fetch_success",
    "failure_reason",
    "start_date",
    "end_date",
    "row_count",
    "latest_expected_date",
    "end_date_gap_days",
    "missing_required_columns",
    "abnormal_return_count",
    "max_abs_return",
    "max_return_date",
    "duplicate_dates_count",
    "missing_values_count",
    "zero_amount_days",
    "schema_valid",
    "quality_passed",
    "quality_reason",
    "close_overlap_days",
    "max_abs_close_diff_vs_sina",
    "max_abs_return_diff_vs_sina",
    "preferred_candidate",
    "preference_reason",
    "safe_to_promote",
    "requires_manual_review",
    "notes",
]

SOURCE_CANDIDATES = {
    "sina_unknown": {"api_name": "fund_etf_hist_sina", "adjust": "unknown", "source": "akshare.fund_etf_hist_sina"},
    "em_qfq": {"api_name": "fund_etf_hist_em", "adjust": "qfq", "source": "akshare.fund_etf_hist_em.qfq"},
    "em_none": {"api_name": "fund_etf_hist_em", "adjust": "none", "source": "akshare.fund_etf_hist_em.none"},
}

CLOSE_DIFF_REVIEW_RATIO = 0.05
RETURN_DIFF_REVIEW_THRESHOLD = 0.03
ROW_COUNT_DEFICIT_RATIO = 0.90


@dataclass
class SourceSample:
    symbol: str
    name: str
    source_candidate: str
    api_name: str
    adjust: str
    fetch_success: bool
    frame: pd.DataFrame | None = None
    failure_reason: str = ""
    temp_csv: str = ""


Fetcher = Callable[[str, str, str | None], pd.DataFrame]


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int_value(value: Any) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0 if pd.isna(parsed) else int(float(parsed))


def _sina_symbol(symbol: str) -> str:
    return f"sh{symbol}" if str(symbol).startswith(("5", "6")) else f"sz{symbol}"


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


def _symbol_name_map(config_path: str | Path = "config/etf_universe.yaml") -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        for item in load_etf_pool(config_path):
            names[str(item.get("symbol", "")).zfill(6)] = str(item.get("name", ""))
    except Exception:
        pass
    universe_path = Path("data") / "universe" / "etf_universe.csv"
    if universe_path.exists():
        try:
            frame = pd.read_csv(universe_path, dtype={"symbol": str}).fillna("")
            for row in frame.to_dict("records"):
                symbol = str(row.get("symbol", "")).zfill(6)
                name = str(row.get("name", ""))
                if symbol and name:
                    names.setdefault(symbol, name)
        except Exception:
            pass
    return names


def _core_symbols(config_path: str | Path = "config/etf_universe.yaml") -> list[str]:
    try:
        import yaml

        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        symbols = ((raw.get("presets") or {}).get("core_11") or {}).get("symbols") or []
        return [str(symbol).zfill(6) for symbol in symbols]
    except Exception:
        return []


def build_source_eval_symbols(
    *,
    pool: str | None = None,
    symbols: str | list[str] | None = None,
    max_count: int = MAX_SOURCE_EVAL_COUNT,
    config_path: str | Path = "config/etf_universe.yaml",
) -> list[dict[str, str]]:
    if max_count > MAX_SOURCE_EVAL_COUNT:
        raise ValueError(f"source preference eval max_count must be <= {MAX_SOURCE_EVAL_COUNT}")
    if pool and pool != "core_11":
        raise ValueError("source preference eval only supports --pool core_11")

    if symbols:
        if isinstance(symbols, str):
            requested = [item.strip().zfill(6) for item in symbols.split(",") if item.strip()]
        else:
            requested = [str(item).zfill(6) for item in symbols]
    elif pool == "core_11":
        requested = [*_core_symbols(config_path), "159032", "516840", "560320"]
    else:
        requested = DEFAULT_SOURCE_EVAL_SYMBOLS.copy()

    requested = list(dict.fromkeys(requested))[: int(max_count)]
    names = _symbol_name_map(config_path)
    return [{"symbol": symbol, "name": names.get(symbol, "")} for symbol in requested]


def fetch_sina_sample(symbol: str, start_date: str = "20190101", end_date: str | None = None, ak_module: Any | None = None) -> pd.DataFrame:
    if ak_module is None:
        import akshare as ak_module

    raw = ak_module.fund_etf_hist_sina(symbol=_sina_symbol(symbol))
    frame = normalize_source_frame(symbol, raw)
    start = pd.to_datetime(start_date, errors="coerce")
    end = pd.to_datetime(end_date or datetime.now().strftime("%Y%m%d"), errors="coerce")
    if not pd.isna(start):
        frame = frame[frame["date"] >= start]
    if not pd.isna(end):
        frame = frame[frame["date"] <= end]
    return frame.reset_index(drop=True)


def fetch_em_qfq_sample(symbol: str, start_date: str = "20190101", end_date: str | None = None, ak_module: Any | None = None) -> pd.DataFrame:
    return _fetch_em_sample(symbol, start_date=start_date, end_date=end_date, adjust="qfq", ak_module=ak_module)


def fetch_em_none_sample(symbol: str, start_date: str = "20190101", end_date: str | None = None, ak_module: Any | None = None) -> pd.DataFrame:
    return _fetch_em_sample(symbol, start_date=start_date, end_date=end_date, adjust="", ak_module=ak_module)


def _fetch_em_sample(
    symbol: str,
    *,
    start_date: str = "20190101",
    end_date: str | None = None,
    adjust: str = "qfq",
    ak_module: Any | None = None,
) -> pd.DataFrame:
    if ak_module is None:
        import akshare as ak_module

    raw = ak_module.fund_etf_hist_em(
        symbol=str(symbol).zfill(6),
        period="daily",
        start_date=start_date,
        end_date=end_date or datetime.now().strftime("%Y%m%d"),
        adjust=adjust,
    )
    return normalize_source_frame(symbol, raw)


def _storage_frame(sample: SourceSample) -> pd.DataFrame:
    if sample.frame is None:
        return pd.DataFrame()
    frame = sample.frame.copy()
    if "date" not in frame.columns:
        frame = frame.reset_index()
    frame["symbol"] = sample.symbol
    frame["name"] = sample.name
    frame["source"] = SOURCE_CANDIDATES[sample.source_candidate]["source"]
    return frame


def _return_stats(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty or "close" not in frame.columns:
        return {"abnormal_return_count": 0, "max_abs_return": "", "max_return_date": ""}
    work = frame.copy()
    if "date" not in work.columns:
        work = work.reset_index()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    returns = work.sort_values("date")["close"].pct_change()
    abs_returns = returns.abs()
    abnormal_count = int((abs_returns > RETURN_WARNING_THRESHOLD).sum())
    if abs_returns.dropna().empty:
        return {"abnormal_return_count": abnormal_count, "max_abs_return": "", "max_return_date": ""}
    idx = abs_returns.idxmax()
    return {
        "abnormal_return_count": abnormal_count,
        "max_abs_return": float(abs_returns.loc[idx]),
        "max_return_date": _date_text(work.loc[idx, "date"]),
    }


def _comparison_vs_sina(frame: pd.DataFrame, sina_frame: pd.DataFrame | None) -> dict[str, Any]:
    result = {"close_overlap_days": 0, "max_abs_close_diff_vs_sina": "", "max_abs_return_diff_vs_sina": ""}
    if frame.empty or sina_frame is None or sina_frame.empty or "date" not in frame.columns or "date" not in sina_frame.columns:
        return result
    left = sina_frame[["date", "close"]].copy()
    right = frame[["date", "close"]].copy()
    left["date"] = pd.to_datetime(left["date"], errors="coerce")
    right["date"] = pd.to_datetime(right["date"], errors="coerce")
    left["close"] = pd.to_numeric(left["close"], errors="coerce")
    right["close"] = pd.to_numeric(right["close"], errors="coerce")
    merged = left.merge(right, on="date", how="inner", suffixes=("_sina", "_candidate")).dropna()
    result["close_overlap_days"] = int(len(merged))
    if merged.empty:
        return result
    close_diff = (merged["close_candidate"] - merged["close_sina"]).abs()
    result["max_abs_close_diff_vs_sina"] = float(close_diff.max())

    left_returns = left.sort_values("date").assign(return_sina=left.sort_values("date")["close"].pct_change())[["date", "return_sina"]]
    right_returns = right.sort_values("date").assign(return_candidate=right.sort_values("date")["close"].pct_change())[["date", "return_candidate"]]
    return_overlap = left_returns.merge(right_returns, on="date", how="inner").dropna()
    if not return_overlap.empty:
        result["max_abs_return_diff_vs_sina"] = float((return_overlap["return_candidate"] - return_overlap["return_sina"]).abs().max())
    return result


def _build_row(sample: SourceSample, latest_expected_date: str, sina_frame: pd.DataFrame | None) -> dict[str, Any]:
    info = SOURCE_CANDIDATES[sample.source_candidate]
    base = {
        "symbol": sample.symbol,
        "name": sample.name,
        "source_candidate": sample.source_candidate,
        "api_name": info["api_name"],
        "adjust": info["adjust"],
        "fetch_success": bool(sample.fetch_success),
        "failure_reason": sample.failure_reason,
        "start_date": "",
        "end_date": "",
        "row_count": 0,
        "latest_expected_date": latest_expected_date,
        "end_date_gap_days": 0,
        "missing_required_columns": "",
        "abnormal_return_count": 0,
        "max_abs_return": "",
        "max_return_date": "",
        "duplicate_dates_count": 0,
        "missing_values_count": 0,
        "zero_amount_days": 0,
        "schema_valid": False,
        "quality_passed": False,
        "quality_reason": sample.failure_reason,
        "close_overlap_days": 0,
        "max_abs_close_diff_vs_sina": "",
        "max_abs_return_diff_vs_sina": "",
        "preferred_candidate": "",
        "preference_reason": "",
        "safe_to_promote": False,
        "requires_manual_review": False,
        "notes": "",
    }
    if not sample.fetch_success or sample.frame is None:
        return base

    frame = _storage_frame(sample)
    missing_required = [column for column in PRICE_CACHE_REQUIRED_COLUMNS if column not in frame.columns]
    base["missing_required_columns"] = ";".join(missing_required)
    base["schema_valid"] = not missing_required
    dates = pd.to_datetime(frame["date"], errors="coerce") if "date" in frame.columns else pd.Series(dtype="datetime64[ns]")
    end_date = _date_text(dates.max()) if not dates.empty else ""
    stats = _return_stats(frame)
    missing_values = int(frame[[column for column in PRICE_CACHE_REQUIRED_COLUMNS if column in frame.columns]].isna().any(axis=1).sum()) if not frame.empty else 0
    zero_amount = int((pd.to_numeric(frame.get("amount", pd.Series(dtype=float)), errors="coerce") <= 0).sum()) if "amount" in frame.columns else 0
    duplicate_dates = int(dates.duplicated().sum()) if not dates.empty else 0
    quality = analyze_single_etf(sample.symbol, sample.name, frame) if base["schema_valid"] else None
    comparison = _comparison_vs_sina(frame, sina_frame)
    base.update(
        {
            "start_date": _date_text(dates.min()) if not dates.empty else "",
            "end_date": end_date,
            "row_count": int(len(frame)),
            "end_date_gap_days": _end_date_gap_days(end_date, latest_expected_date),
            "abnormal_return_count": stats["abnormal_return_count"],
            "max_abs_return": stats["max_abs_return"],
            "max_return_date": stats["max_return_date"],
            "duplicate_dates_count": duplicate_dates,
            "missing_values_count": missing_values,
            "zero_amount_days": zero_amount,
            "quality_passed": bool(quality and quality.status in {"passed", "warning"}),
            "quality_reason": "" if quality is None else "; ".join([*quality.errors, *quality.warnings]),
            **comparison,
            "notes": f"temp_csv={sample.temp_csv}" if sample.temp_csv else "",
        }
    )
    return base


def _close_diff_is_material(row: dict[str, Any], sina_row: dict[str, Any] | None) -> bool:
    if not sina_row:
        return False
    overlap = _int_value(row.get("close_overlap_days", 0))
    if overlap <= 0:
        return False
    close_diff = pd.to_numeric(row.get("max_abs_close_diff_vs_sina", ""), errors="coerce")
    return_diff = pd.to_numeric(row.get("max_abs_return_diff_vs_sina", ""), errors="coerce")
    if not pd.isna(return_diff) and float(return_diff) > RETURN_DIFF_REVIEW_THRESHOLD:
        return True
    if pd.isna(close_diff) or float(close_diff) <= 0:
        return False
    sina_close = pd.to_numeric(sina_row.get("_median_close", ""), errors="coerce")
    if pd.isna(sina_close) or float(sina_close) <= 0:
        return False
    return float(close_diff) / float(sina_close) > CLOSE_DIFF_REVIEW_RATIO


def _select_preference(rows: list[dict[str, Any]]) -> tuple[str, str, bool, bool]:
    by_candidate = {str(row["source_candidate"]): row for row in rows}
    sina = by_candidate.get("sina_unknown")
    qfq = by_candidate.get("em_qfq")
    none = by_candidate.get("em_none")
    sina_success = bool(sina and _bool_value(sina.get("fetch_success")))
    qfq_success = bool(qfq and _bool_value(qfq.get("fetch_success")))
    none_success = bool(none and _bool_value(none.get("fetch_success")))

    if qfq_success:
        qfq_rows = _int_value(qfq.get("row_count", 0))
        qfq_ok = bool(_bool_value(qfq.get("schema_valid")) and _bool_value(qfq.get("quality_passed")) and qfq_rows >= 250)
        reasons: list[str] = []
        manual = _close_diff_is_material(qfq, sina)
        if manual:
            reasons.append("em_qfq differs materially from Sina on overlapping dates")
        if not qfq_ok:
            reasons.append("em_qfq failed row/schema/quality requirements")
        if sina_success and sina:
            sina_rows = _int_value(sina.get("row_count", 0))
            if qfq_rows < int(sina_rows * ROW_COUNT_DEFICIT_RATIO):
                reasons.append("em_qfq row_count is materially lower than Sina")
            if str(qfq.get("end_date", "")) and str(sina.get("end_date", "")):
                if pd.Timestamp(qfq["end_date"]) < pd.Timestamp(sina["end_date"]):
                    reasons.append("em_qfq end_date is behind Sina")
        if qfq_ok and not reasons:
            return "em_qfq", "em_qfq has explicit qfq adjustment, sufficient rows, valid schema, acceptable quality, and no worse recency than Sina", True, False
        if manual:
            return "em_qfq", "; ".join(reasons), False, True
        if none_success:
            return "em_none", "; ".join(reasons + ["em_none is available only as fallback evidence"]), False, False
        if sina_success:
            return "sina_unknown", "; ".join(reasons + ["keep Sina fallback until em_qfq is explainable"]), False, False
        return "em_qfq", "; ".join(reasons), False, False

    if sina_success:
        return "sina_unknown", "em_qfq failed; keep existing Sina fallback evidence", False, False
    if none_success:
        return "em_none", "em_qfq and Sina failed; em_none is only diagnostic fallback, not promotion candidate", False, True
    return "unknown", "all source candidates failed", False, True


def compare_source_data(
    symbol: str,
    name: str,
    samples: list[SourceSample],
    *,
    latest_expected_date: str | None = None,
) -> list[dict[str, Any]]:
    expected = latest_expected_date or _latest_expected_date()
    sina_sample = next((sample for sample in samples if sample.source_candidate == "sina_unknown" and sample.fetch_success), None)
    sina_frame = _storage_frame(sina_sample) if sina_sample is not None else None
    rows = [_build_row(sample, expected, sina_frame) for sample in samples]
    if sina_frame is not None and not sina_frame.empty:
        median_close = pd.to_numeric(sina_frame.get("close"), errors="coerce").median()
        for row in rows:
            row["_median_close"] = "" if pd.isna(median_close) else float(median_close)
    preferred, reason, safe, manual = _select_preference(rows)
    for row in rows:
        row["preferred_candidate"] = preferred
        row["preference_reason"] = reason
        row["safe_to_promote"] = bool(safe and row["source_candidate"] == preferred)
        row["requires_manual_review"] = bool(manual)
        row.pop("_median_close", None)
        row["notes"] = "; ".join(item for item in [str(row.get("notes", "")), "evaluation only; formal cache not modified"] if item)
    return [{column: row.get(column, "") for column in SOURCE_AUDIT_COLUMNS} for row in rows]


def _write_temp_sample(sample: SourceSample, run_dir: Path) -> SourceSample:
    if not sample.fetch_success or sample.frame is None:
        return sample
    frame = _storage_frame(sample)
    path = run_dir / f"{sample.symbol}_{sample.source_candidate}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    sample.temp_csv = str(path)
    return sample


def write_source_preference_audit(rows: list[dict[str, Any]], path: str | Path = "output/source_preference_audit.csv") -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=SOURCE_AUDIT_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def summarize_source_preference_audit(
    rows: list[dict[str, Any]] | None = None,
    report_path: str | Path = "output/source_preference_audit.csv",
    example_limit: int = 10,
) -> dict[str, Any]:
    path = Path(report_path)
    if rows is None:
        if not path.exists():
            return {
                "status": "not_run",
                "report": str(path),
                "total_symbols": 0,
                "total_candidates": 0,
                "em_qfq_success_count": 0,
                "sina_success_count": 0,
                "em_qfq_safe_to_promote_count": 0,
                "manual_review_required_count": 0,
                "preferred_candidate_counts": {},
                "top_examples": [],
            }
        frame = pd.read_csv(path, dtype={"symbol": str}, encoding="utf-8-sig").fillna("")
    else:
        frame = pd.DataFrame(rows)
    if frame.empty:
        return {
            "status": "not_run",
            "report": str(path),
            "total_symbols": 0,
            "total_candidates": 0,
            "em_qfq_success_count": 0,
            "sina_success_count": 0,
            "em_qfq_safe_to_promote_count": 0,
            "manual_review_required_count": 0,
            "preferred_candidate_counts": {},
            "top_examples": [],
        }
    success = frame["fetch_success"].astype(str).str.lower().isin(["true", "1", "yes"])
    safe = frame["safe_to_promote"].astype(str).str.lower().isin(["true", "1", "yes"])
    manual = frame["requires_manual_review"].astype(str).str.lower().isin(["true", "1", "yes"])
    preferred_counts = frame.drop_duplicates("symbol")["preferred_candidate"].value_counts().sort_index().to_dict()
    examples = frame[frame["source_candidate"].eq("em_qfq")].head(example_limit)[
        ["symbol", "name", "fetch_success", "row_count", "end_date", "preferred_candidate", "safe_to_promote", "requires_manual_review", "preference_reason"]
    ].to_dict("records")
    return {
        "status": "ok",
        "report": str(path),
        "total_symbols": int(frame["symbol"].astype(str).nunique()),
        "total_candidates": int(len(frame)),
        "em_qfq_success_count": int((frame["source_candidate"].eq("em_qfq") & success).sum()),
        "sina_success_count": int((frame["source_candidate"].eq("sina_unknown") & success).sum()),
        "em_qfq_safe_to_promote_count": int((frame["source_candidate"].eq("em_qfq") & safe).sum()),
        "manual_review_required_count": int(frame[manual]["symbol"].astype(str).nunique()),
        "preferred_candidate_counts": {str(k): int(v) for k, v in preferred_counts.items()},
        "top_examples": examples,
    }


def run_source_preference_evaluation(
    *,
    pool: str | None = None,
    symbols: str | list[str] | None = None,
    max_count: int = MAX_SOURCE_EVAL_COUNT,
    start_date: str = "20190101",
    end_date: str | None = None,
    output_dir: str | Path = "output",
    source_eval_root: str | Path = "data/source_eval",
    config_path: str | Path = "config/etf_universe.yaml",
    fetchers: dict[str, Fetcher] | None = None,
) -> tuple[list[dict[str, Any]], Path, Path]:
    selected = build_source_eval_symbols(pool=pool, symbols=symbols, max_count=max_count, config_path=config_path)
    run_id = "source_eval_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(source_eval_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    fetchers = fetchers or {
        "sina_unknown": fetch_sina_sample,
        "em_qfq": fetch_em_qfq_sample,
        "em_none": fetch_em_none_sample,
    }
    latest_expected = _latest_expected_date()
    rows: list[dict[str, Any]] = []
    for item in selected:
        symbol = str(item["symbol"]).zfill(6)
        name = str(item.get("name", ""))
        samples: list[SourceSample] = []
        for candidate in ["sina_unknown", "em_qfq", "em_none"]:
            info = SOURCE_CANDIDATES[candidate]
            try:
                frame = fetchers[candidate](symbol, start_date, end_date)
                sample = SourceSample(symbol=symbol, name=name, source_candidate=candidate, api_name=info["api_name"], adjust=info["adjust"], fetch_success=True, frame=frame)
                sample = _write_temp_sample(sample, run_dir)
            except Exception as exc:  # noqa: BLE001
                sample = SourceSample(
                    symbol=symbol,
                    name=name,
                    source_candidate=candidate,
                    api_name=info["api_name"],
                    adjust=info["adjust"],
                    fetch_success=False,
                    failure_reason=str(exc),
                )
            samples.append(sample)
        rows.extend(compare_source_data(symbol, name, samples, latest_expected_date=latest_expected))

    audit_path = write_source_preference_audit(rows, Path(output_dir) / "source_preference_audit.csv")
    return rows, audit_path, run_dir
