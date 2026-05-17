from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


MANUAL_REVIEW_SYMBOLS = ["159231", "159246", "159287", "159387", "560320"]

EVIDENCE_COLUMNS = [
    "symbol",
    "name",
    "row_count",
    "first_date",
    "last_date",
    "history_status",
    "abnormal_return_flag",
    "abnormal_return_dates",
    "max_abs_return",
    "low_liquidity_flag",
    "avg_amount_20",
    "zero_amount_days",
    "existing_review_reason",
    "evidence_summary",
    "reviewer_can_decide",
    "recommended_review_decision",
    "recommended_status",
    "next_action",
    "notes",
]

DECISION_TEMPLATE_COLUMNS = [
    "symbol",
    "name",
    "review_decision",
    "review_status",
    "unblock_allowed",
    "reason",
    "required_future_condition",
    "reviewer_note",
]

DEFAULT_REQUIRED_FUTURE_CONDITION = "sufficient history + anomaly explained + liquidity acceptable"


def _read_csv(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    return pd.read_csv(file_path, dtype={"symbol": str}, encoding="utf-8-sig").fillna("")


def _read_json(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text if text else default


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int_value(value: Any, default: int = 0) -> int:
    number = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(number) else int(float(number))


def _float_value(value: Any) -> float | None:
    number = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(number) else float(number)


def _format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def _symbol_map(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    return {
        str(row.get("symbol", "")).zfill(6): row
        for row in frame.to_dict("records")
        if _text(row.get("symbol"))
    }


def _failure_map(frame: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for row in frame.to_dict("records"):
        symbol = str(row.get("symbol", "")).zfill(6)
        if symbol:
            rows.setdefault(symbol, []).append(row)
    return rows


def _split_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for item in _text(value).replace("|", ";").replace(",", ";").split(";"):
            item = item.strip()
            if item:
                tokens.add(item)
    return tokens


def _load_cache_metrics(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {
            "cache_exists": False,
            "abnormal_return_dates": "",
            "max_abs_return": "",
            "avg_amount_20": "",
            "zero_amount_days": "",
        }
    try:
        frame = pd.read_csv(cache_path, encoding="utf-8-sig").fillna("")
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return {
            "cache_exists": False,
            "abnormal_return_dates": "",
            "max_abs_return": "",
            "avg_amount_20": "",
            "zero_amount_days": "",
        }
    if frame.empty:
        return {
            "cache_exists": True,
            "abnormal_return_dates": "",
            "max_abs_return": "",
            "avg_amount_20": "",
            "zero_amount_days": "",
        }

    close = pd.to_numeric(frame.get("close", pd.Series(dtype=float)), errors="coerce")
    returns = close.pct_change()
    abnormal_mask = returns.abs() > 0.2
    dates = frame.get("date", pd.Series(dtype=str)).astype(str)
    abnormal_dates = dates[abnormal_mask.fillna(False)].tolist()
    amount = pd.to_numeric(frame.get("amount", pd.Series(dtype=float)), errors="coerce")
    return {
        "cache_exists": True,
        "abnormal_return_dates": ";".join(abnormal_dates),
        "max_abs_return": _format_float(float(returns.abs().max()) if not returns.dropna().empty else None),
        "avg_amount_20": _format_float(float(amount.tail(20).mean()) if not amount.dropna().empty else None, digits=2),
        "zero_amount_days": int(amount.fillna(0).le(0).sum()) if "amount" in frame.columns else "",
    }


def _evidence_summary(
    *,
    row_count: int,
    history_status: str,
    abnormal_return_flag: bool,
    low_liquidity_flag: bool,
    abnormal_return_dates: str,
    max_abs_return: str,
    avg_amount_20: str,
    zero_amount_days: Any,
) -> str:
    parts = [f"历史状态={history_status or 'unknown'}，已有 {row_count} 行。"]
    if abnormal_return_flag:
        date_text = abnormal_return_dates or "日期待人工确认"
        return_text = f"，最大绝对日收益 {max_abs_return}" if max_abs_return else ""
        parts.append(f"存在异常日收益证据：{date_text}{return_text}。")
    if low_liquidity_flag:
        amount_text = f"近20日平均成交额 {avg_amount_20}" if avg_amount_20 else "近20日平均成交额待确认"
        zero_text = f"，零成交天数 {zero_amount_days}" if zero_amount_days != "" else ""
        parts.append(f"存在低流动性证据：{amount_text}{zero_text}。")
    if history_status == "very_short_history":
        parts.append("极短历史，不能通过人工复核直接解除阻断。")
    if not abnormal_return_flag and not low_liquidity_flag and history_status != "very_short_history":
        parts.append("证据仍不足以解除阻断。")
    return " ".join(parts)


def _next_action(*, history_status: str, abnormal_return_flag: bool, low_liquidity_flag: bool) -> str:
    actions = ["keep_blocked"]
    if history_status in {"short_history", "very_short_history"}:
        actions.append("wait_for_sufficient_history")
    if abnormal_return_flag:
        actions.append("explain_abnormal_return_before_any_unblock")
    if low_liquidity_flag:
        actions.append("verify_liquidity_before_candidate_use")
    return ";".join(actions)


def _notes(
    *,
    diagnosis: dict[str, Any],
    manual: dict[str, Any],
    candidate: dict[str, Any],
    qa_report: dict[str, Any],
) -> str:
    notes = [
        "report only; does not clear manual_review_required",
        "default decision is keep_blocked",
        _text(diagnosis.get("reason")),
        _text(manual.get("notes")),
        f"candidate_status={_text(candidate.get('candidate_status'), 'unknown')}",
    ]
    qa_exit = _text(qa_report.get("qa_exit_status"))
    if qa_exit:
        notes.append(f"qa_exit_status={qa_exit}")
    return "; ".join(dict.fromkeys([item for item in notes if item]))


def build_manual_review_evidence_pack(
    *,
    project_root: str | Path = ".",
    output_dir: str | Path | None = None,
    symbols: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = Path(project_root)
    output = root / (output_dir or "output")
    manual_frame = _read_csv(output / "manual_review_list.csv")
    diagnosis_by_symbol = _symbol_map(_read_csv(output / "data_quality_diagnosis.csv"))
    quality_by_symbol = _symbol_map(_read_csv(output / "data_quality_report.csv"))
    failure_by_symbol = _failure_map(_read_csv(output / "data_failure_summary.csv"))
    observation_by_symbol = _symbol_map(_read_csv(output / "short_history_observation_pool.csv"))
    candidate_by_symbol = _symbol_map(_read_csv(output / "candidate_gate.csv"))
    qa_report = _read_json(output / "qa_report.json")

    if manual_frame.empty:
        target_symbols = symbols or []
        manual_by_symbol: dict[str, dict[str, Any]] = {}
    else:
        manual_by_symbol = _symbol_map(manual_frame)
        target_symbols = symbols or sorted(manual_by_symbol)
    target_symbols = [str(symbol).zfill(6) for symbol in target_symbols]

    evidence_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    for symbol in target_symbols:
        manual = manual_by_symbol.get(symbol, {})
        diagnosis = diagnosis_by_symbol.get(symbol, {})
        quality = quality_by_symbol.get(symbol, {})
        observation = observation_by_symbol.get(symbol, {})
        candidate = candidate_by_symbol.get(symbol, {})
        failures = failure_by_symbol.get(symbol, [])
        cache_metrics = _load_cache_metrics(root / "data" / "cache" / f"{symbol}.csv")

        row_count = _int_value(manual.get("row_count") or diagnosis.get("row_count") or observation.get("row_count"))
        first_date = _text(manual.get("first_date") or diagnosis.get("first_date") or observation.get("first_date") or quality.get("start_date"))
        last_date = _text(manual.get("last_date") or diagnosis.get("last_date") or observation.get("last_date") or quality.get("end_date"))
        history_status = _text(manual.get("history_status") or diagnosis.get("history_status") or observation.get("history_status"), "unknown")
        tokens = _split_tokens(
            manual.get("manual_review_reason"),
            diagnosis.get("failure_type"),
            diagnosis.get("secondary_failure_type"),
            observation.get("manual_review_reason"),
            quality.get("failure_types"),
            ";".join(_text(row.get("failure_type")) for row in failures),
        )
        abnormal_return_flag = (
            _bool_value(manual.get("abnormal_return_flag"))
            or _bool_value(observation.get("abnormal_return_flag"))
            or "abnormal_return" in tokens
        )
        low_liquidity_flag = (
            _bool_value(manual.get("low_liquidity_flag"))
            or _bool_value(observation.get("low_liquidity_flag"))
            or _text(diagnosis.get("liquidity_status")) == "low_liquidity"
            or _text(candidate.get("liquidity_status")) == "low_liquidity"
            or "low_liquidity" in tokens
            or "zero_or_low_liquidity" in tokens
        )
        existing_review_reason = _text(manual.get("manual_review_reason") or observation.get("manual_review_reason") or diagnosis.get("reason"), "manual_review_required")
        evidence_summary = _evidence_summary(
            row_count=row_count,
            history_status=history_status,
            abnormal_return_flag=abnormal_return_flag,
            low_liquidity_flag=low_liquidity_flag,
            abnormal_return_dates=str(cache_metrics["abnormal_return_dates"]),
            max_abs_return=str(cache_metrics["max_abs_return"]),
            avg_amount_20=str(cache_metrics["avg_amount_20"]),
            zero_amount_days=cache_metrics["zero_amount_days"],
        )
        next_action = _next_action(
            history_status=history_status,
            abnormal_return_flag=abnormal_return_flag,
            low_liquidity_flag=low_liquidity_flag,
        )
        name = _text(manual.get("name") or diagnosis.get("name") or observation.get("name") or candidate.get("name"))
        evidence_row = {
            "symbol": symbol,
            "name": name,
            "row_count": row_count,
            "first_date": first_date,
            "last_date": last_date,
            "history_status": history_status,
            "abnormal_return_flag": bool(abnormal_return_flag),
            "abnormal_return_dates": cache_metrics["abnormal_return_dates"],
            "max_abs_return": cache_metrics["max_abs_return"],
            "low_liquidity_flag": bool(low_liquidity_flag),
            "avg_amount_20": cache_metrics["avg_amount_20"],
            "zero_amount_days": cache_metrics["zero_amount_days"],
            "existing_review_reason": existing_review_reason,
            "evidence_summary": evidence_summary,
            "reviewer_can_decide": False,
            "recommended_review_decision": "keep_blocked",
            "recommended_status": "blocked_until_review",
            "next_action": next_action,
            "notes": _notes(diagnosis=diagnosis, manual=manual, candidate=candidate, qa_report=qa_report),
        }
        evidence_rows.append({column: evidence_row.get(column, "") for column in EVIDENCE_COLUMNS})
        reason_parts = [existing_review_reason]
        if history_status == "very_short_history":
            reason_parts.append("very_short_history")
        if abnormal_return_flag:
            reason_parts.append("abnormal_return_not_explained")
        if low_liquidity_flag:
            reason_parts.append("liquidity_not_confirmed")
        decision_rows.append(
            {
                "symbol": symbol,
                "name": name,
                "review_decision": "keep_blocked",
                "review_status": "blocked_until_review",
                "unblock_allowed": False,
                "reason": ";".join(dict.fromkeys([item for item in reason_parts if item])),
                "required_future_condition": DEFAULT_REQUIRED_FUTURE_CONDITION,
                "reviewer_note": "",
            }
        )
    return evidence_rows, decision_rows


def write_manual_review_evidence_outputs(
    evidence_rows: list[dict[str, Any]],
    decision_rows: list[dict[str, Any]],
    *,
    evidence_path: str | Path = "output/manual_review_evidence_pack.csv",
    decision_path: str | Path = "output/manual_review_decision_template.csv",
) -> tuple[Path, Path]:
    evidence = Path(evidence_path)
    decision = Path(decision_path)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    decision.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(evidence_rows, columns=EVIDENCE_COLUMNS).to_csv(evidence, index=False, encoding="utf-8-sig")
    pd.DataFrame(decision_rows, columns=DECISION_TEMPLATE_COLUMNS).to_csv(decision, index=False, encoding="utf-8-sig")
    return evidence, decision


def build_and_write_manual_review_evidence(
    *,
    project_root: str | Path = ".",
    output_dir: str | Path = "output",
    symbols: list[str] | None = None,
) -> tuple[Path, Path, list[dict[str, Any]], list[dict[str, Any]]]:
    root = Path(project_root)
    evidence_rows, decision_rows = build_manual_review_evidence_pack(
        project_root=root,
        output_dir=output_dir,
        symbols=symbols,
    )
    output = root / output_dir
    evidence_path, decision_path = write_manual_review_evidence_outputs(
        evidence_rows,
        decision_rows,
        evidence_path=output / "manual_review_evidence_pack.csv",
        decision_path=output / "manual_review_decision_template.csv",
    )
    return evidence_path, decision_path, evidence_rows, decision_rows
