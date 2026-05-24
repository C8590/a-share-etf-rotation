from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .io_utils import read_table, write_table


SUGGESTION_COLUMNS = [
    "suggested_manual_label",
    "suggested_failure_reason",
    "suggested_action",
    "suggested_confidence",
    "suggested_rule_hit",
    "need_human_review",
]
MANUAL_COLUMNS = ["manual_label", "manual_failure_reason", "manual_action", "manual_confidence", "manual_review_note"]
SUMMARY_KEYS = [
    "total_rows",
    "auto_prefilled_rows",
    "high_confidence_rows",
    "medium_confidence_rows",
    "low_confidence_rows",
    "need_human_review_rows",
    "accepted_rows",
    "pending_rows",
    "adopted_failure_rows",
    "adopted_missed_winner_rows",
    "pending_failure_rows",
    "pending_missed_winner_rows",
    "missed_big_winner_total",
    "missed_big_winner_high_confidence",
    "missed_big_winner_medium_confidence",
    "missed_big_winner_low_confidence",
    "missed_big_winner_need_review",
    "missed_big_winner_accepted",
    "missed_big_winner_pending",
]


def generate_manual_label_suggestions_from_file(input_path: str | Path, out_dir: str | Path, output_name: str = "manual_review_queue_prefilled") -> tuple[pd.DataFrame, dict[str, Any]]:
    review = read_table(input_path)
    prefilled, summary = suggest_manual_labels(review)
    output_path = write_table(prefilled, out_dir, output_name, "csv")
    summary["output_path"] = str(output_path)
    return prefilled, summary


