from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import HistoricalMLConfig
from .io_utils import write_table
from .schemas import CALIBRATION_SUGGESTION_COLUMNS


def generate_entry_calibration_outputs(
    labeled_samples: pd.DataFrame,
    out_dir: str | Path,
    config: HistoricalMLConfig = HistoricalMLConfig(),
) -> tuple[pd.DataFrame, str]:
    suggestions, report = build_entry_calibration(labeled_samples, config=config)
    write_table(suggestions, out_dir, "entry_calibration_suggestions", config.output_format)
    path = Path(out_dir) / "entry_calibration_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    return suggestions, report


def build_entry_calibration(
    labeled_samples: pd.DataFrame,
    config: HistoricalMLConfig = HistoricalMLConfig(),
) -> tuple[pd.DataFrame, str]:
    df = _prepare_labeled(labeled_samples)
    complete = df.loc[df["auto_label"].isin(["good_entry", "bad_entry", "neutral_entry"])].copy()
    suggestions: list[dict[str, Any]] = []

    if not complete.empty:
        suggestions.extend(_momentum_suggestions(complete, config))
        suggestions.extend(_acceleration_suggestions(complete, config))
        suggestions.extend(_trend_maturity_suggestions(complete, config))
        suggestions.extend(_rank_suggestions(complete, "sector_rank", "sector_rank", config))
        suggestions.extend(_rank_suggestions(complete, "etf_rank", "etf_rank", config))
        suggestions.extend(_market_state_suggestions(complete, config))
        suggestions.extend(_selected_bad_suggestions(complete, config))
        suggestions.extend(_bought_bad_suggestions(complete, config))
        suggestions.extend(_missed_winner_suggestions(complete, config))
        suggestions.extend(_score_stability_suggestions(complete, config))

    suggestions_df = pd.DataFrame(suggestions, columns=CALIBRATION_SUGGESTION_COLUMNS)
    if suggestions_df.empty:
        suggestions_df = pd.DataFrame(columns=CALIBRATION_SUGGESTION_COLUMNS)
    else:
        suggestions_df["suggestion_id"] = [f"CAL-{i:03d}" for i in range(1, len(suggestions_df) + 1)]
        suggestions_df = suggestions_df[CALIBRATION_SUGGESTION_COLUMNS]

    report = _build_report(complete, suggestions_df, config)
    return suggestions_df, report


