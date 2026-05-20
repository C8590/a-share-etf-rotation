from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import HistoricalMLConfig


def _rate_table(df: pd.DataFrame, group_col: str, config: HistoricalMLConfig) -> pd.DataFrame:
    if df.empty or group_col not in df.columns:
        return pd.DataFrame()
    g = df.groupby(group_col, dropna=False, observed=False).agg(
        sample_count=("code", "count"),
        good_rate=("auto_label", lambda s: float((s == "good_entry").mean())),
        bad_rate=("auto_label", lambda s: float((s == "bad_entry").mean())),
        avg_return_10d=("future_return_10d", "mean"),
        avg_drawdown_10d=("future_max_drawdown_10d", "mean"),
        bought_rate=("was_bought", "mean"),
    )
    g = g.reset_index()
    return g.loc[g["sample_count"] >= config.min_group_size_for_report].sort_values("good_rate", ascending=False)


def _add_quantile_bin(df: pd.DataFrame, col: str, bins: int) -> str | None:
    if col not in df.columns or df[col].dropna().nunique() < 2:
        return None
    bin_col = f"{col}_bin"
    try:
        df[bin_col] = pd.qcut(df[col], q=bins, duplicates="drop")
    except ValueError:
        return None
    return bin_col


def generate_entry_threshold_report(
    labeled_samples: pd.DataFrame,
    out_path: str | Path | None = None,
    config: HistoricalMLConfig = HistoricalMLConfig(),
) -> str:
    """Generate a markdown calibration report for entry features."""

    df = labeled_samples.copy()
    if df.empty:
        report = "# entry_threshold_report\n\nNo labeled samples available.\n"
        if out_path:
            Path(out_path).write_text(report, encoding="utf-8")
        return report

    complete = df.loc[df.get("auto_label", "") != "unlabeled"].copy()
    candidate = complete.loc[complete.get("was_candidate", False).astype(bool)].copy()
    bought = complete.loc[complete.get("was_bought", False).astype(bool)].copy()

    lines: list[str] = []
    lines.append("# entry_threshold_report")
    lines.append("")
    lines.append("## 1. 样本覆盖")
    lines.append("")
    lines.append(f"- 全部样本数：{len(df):,}")
    lines.append(f"- 可完整打标样本数：{len(complete):,}")
    lines.append(f"- entry 候选样本数：{len(candidate):,}")
    lines.append(f"- was_bought 样本数：{len(bought):,}")
    lines.append(f"- good_entry：{int((complete['auto_label'] == 'good_entry').sum()):,}")
    lines.append(f"- bad_entry：{int((complete['auto_label'] == 'bad_entry').sum()):,}")
    lines.append("")

    if complete.empty:
        lines.append("当前没有完整未来窗口标签；无法判断阈值。请使用拥有至少 20 个未来交易日的数据，或缩短回放结束日。")
        report = "\n".join(lines)
        if out_path:
            Path(out_path).write_text(report, encoding="utf-8")
        return report

    # Feature buckets.
    bucket_cols = ["market_state", "sector_state", "sector_rank", "etf_rank"]
    for col in ["momentum_score", "acceleration_score", "entry_score", "trend_maturity"]:
        b = _add_quantile_bin(complete, col, config.report_feature_bins)
        if b:
            bucket_cols.append(b)

    lines.append("## 2. 哪些 entry 特征成功率高？")
    lines.append("")
    for col in bucket_cols:
        tbl = _rate_table(complete, col, config)
        if tbl.empty:
            continue
        lines.append(f"### {col}")
        lines.append(tbl.head(10).to_markdown(index=False))
        lines.append("")

    lines.append("## 3. 哪些失败最多？")
    lines.append("")
    failure_cols = ["market_state", "sector_state", "exclude_reason"]
    for col in failure_cols:
        if col not in complete.columns:
            continue
        tbl = complete.groupby(col, dropna=False).agg(
            sample_count=("code", "count"),
            bad_count=("auto_label", lambda s: int((s == "bad_entry").sum())),
            bad_rate=("auto_label", lambda s: float((s == "bad_entry").mean())),
            avg_return_10d=("future_return_10d", "mean"),
        ).reset_index().sort_values(["bad_count", "bad_rate"], ascending=False)
        tbl = tbl.loc[tbl["sample_count"] >= max(3, config.min_group_size_for_report // 2)]
        lines.append(f"### {col}")
        lines.append(tbl.head(10).to_markdown(index=False) if not tbl.empty else "样本不足。")
        lines.append("")

    lines.extend(_build_phase2_diagnostics(complete, config))
    lines.append("")

    lines.append("## 5. 阈值与参数建议")
    lines.append("")
    lines.extend(_build_recommendations(complete, config))
    lines.append("")

    lines.append("## 6. 风险提示")
    lines.append("")
    lines.append("- historical_ml 只产样本、标签、统计结论和参数建议，不直接修改 entry 规则。")
    lines.append("- 未来表现标签只在 label 阶段使用，不能回流到 replay 特征生成。")
    lines.append("- `label_status=insufficient_future_data` 的样本不能用于 20 日标签结论。")

    report = "\n".join(lines)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(report, encoding="utf-8")
    return report


def _build_phase2_diagnostics(complete: pd.DataFrame, config: HistoricalMLConfig) -> list[str]:
    lines: list[str] = []
    lines.append("## 4. Phase 2 Quality Diagnostics")
    lines.append("")

    for col in ["market_state", "sector_state"]:
        lines.append(f"### {col} x auto_label")
        lines.append(_cross_tab_markdown(complete, col, "auto_label"))
        lines.append("")

    for col in ["momentum_score", "acceleration_score", "trend_maturity", "sector_rank", "etf_rank"]:
        lines.append(f"### {col} bucket success rate")
        lines.append(_bucket_success_markdown(complete, col, config))
        lines.append("")

    lines.append("### was_selected=True and bad_entry Top 20")
    lines.append(_top_samples_markdown(complete, complete["was_selected"].astype(bool) & (complete["auto_label"] == "bad_entry")))
    lines.append("")

    lines.append("### was_bought=True and bad_entry Top 20")
    lines.append(_top_samples_markdown(complete, complete["was_bought"].astype(bool) & (complete["auto_label"] == "bad_entry")))
    lines.append("")

    lines.append("### was_candidate=False and large future_return_10d Top 20")
    lines.append(
        _top_samples_markdown(
            complete,
            (~complete["was_candidate"].astype(bool)) & (complete["future_return_10d"] >= config.missed_big_winner_return_10d),
            sort_col="future_return_10d",
            ascending=False,
        )
    )
    return lines


def _cross_tab_markdown(df: pd.DataFrame, row_col: str, label_col: str) -> str:
    if df.empty or row_col not in df.columns or label_col not in df.columns:
        return "No rows."
    table = pd.crosstab(df[row_col].fillna("<missing>"), df[label_col].fillna("<missing>"))
    if table.empty:
        return "No rows."
    table["total"] = table.sum(axis=1)
    return table.reset_index().to_markdown(index=False)


def _bucket_success_markdown(df: pd.DataFrame, col: str, config: HistoricalMLConfig) -> str:
    if df.empty or col not in df.columns:
        return "No rows."
    required = ["auto_label", "future_return_10d", "was_bought"]
    if any(c not in df.columns for c in required):
        return "No rows."
    tmp = df[[col, *required]].copy()
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

    table = tmp.groupby("bucket", observed=False).agg(
        sample_count=("auto_label", "count"),
        good_count=("auto_label", lambda s: int((s == "good_entry").sum())),
        bad_count=("auto_label", lambda s: int((s == "bad_entry").sum())),
        good_rate=("auto_label", lambda s: float((s == "good_entry").mean())),
        bad_rate=("auto_label", lambda s: float((s == "bad_entry").mean())),
        avg_return_10d=("future_return_10d", "mean"),
        bought_rate=("was_bought", "mean"),
    ).reset_index()
    table["bucket"] = table["bucket"].astype(str)
    return table.to_markdown(index=False)


def _top_samples_markdown(
    df: pd.DataFrame,
    mask: pd.Series,
    sort_col: str = "future_return_10d",
    ascending: bool = True,
) -> str:
    if df.empty:
        return "No rows."
    cols = [
        "trade_date",
        "execution_date",
        "code",
        "name",
        "sector",
        "market_state",
        "sector_state",
        "momentum_score",
        "acceleration_score",
        "entry_score",
        "trend_maturity",
        "sector_rank",
        "etf_rank",
        "was_candidate",
        "was_selected",
        "was_bought",
        "future_return_10d",
        "future_max_drawdown_10d",
        "exclude_reason",
    ]
    present = [c for c in cols if c in df.columns]
    sub = df.loc[mask, present].copy()
    if sub.empty:
        return "No rows."
    if sort_col in sub.columns:
        sub = sub.sort_values(sort_col, ascending=ascending)
    return sub.head(20).to_markdown(index=False)


def _build_recommendations(complete: pd.DataFrame, config: HistoricalMLConfig) -> list[str]:
    lines = []
    q = {}
    for col in ["momentum_score", "acceleration_score", "entry_score", "trend_maturity"]:
        if col in complete.columns and complete[col].dropna().nunique() >= 2:
            q[col] = complete[col].quantile([0.2, 0.4, 0.6, 0.8]).to_dict()

    def metric(mask):
        sub = complete.loc[mask]
        if len(sub) < config.min_group_size_for_report:
            return None
        return {
            "n": len(sub),
            "good": float((sub["auto_label"] == "good_entry").mean()),
            "bad": float((sub["auto_label"] == "bad_entry").mean()),
            "ret": float(sub["future_return_10d"].mean()),
        }

    # Momentum threshold.
    if "momentum_score" in q:
        high_m = metric(complete["momentum_score"] >= q["momentum_score"].get(0.6))
        low_m = metric(complete["momentum_score"] <= q["momentum_score"].get(0.4))
        if high_m and low_m:
            lines.append(
                f"- 动量分阈值：高动量组 good_rate={high_m['good']:.2%}，低动量组 good_rate={low_m['good']:.2%}。"
                + ("建议保留或提高动量门槛。" if high_m["good"] > low_m["good"] else "当前动量门槛区分度不足，建议降低权重或加入绝对趋势过滤。")
            )

    # Acceleration weight.
    if "acceleration_score" in q and "momentum_score" in q:
        high_a_low_m = metric((complete["acceleration_score"] >= q["acceleration_score"].get(0.8)) & (complete["momentum_score"] <= q["momentum_score"].get(0.4)))
        if high_a_low_m:
            verdict = "加速度权重可能过高，避免低动量但短期冲刺样本被过度提分。" if high_a_low_m["bad"] > high_a_low_m["good"] else "加速度对低动量修复样本有正向贡献，可继续观察。"
            lines.append(f"- 加速度权重：高加速度但低动量组 n={high_a_low_m['n']}，good_rate={high_a_low_m['good']:.2%}，bad_rate={high_a_low_m['bad']:.2%}。{verdict}")

    # Trend maturity.
    if "trend_maturity" in q:
        mature = metric(complete["trend_maturity"] >= q["trend_maturity"].get(0.8))
        early = metric(complete["trend_maturity"] <= q["trend_maturity"].get(0.4))
        if mature and early:
            verdict = "趋势成熟度能过滤追高，建议对高成熟度样本扣分或提高确认门槛。" if mature["bad"] > early["bad"] else "高成熟度未显著导致失败，可降低其惩罚。"
            lines.append(f"- 趋势成熟度：高成熟组 bad_rate={mature['bad']:.2%}，较早期组 bad_rate={early['bad']:.2%}。{verdict}")

    # Sector crowding.
    if {"trade_date", "sector"}.issubset(complete.columns):
        crowd = complete.groupby(["trade_date", "sector"]).size().rename("sector_sample_count").reset_index()
        tmp = complete.merge(crowd, on=["trade_date", "sector"], how="left")
        crowded = metric(tmp["sector_sample_count"] >= 3)
        uncrowded = metric(tmp["sector_sample_count"] < 3)
        if crowded and uncrowded:
            verdict = "板块拥挤可能导致失败，建议限制同板块候选数量或提高同板块第二只门槛。" if crowded["bad"] > uncrowded["bad"] else "当前没有证据显示板块拥挤显著恶化。"
            lines.append(f"- 板块拥挤：拥挤组 bad_rate={crowded['bad']:.2%}，非拥挤组 bad_rate={uncrowded['bad']:.2%}。{verdict}")

    # Market state.
    if "market_state" in complete.columns:
        state_tbl = _rate_table(complete, "market_state", config)
        if not state_tbl.empty:
            best = state_tbl.iloc[0]
            worst = state_tbl.sort_values("bad_rate", ascending=False).iloc[0]
            lines.append(f"- market_state 参数：{best['market_state']} 的 good_rate 较高；{worst['market_state']} 的 bad_rate 较高。建议按 market_state 分别设置 entry_score 阈值和加速度权重。")

    if not lines:
        lines.append("- 样本量不足，暂不输出参数建议。")
    return lines