def suggest_manual_labels(review_queue: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = review_queue.copy()
    for col in MANUAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if df.empty:
        for col in SUGGESTION_COLUMNS:
            df[col] = []
        return df, _summary(df)

    suggestions = [_suggest_row(row) for _, row in df.iterrows()]
    for col in SUGGESTION_COLUMNS:
        df[col] = [item[col] for item in suggestions]
    return df, _summary(df)


def adopt_high_confidence_suggestions(prefilled: pd.DataFrame, min_confidence: str = "high") -> tuple[pd.DataFrame, dict[str, Any]]:
    df = prefilled.copy()
    for col in MANUAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype("object")
    if "suggested_confidence" not in df.columns:
        df["suggested_confidence"] = "low"
    threshold = {"low": 0, "medium": 1, "high": 2}.get(min_confidence, 2)
    rank = df["suggested_confidence"].fillna("").astype(str).map({"low": 0, "medium": 1, "high": 2}).fillna(0)
    adopt = rank >= threshold
    df.loc[adopt, "manual_label"] = df.loc[adopt, "suggested_manual_label"].fillna("")
    df.loc[adopt, "manual_failure_reason"] = df.loc[adopt, "suggested_failure_reason"].fillna("")
    df.loc[adopt, "manual_action"] = df.loc[adopt, "suggested_action"].fillna("")
    df.loc[adopt, "manual_confidence"] = df.loc[adopt, "suggested_confidence"].fillna("")
    df.loc[adopt, "manual_review_note"] = f"auto_adopted_{min_confidence}_confidence"
    summary = _summary(df)
    summary.update(
        {
            "adopted_rows": int(adopt.sum()),
            "final_valid_manual_label_rows": int(df["manual_label"].fillna("").astype(str).str.strip().ne("").sum()),
        }
    )
    return df, summary


def low_confidence_review_rows(prefilled: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = prefilled.copy()
    if "need_human_review" not in df.columns:
        df["need_human_review"] = True
    if "suggested_confidence" not in df.columns:
        df["suggested_confidence"] = "low"
    need = _bool_series(df["need_human_review"]) | df["suggested_confidence"].fillna("").astype(str).eq("low")
    out = df.loc[need].copy()
    summary = _summary(df)
    summary["low_confidence_review_rows"] = int(len(out))
    return out, summary


def pending_review_rows(prefilled: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = prefilled.copy()
    for col in MANUAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    manual = df["manual_label"].fillna("").astype(str).str.strip()
    pending = manual.eq("")
    out = df.loc[pending].copy()
    stats = _summary(df)
    stats.update(
        {
            "pending_rows": int(len(out)),
            "pending_failure_rows": int((pending & _failure_mask(df)).sum()),
            "pending_missed_winner_rows": int((pending & _missed_mask(df)).sum()),
        }
    )
    return out, stats


def _suggest_row(row: pd.Series) -> dict[str, Any]:
    review_reason = _text(row.get("review_reason"))
    auto_label = _text(row.get("auto_label"))
    data_flag = _text(row.get("data_quality_flag", "ok")).lower()
    missing_ratio = _num(row.get("missing_ratio_60d"))
    if data_flag not in {"", "ok", "normal"} or missing_ratio > 0.0:
        return _suggest("data_issue", "数据质量问题", "不纳入训练", "high", "data_quality_rule", False)

    if review_reason in {"large_loss_entry", "quick_failure_entry", "bought_and_knocked_out"} and auto_label == "bad_entry":
        reason, rule = _bad_entry_reason(row, review_reason)
        return _suggest("valid_bad_entry", reason, _bad_entry_action(reason), "high", rule, False)

    if review_reason == "missed_big_winner" and auto_label == "good_entry":
        if _bool(row.get("was_candidate")) and not _bool(row.get("was_bought")):
            return _suggest("valid_missed_opportunity", "entry过于保守 / 阈值过高", "允许小仓试探 / 降低观察转买入门槛", "medium", "missed_winner_candidate_not_bought", False)
        return _suggest("valid_missed_opportunity", "候选池遗漏 / 预选规则过严", "复核预选覆盖和候选池门槛", "medium", "missed_winner_not_candidate", False)

    if review_reason == "missed_big_winner":
        return _suggest("not_actionable_missed_winner", "原始涨幅较高但未被 good_entry 标签确认", "暂不作为漏买规则依据，仅抽样观察", "medium", "raw_missed_winner_not_confirmed", False)

    if review_reason == "top_rank_filtered":
        return _suggest("valid_filtered_sample", "候选排名靠前但被过滤 / 预选规则偏严", "抽查过滤原因，暂不自动放宽规则", "medium", "top_rank_filtered_rule", False)

    if review_reason == "same_sector_skipped":
        return _suggest("valid_same_sector_skip", "同板块约束导致跳过", "复核同板块替换逻辑，暂不自动放宽规则", "medium", "same_sector_skip_rule", False)

    return _suggest("needs_review", "规则未覆盖", "人工复核", "low", "fallback_low_confidence", True)


def _bad_entry_reason(row: pd.Series, review_reason: str) -> tuple[str, str]:
    if review_reason in {"quick_failure_entry", "bought_and_knocked_out"} or _bool(row.get("exit_within_3d")):
        return "假突破 / 买入确认不足", "quick_failure_rule"
    if _num(row.get("overheat_score")) >= 0.25 or _num(row.get("trend_maturity")) >= 0.75:
        return "追高尾段 / 趋势过热", "overheat_rule"
    if _text(row.get("market_state")) != "offense" or _num(row.get("risk_score")) >= 1.0:
        return "市场状态不支持", "market_state_rule"
    if _text(row.get("sector_state")) not in {"strong", "强势"} or _num(row.get("sector_score")) < 0:
        return "板块广度不足", "sector_breadth_rule"
    return "追高尾段 / 假突破", "large_loss_rule"


def _bad_entry_action(reason: str) -> str:
    if "过热" in reason or "追高" in reason:
        return "降级为观察 / 加入过热惩罚"
    if "确认不足" in reason or "假突破" in reason:
        return "延迟确认 / 提高 entry 门槛"
    if "市场" in reason:
        return "限制弱市场状态下的买入"
    if "板块" in reason:
        return "提高板块强度或广度要求"
    return "降级为观察"


def _suggest(label: str, reason: str, action: str, confidence: str, rule_hit: str, need_review: bool) -> dict[str, Any]:
    return {
        "suggested_manual_label": label,
        "suggested_failure_reason": reason,
        "suggested_action": action,
        "suggested_confidence": confidence,
        "suggested_rule_hit": rule_hit,
        "need_human_review": bool(need_review),
    }


def _summary(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or "suggested_manual_label" not in df.columns:
        result = {key: 0 for key in SUMMARY_KEYS}
        result["manual_label_balance_warning"] = ""
        return result
    confidence = df["suggested_confidence"].fillna("").astype(str)
    manual_label = df.get("manual_label", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    accepted = manual_label.ne("")
    failure = _failure_mask(df)
    missed = _missed_mask(df)
    pending = ~accepted
    result = {
        "total_rows": int(len(df)),
        "auto_prefilled_rows": int(df["suggested_manual_label"].fillna("").astype(str).str.strip().ne("").sum()),
        "high_confidence_rows": int(confidence.eq("high").sum()),
        "medium_confidence_rows": int(confidence.eq("medium").sum()),
        "low_confidence_rows": int(confidence.eq("low").sum()),
        "need_human_review_rows": int(_bool_series(df["need_human_review"]).sum()),
        "accepted_rows": int(accepted.sum()),
        "pending_rows": int(pending.sum()),
        "adopted_failure_rows": int((accepted & failure).sum()),
        "adopted_missed_winner_rows": int((accepted & missed).sum()),
        "pending_failure_rows": int((pending & failure).sum()),
        "pending_missed_winner_rows": int((pending & missed).sum()),
        "missed_big_winner_total": int(missed.sum()),
        "missed_big_winner_high_confidence": int((missed & confidence.eq("high")).sum()),
        "missed_big_winner_medium_confidence": int((missed & confidence.eq("medium")).sum()),
        "missed_big_winner_low_confidence": int((missed & confidence.eq("low")).sum()),
        "missed_big_winner_need_review": int((missed & _bool_series(df["need_human_review"])).sum()),
        "missed_big_winner_accepted": int((missed & accepted).sum()),
        "missed_big_winner_pending": int((missed & pending).sum()),
    }
    result["manual_label_balance_warning"] = _balance_warning(result)
    return result


def _failure_mask(df: pd.DataFrame) -> pd.Series:
    if "review_reason" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["review_reason"].fillna("").astype(str).isin({"large_loss_entry", "quick_failure_entry", "bought_and_knocked_out"})


def _missed_mask(df: pd.DataFrame) -> pd.Series:
    if "review_reason" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["review_reason"].fillna("").astype(str).eq("missed_big_winner")


def _balance_warning(summary: dict[str, Any]) -> str:
    if int(summary.get("missed_big_winner_total", 0)) and int(summary.get("adopted_missed_winner_rows", 0)) == 0:
        return "当前人工标注覆盖偏向失败类样本，敢买类样本覆盖不足。"
    return ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(parsed) else float(parsed)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


def _bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.fillna(False)
    return s.fillna(False).map(_bool)