def _prepare_labeled(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["trade_date", "signal_date", "execution_date"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
    for col in [
        "momentum_score",
        "acceleration_score",
        "entry_score",
        "trend_maturity",
        "sector_rank",
        "etf_rank",
        "future_return_10d",
        "future_max_drawdown_10d",
    ]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        else:
            out[col] = np.nan
    for col in ["was_candidate", "was_selected", "was_bought"]:
        out[col] = _bool_series(out[col]) if col in out.columns else False
    if "auto_label" not in out.columns:
        out["auto_label"] = "unlabeled"
    if "market_state" not in out.columns:
        out["market_state"] = "unknown"
    if "sector_state" not in out.columns:
        out["sector_state"] = "unknown"
    if "exclude_reason" not in out.columns:
        out["exclude_reason"] = ""
    for col in ["outperform_market_10d", "outperform_sector_10d"]:
        out[col] = _bool_series(out[col]) if col in out.columns else False
    out["trend_stage"] = out["trend_maturity"].map(_trend_stage)
    out["sector_rank_group"] = out["sector_rank"].map(_rank_group)
    out["etf_rank_group"] = out["etf_rank"].map(_rank_group)
    return out


def _bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.fillna(False)
    return s.fillna(False).map(lambda v: str(v).strip().lower() in {"1", "true", "yes", "y", "selected"})


def _trend_stage(value: float) -> str:
    if pd.isna(value):
        return "unknown"
    if value <= 0.25:
        return "startup"
    if value <= 0.50:
        return "confirmation"
    if value <= 0.75:
        return "main_uptrend"
    return "overheat"


def _rank_group(value: float) -> str:
    if pd.isna(value):
        return "unknown"
    rank = int(value)
    if rank in {1, 2, 3}:
        return str(rank)
    return ">3"


def _metrics(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "sample_count": 0,
            "good_rate": 0.0,
            "bad_rate": 0.0,
            "avg_future_return_10d": np.nan,
            "max_drawdown_warning": np.nan,
        }
    return {
        "sample_count": int(len(df)),
        "good_rate": float((df["auto_label"] == "good_entry").mean()),
        "bad_rate": float((df["auto_label"] == "bad_entry").mean()),
        "avg_future_return_10d": float(df["future_return_10d"].mean()),
        "max_drawdown_warning": float(df["future_max_drawdown_10d"].min()),
    }


def _suggestion(
    parameter_area: str,
    current_pattern: str,
    evidence_metric: str,
    evidence_value: Any,
    suggested_action: str,
    metrics: dict[str, Any],
    confidence: str,
    affected_market_state: str = "all",
    affected_sector_state: str = "all",
    notes: str = "",
) -> dict[str, Any]:
    return {
        "suggestion_id": "",
        "parameter_area": parameter_area,
        "current_pattern": current_pattern,
        "evidence_metric": evidence_metric,
        "evidence_value": evidence_value,
        "suggested_action": suggested_action,
        "confidence": confidence,
        "affected_market_state": affected_market_state,
        "affected_sector_state": affected_sector_state,
        "sample_count": metrics["sample_count"],
        "good_rate": metrics["good_rate"],
        "bad_rate": metrics["bad_rate"],
        "avg_future_return_10d": metrics["avg_future_return_10d"],
        "max_drawdown_warning": metrics["max_drawdown_warning"],
        "notes": notes,
    }


def _min_support(config: HistoricalMLConfig) -> int:
    return max(1, min(config.min_group_size_for_report, 10))


def _confidence(sample_count: int, delta: float, config: HistoricalMLConfig, evidence: pd.DataFrame | None = None) -> str:
    if sample_count >= 30 and delta >= 0.15 and not _is_concentrated(evidence):
        return "high"
    if sample_count >= _min_support(config) and delta >= 0.08:
        return "medium"
    return "low"


def _is_concentrated(df: pd.DataFrame | None, threshold: float = 0.50) -> bool:
    if df is None or df.empty:
        return True
    for col in ["code", "sector"]:
        if col not in df.columns:
            continue
        counts = df[col].fillna("<missing>").astype(str).value_counts(normalize=True)
        if len(counts) <= 1 or float(counts.iloc[0]) > threshold:
            return True
    return False


def _concentration_warning(df: pd.DataFrame, threshold: float = 0.50) -> str:
    if df.empty:
        return ""
    warnings: list[str] = []
    for col in ["code", "sector"]:
        if col not in df.columns:
            continue
        counts = df[col].fillna("<missing>").astype(str).value_counts()
        if counts.empty:
            continue
        top_value = counts.index[0]
        top_count = int(counts.iloc[0])
        share = top_count / len(df)
        if len(counts) <= 1 or share > threshold:
            warnings.append(f"concentration warning: top_{col}={top_value} share={share:.1%}")
    return " | ".join(warnings)


def _append_notes(*parts: str) -> str:
    return " | ".join(part for part in parts if part)


def _momentum_suggestions(df: pd.DataFrame, config: HistoricalMLConfig) -> list[dict[str, Any]]:
    if df["momentum_score"].dropna().nunique() < 2:
        return []
    out: list[dict[str, Any]] = []
    low_cut = df["momentum_score"].quantile(0.25)
    low = df.loc[df["momentum_score"] <= low_cut]
    overall_bad = float((df["auto_label"] == "bad_entry").mean())
    m = _metrics(low)
    delta = m["bad_rate"] - overall_bad
    if m["sample_count"] >= _min_support(config) and delta > 0.05:
        out.append(
            _suggestion(
                "momentum_score",
                f"lowest quartile <= {low_cut:.4f} has elevated bad_rate",
                "bad_rate_delta_vs_overall",
                round(delta, 4),
                "raise minimum momentum threshold or down-rank low momentum entries",
                m,
                _confidence(m["sample_count"], delta, config, low),
                notes=_append_notes(
                    "Evidence answers whether momentum_score threshold is too low.",
                    _concentration_warning(low),
                ),
            )
        )
    return out


def _acceleration_suggestions(df: pd.DataFrame, config: HistoricalMLConfig) -> list[dict[str, Any]]:
    if df["acceleration_score"].dropna().nunique() < 2:
        return []
    high_cut = df["acceleration_score"].quantile(0.80)
    high = df.loc[df["acceleration_score"] >= high_cut]
    bad_pullback = high.loc[
        (high["auto_label"] == "bad_entry")
        | (high["future_return_10d"] <= config.bad_return_10d)
        | (high["future_max_drawdown_10d"] <= config.bad_drawdown_10d)
    ]
    m = _metrics(high)
    bad_share = 0.0 if high.empty else len(bad_pullback) / len(high)
    if m["sample_count"] >= _min_support(config) and bad_share >= 0.35:
        return [
            _suggestion(
                "acceleration_score",
                f"top acceleration quintile >= {high_cut:.4f} often fails after entry",
                "high_acceleration_failure_share",
                round(bad_share, 4),
                "lower acceleration weight or require stronger momentum confirmation",
                m,
                _confidence(m["sample_count"], bad_share - 0.30, config, bad_pullback),
                notes=_append_notes(
                    "High acceleration with poor forward return or deep drawdown suggests chase failure.",
                    _concentration_warning(bad_pullback),
                ),
            )
        ]
    return []


def _trend_maturity_suggestions(df: pd.DataFrame, config: HistoricalMLConfig) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    table = _group_metrics(df, "trend_stage")
    if table.empty or "overheat" not in set(table["trend_stage"]):
        return out
    overheat = table.loc[table["trend_stage"] == "overheat"].iloc[0]
    baseline = table.loc[table["trend_stage"] != "overheat"]
    baseline_bad = float(np.average(baseline["bad_rate"], weights=baseline["sample_count"])) if not baseline.empty else 0.0
    delta = float(overheat["bad_rate"] - baseline_bad)
    if int(overheat["sample_count"]) >= _min_support(config) and delta > 0.05:
        out.append(
            _suggestion(
                "trend_maturity",
                "overheat stage has elevated bad_rate",
                "overheat_bad_rate_delta",
                round(delta, 4),
                "increase overheat penalty, reduce weight, or require pullback confirmation",
                overheat.to_dict(),
                _confidence(int(overheat["sample_count"]), delta, config, df.loc[df["trend_stage"] == "overheat"]),
                notes=_append_notes(
                    "Trend maturity should filter chase-high risk.",
                    _concentration_warning(df.loc[df["trend_stage"] == "overheat"]),
                ),
            )
        )
    return out


def _rank_suggestions(df: pd.DataFrame, rank_col: str, area: str, config: HistoricalMLConfig) -> list[dict[str, Any]]:
    group_col = f"{rank_col}_group"
    if group_col not in df.columns:
        return []
    table = _group_metrics(df, group_col)
    weak = table.loc[table[group_col] == ">3"]
    top = table.loc[table[group_col].isin(["1", "2", "3"])]
    if weak.empty or top.empty:
        return []
    weak_row = weak.iloc[0]
    top_bad = float(np.average(top["bad_rate"], weights=top["sample_count"]))
    delta = float(weak_row["bad_rate"] - top_bad)
    if int(weak_row["sample_count"]) >= _min_support(config) and delta > 0.05:
        action = "limit weak sector entries" if rank_col == "sector_rank" else "prefer top-ranked ETF within each sector"
        return [
            _suggestion(
                area,
                f"{rank_col} > 3 underperforms top ranks",
                "bad_rate_delta_vs_top3",
                round(delta, 4),
                action,
                weak_row.to_dict(),
                _confidence(int(weak_row["sample_count"]), delta, config, df.loc[df[group_col] == ">3"]),
                notes=_concentration_warning(df.loc[df[group_col] == ">3"]),
            )
        ]
    return []


def _market_state_suggestions(df: pd.DataFrame, config: HistoricalMLConfig) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    table = _group_metrics(df, "market_state")
    if table.empty:
        return out
    overall_bad = float((df["auto_label"] == "bad_entry").mean())
    for _, row in table.iterrows():
        state = str(row["market_state"])
        delta = float(row["bad_rate"] - overall_bad)
        if int(row["sample_count"]) < _min_support(config) or delta <= 0.05:
            continue
        action = "raise entry threshold or block equity buys in defense" if state in {"defense", "闃插畧"} else "use stricter switch threshold in this market state"
        out.append(
            _suggestion(
                "market_state",
                f"{state} has elevated bad_rate",
                "bad_rate_delta_vs_overall",
                round(delta, 4),
                action,
                row.to_dict(),
                _confidence(int(row["sample_count"]), delta, config, df.loc[df["market_state"].astype(str) == state]),
                affected_market_state=state,
                notes=_concentration_warning(df.loc[df["market_state"].astype(str) == state]),
            )
        )
    return out


def _selected_bad_suggestions(df: pd.DataFrame, config: HistoricalMLConfig) -> list[dict[str, Any]]:
    selected_bad = df.loc[df["was_selected"] & (df["auto_label"] == "bad_entry")]
    selected = df.loc[df["was_selected"]]
    if len(selected_bad) < _min_support(config) or selected.empty:
        return []
    m = _metrics(selected)
    share = len(selected_bad) / len(selected)
    return [
        _suggestion(
            "selected_bad_entry",
            "selected entries include bad_entry cluster",
            "selected_bad_share",
            round(share, 4),
            "review selected bad common factors before changing entry rules",
            m,
            _confidence(len(selected_bad), share, config, selected_bad),
            notes=_append_notes(_commonality_notes(selected_bad), _concentration_warning(selected_bad)),
        )
    ]


def _bought_bad_suggestions(df: pd.DataFrame, config: HistoricalMLConfig) -> list[dict[str, Any]]:
    bought_bad = df.loc[df["was_bought"] & (df["auto_label"] == "bad_entry")]
    bought = df.loc[df["was_bought"]]
    if len(bought_bad) < _min_support(config) or bought.empty:
        return []
    m = _metrics(bought)
    share = len(bought_bad) / len(bought)
    return [
        _suggestion(
            "bought_bad_entry",
            "actual bought entries include bad_entry cluster",
            "bought_bad_share",
            round(share, 4),
            "tighten buy confirmation for the common failure pattern",
            m,
            _confidence(len(bought_bad), share, config, bought_bad),
            notes=_append_notes(_commonality_notes(bought_bad), _concentration_warning(bought_bad)),
        )
    ]


def _missed_winner_suggestions(df: pd.DataFrame, config: HistoricalMLConfig) -> list[dict[str, Any]]:
    missed = _missed_winner_frames(df, config)
    true_missed = missed["true_missed_winner"]
    if len(true_missed) < _min_support(config):
        return []
    m = _metrics(true_missed)
    return [
        _suggestion(
            "missed_winner",
            "unbought samples later rose strongly and outperformed market or sector",
            "true_missed_winner_count",
            len(true_missed),
            "inspect exclude_reason filters before loosening them; ignore raw missed winners that only reflect broad market strength",
            m,
            _confidence(len(true_missed), m["good_rate"], config, true_missed),
            notes=_append_notes(_exclude_reason_notes(true_missed), _concentration_warning(true_missed)),
        )
    ]


def _missed_winner_frames(df: pd.DataFrame, config: HistoricalMLConfig) -> dict[str, pd.DataFrame]:
    raw = df.loc[(~df["was_bought"]) & (df["future_return_10d"] >= config.missed_big_winner_return_10d)].copy()
    market = raw.loc[raw["outperform_market_10d"]].copy()
    sector = raw.loc[raw["outperform_sector_10d"]].copy()
    true_missed = raw.loc[raw["outperform_market_10d"] | raw["outperform_sector_10d"]].copy()
    return {
        "raw_missed_winner": raw,
        "market_outperform_missed_winner": market,
        "sector_outperform_missed_winner": sector,
        "true_missed_winner": true_missed,
    }


def _score_stability_suggestions(df: pd.DataFrame, config: HistoricalMLConfig) -> list[dict[str, Any]]:
    if df.empty or "code" not in df.columns or "trade_date" not in df.columns:
        return []
    tmp = df.sort_values(["code", "trade_date"]).copy()
    tmp["score_change"] = tmp.groupby("code")["entry_score"].diff().abs()
    vol = tmp.groupby("code")["score_change"].mean().dropna()
    out: list[dict[str, Any]] = []
    if not vol.empty:
        high_vol = vol.loc[vol >= vol.quantile(0.80)]
        if len(high_vol) >= 1:
            sub = tmp.loc[tmp["code"].isin(high_vol.index)]
            m = _metrics(sub)
            out.append(
                _suggestion(
                    "score_stability",
                    "entry_score has high day-to-day volatility for some codes",
                    "mean_abs_score_change_top_codes",
                    round(float(high_vol.mean()), 4),
                    "review smoothing or confirmation windows before using score changes as triggers",
                    m,
                    "low",
                    notes=", ".join(map(str, high_vol.sort_values(ascending=False).head(5).index)),
                )
            )

    selected_not_bought = tmp.loc[tmp["was_selected"] & (~tmp["was_bought"])]
    if len(selected_not_bought) >= _min_support(config):
        m = _metrics(selected_not_bought)
        out.append(
            _suggestion(
                "score_stability",
                "consecutive selected-but-not-bought samples need follow-up review",
                "selected_not_bought_count",
                len(selected_not_bought),
                "separate selection evidence from buy confirmation evidence in reports",
                m,
                "medium" if len(selected_not_bought) >= config.min_group_size_for_report else "low",
            )
        )

    tmp["selected_flip"] = tmp.groupby("code")["was_selected"].transform(lambda s: s.astype(int).diff().abs().fillna(0))
    flips = tmp.groupby("code")["selected_flip"].sum()
    frequent = flips.loc[flips >= 3]
    if len(frequent) >= 1:
        sub = tmp.loc[tmp["code"].isin(frequent.index)]
        m = _metrics(sub)
        out.append(
            _suggestion(
                "score_stability",
                "frequent selected/unselected flipping detected",
                "flip_count",
                int(frequent.sum()),
                "consider hysteresis or minimum holdout windows for calibration review",
                m,
                "low",
                notes=", ".join(map(str, frequent.sort_values(ascending=False).head(5).index)),
            )
        )

    high_score_cut = tmp["entry_score"].quantile(0.80)
    stable_fail = tmp.loc[(tmp["entry_score"] >= high_score_cut) & (tmp["auto_label"] == "bad_entry")]
    if len(stable_fail) >= _min_support(config):
        m = _metrics(stable_fail)
        out.append(
            _suggestion(
                "score_stability",
                "high entry_score but poor future return",
                "high_score_bad_count",
                len(stable_fail),
            "audit score formula components before raising exposure",
            m,
            _confidence(len(stable_fail), m["bad_rate"], config, stable_fail),
            notes=_append_notes(_commonality_notes(stable_fail), _concentration_warning(stable_fail)),
            )
        )
    return out


def _group_metrics(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    if df.empty or group_col not in df.columns:
        return pd.DataFrame()
    return df.groupby(group_col, dropna=False, observed=False).apply(lambda g: pd.Series(_metrics(g))).reset_index()


def _commonality_notes(df: pd.DataFrame) -> str:
    parts = []
    for col in ["market_state", "sector_state", "trend_stage", "sector_rank_group", "etf_rank_group"]:
        if col in df.columns and not df[col].dropna().empty:
            counts = df[col].astype(str).value_counts().head(3)
            parts.append(f"{col}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return " | ".join(parts)


def _exclude_reason_notes(df: pd.DataFrame) -> str:
    if "exclude_reason" not in df.columns or df.empty:
        return ""
    counts = df["exclude_reason"].fillna("").astype(str).value_counts().head(5)
    return "exclude_reason: " + ", ".join(f"{k}={v}" for k, v in counts.items())


def _build_report(complete: pd.DataFrame, suggestions: pd.DataFrame, config: HistoricalMLConfig) -> str:
    lines: list[str] = []
    lines.append("# entry_calibration_report")
    lines.append("")
    lines.append("This report provides evidence for entry parameter calibration. It does not change entry rules.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- labeled_samples_used: {len(complete):,}")
    lines.append(f"- suggestions: {len(suggestions):,}")
    lines.append("")

    if complete.empty:
        lines.append("No complete labeled samples available.")
        return "\n".join(lines)

    lines.append("## Structured Suggestions")
    lines.append("")
    display_cols = [
        "suggestion_id",
        "parameter_area",
        "evidence_metric",
        "evidence_value",
        "suggested_action",
        "confidence",
        "sample_count",
        "good_rate",
        "bad_rate",
    ]
    lines.append(suggestions[display_cols].to_markdown(index=False) if not suggestions.empty else "No suggestions met support thresholds.")
    lines.append("")

    lines.extend(_phase35_diagnostics(complete, config))
    lines.append("")

    sections = [
        ("Momentum Score", _bucket_table(complete, "momentum_score", config)),
        ("Acceleration Score", _bucket_table(complete, "acceleration_score", config)),
        ("Trend Maturity Stage", _group_metrics(complete, "trend_stage")),
        ("Sector Rank", _group_metrics(complete, "sector_rank_group")),
        ("ETF Rank", _group_metrics(complete, "etf_rank_group")),
        ("Market State", _group_metrics(complete, "market_state")),
    ]
    for title, table in sections:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(table.to_markdown(index=False) if not table.empty else "No rows.")
        lines.append("")

    lines.append("## Selected Bad Entry Top 20")
    lines.append("")
    lines.append(_top20(complete, complete["was_selected"] & (complete["auto_label"] == "bad_entry"), ascending=True))
    lines.append("")

    lines.append("## Bought Bad Entry Top 20")
    lines.append("")
    lines.append(_top20(complete, complete["was_bought"] & (complete["auto_label"] == "bad_entry"), ascending=True))
    lines.append("")

    lines.append("## Missed Winners Top 20")
    lines.append("")
    lines.append(
        _top20(
            complete,
            (~complete["was_bought"]) & (complete["future_return_10d"] >= config.missed_big_winner_return_10d),
            ascending=False,
        )
    )
    lines.append("")

    lines.append("## Score Stability")
    lines.append("")
    lines.append(_score_stability_markdown(complete))
    return "\n".join(lines)


def _phase35_diagnostics(complete: pd.DataFrame, config: HistoricalMLConfig) -> list[str]:
    lines: list[str] = []
    lines.append("## Phase 3.5 Defensive Diagnostics")
    lines.append("")
    lines.append("This block reduces false calibration conclusions from broad rallies, small samples, and code or sector concentration.")
    lines.append("")
    lines.append("### Benchmark Quality")
    lines.append("")
    lines.append(_benchmark_quality_markdown(complete))
    lines.append("")

    missed = _missed_winner_frames(complete, config)
    lines.append("### Missed Winner Classification")
    lines.append("")
    missed_counts = pd.DataFrame(
        [
            {
                "category": name,
                "count": len(frame),
                "avg_future_return_10d": float(frame["future_return_10d"].mean()) if not frame.empty else np.nan,
                "top_exclude_reason": _top_value(frame, "exclude_reason"),
            }
            for name, frame in missed.items()
        ]
    )
    lines.append(missed_counts.to_markdown(index=False))
    lines.append("")

    for name, frame in missed.items():
        lines.append(f"#### {name} Top 20")
        lines.append(_top20(frame, pd.Series(True, index=frame.index), ascending=False))
        lines.append("")
        lines.append(f"#### {name} exclude_reason")
        lines.append(_value_counts_table(frame, "exclude_reason"))
        lines.append("")

    selected_bad = complete.loc[complete["was_selected"] & (complete["auto_label"] == "bad_entry")].copy()
    bought_bad = complete.loc[complete["was_bought"] & (complete["auto_label"] == "bad_entry")].copy()
    lines.extend(_bad_entry_attribution("selected_bad_entry", selected_bad, config))
    lines.extend(_bad_entry_attribution("bought_bad_entry", bought_bad, config))

    raw_missed = missed["raw_missed_winner"]
    bad = complete.loc[complete["auto_label"] == "bad_entry"].copy()
    concentration_tables = [
        ("bad_entry_top_codes", bad, "code"),
        ("bad_entry_top_sectors", bad, "sector"),
        ("missed_winner_top_codes", raw_missed, "code"),
        ("missed_winner_top_sectors", raw_missed, "sector"),
    ]
    for title, frame, col in concentration_tables:
        lines.append(f"### {title}")
        lines.append("")
        lines.append(_value_counts_table(frame, col))
        warning = _concentration_warning(frame)
        if warning:
            lines.append("")
            lines.append(warning)
        lines.append("")
    return lines


def _benchmark_quality_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "No rows."
    rows: list[dict[str, Any]] = []
    if "outperform_market_10d" in df.columns:
        rows.append(
            {
                "benchmark": "market",
                "true_count": int(df["outperform_market_10d"].astype(bool).sum()),
                "true_rate": float(df["outperform_market_10d"].astype(bool).mean()),
                "warning": "",
            }
        )
    if "outperform_sector_10d" in df.columns:
        sector_true = int(df["outperform_sector_10d"].astype(bool).sum())
        one_member_rate = _single_member_sector_rate(df)
        warning = ""
        if sector_true == 0:
            warning = (
                "sector_outperform has no true rows; current sector benchmark may be limited "
                "when sector keys are too granular or one ETF represents a sector"
            )
        if one_member_rate >= 0.8:
            warning = _append_notes(
                warning,
                f"{one_member_rate:.1%} of sector-date groups have a single ETF",
            )
        rows.append(
            {
                "benchmark": "sector",
                "true_count": sector_true,
                "true_rate": float(df["outperform_sector_10d"].astype(bool).mean()),
                "warning": warning,
            }
        )
    return pd.DataFrame(rows).to_markdown(index=False) if rows else "No benchmark columns found."


def _single_member_sector_rate(df: pd.DataFrame) -> float:
    if not {"trade_date", "sector", "code"}.issubset(df.columns):
        return 0.0
    groups = df.groupby(["trade_date", "sector"])["code"].nunique()
    if groups.empty:
        return 0.0
    return float((groups <= 1).mean())


def _bad_entry_attribution(title: str, bad_df: pd.DataFrame, config: HistoricalMLConfig) -> list[str]:
    lines = [f"### {title} Attribution", ""]
    if bad_df.empty:
        lines.append("No rows.")
        lines.append("")
        return lines

    dims = ["market_state", "sector_state", "trend_stage", "sector_rank_group", "etf_rank_group", "exclude_reason"]
    for dim in dims:
        lines.append(f"#### {dim}")
        lines.append(_attribution_table(bad_df, dim))
        lines.append("")

    for col in ["momentum_score", "acceleration_score"]:
        lines.append(f"#### {col}_bucket")
        lines.append(_bucket_attribution_table(bad_df, col, config))
        lines.append("")

    warning = _concentration_warning(bad_df)
    if warning:
        lines.append(warning)
        lines.append("")
    return lines


def _attribution_table(df: pd.DataFrame, col: str) -> str:
    if df.empty or col not in df.columns:
        return "No rows."
    counts = df[col].fillna("<missing>").astype(str).value_counts().head(20)
    table = counts.rename_axis(col).reset_index(name="bad_count")
    table["share"] = table["bad_count"] / len(df)
    return table.to_markdown(index=False)


def _bucket_attribution_table(df: pd.DataFrame, col: str, config: HistoricalMLConfig) -> str:
    if df.empty or col not in df.columns or df[col].dropna().empty:
        return "No rows."
    tmp = df[[col]].copy()
    tmp[col] = pd.to_numeric(tmp[col], errors="coerce")
    tmp = tmp.dropna(subset=[col])
    if tmp.empty:
        return "No rows."
    if tmp[col].nunique() >= 2:
        try:
            tmp["bucket"] = pd.qcut(tmp[col], q=min(config.report_feature_bins, tmp[col].nunique()), duplicates="drop")
        except ValueError:
            tmp["bucket"] = tmp[col].astype(str)
    else:
        tmp["bucket"] = tmp[col].astype(str)
    counts = tmp["bucket"].astype(str).value_counts().rename_axis("bucket").reset_index(name="bad_count")
    counts["share"] = counts["bad_count"] / len(tmp)
    return counts.to_markdown(index=False)


def _value_counts_table(df: pd.DataFrame, col: str) -> str:
    if df.empty or col not in df.columns:
        return "No rows."
    counts = df[col].fillna("<missing>").astype(str).value_counts().head(20)
    table = counts.rename_axis(col).reset_index(name="count")
    table["share"] = table["count"] / len(df)
    return table.to_markdown(index=False)


def _top_value(df: pd.DataFrame, col: str) -> str:
    if df.empty or col not in df.columns:
        return ""
    counts = df[col].fillna("<missing>").astype(str).value_counts()
    return "" if counts.empty else str(counts.index[0])


def _bucket_table(df: pd.DataFrame, col: str, config: HistoricalMLConfig) -> pd.DataFrame:
    if df.empty or df[col].dropna().empty:
        return pd.DataFrame()
    tmp = df.copy()
    if tmp[col].dropna().nunique() >= 2:
        try:
            tmp["bucket"] = pd.qcut(tmp[col], q=min(config.report_feature_bins, tmp[col].dropna().nunique()), duplicates="drop")
        except ValueError:
            tmp["bucket"] = tmp[col].astype(str)
    else:
        tmp["bucket"] = tmp[col].astype(str)
    table = _group_metrics(tmp, "bucket")
    if not table.empty:
        table["bucket"] = table["bucket"].astype(str)
    return table


def _top20(df: pd.DataFrame, mask: pd.Series, ascending: bool) -> str:
    cols = [
        "trade_date",
        "code",
        "name",
        "sector",
        "market_state",
        "sector_state",
        "momentum_score",
        "acceleration_score",
        "entry_score",
        "trend_stage",
        "sector_rank",
        "etf_rank",
        "future_return_10d",
        "future_max_drawdown_10d",
        "exclude_reason",
    ]
    present = [c for c in cols if c in df.columns]
    sub = df.loc[mask, present].copy()
    if sub.empty:
        return "No rows."
    return sub.sort_values("future_return_10d", ascending=ascending).head(20).to_markdown(index=False)


def _score_stability_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "No rows."
    tmp = df.sort_values(["code", "trade_date"]).copy()
    tmp["score_change"] = tmp.groupby("code")["entry_score"].diff().abs()
    score = tmp.groupby("code").agg(
        sample_count=("entry_score", "count"),
        avg_abs_score_change=("score_change", "mean"),
        selected_days=("was_selected", "sum"),
        bought_days=("was_bought", "sum"),
        selected_flips=("was_selected", lambda s: int(s.astype(int).diff().abs().fillna(0).sum())),
        bad_high_score_days=("auto_label", lambda s: int((s == "bad_entry").sum())),
    ).reset_index()
    return score.sort_values(["selected_flips", "avg_abs_score_change"], ascending=False).head(20).to_markdown(index=False)
