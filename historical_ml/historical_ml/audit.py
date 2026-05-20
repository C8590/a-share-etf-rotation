from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import HistoricalMLConfig
from .validators import (
    assert_no_replay_label_columns,
    assert_signal_execution_separation,
    assert_source_is_historical_replay,
)


REPLAY_TABLES = [
    "daily_etf_samples",
    "daily_sector_samples",
    "daily_decision_snapshot",
    "entry_candidate_samples_unlabeled",
]


def validate_replay_outputs(outputs: dict[str, pd.DataFrame]) -> None:
    for table in REPLAY_TABLES:
        df = outputs.get(table)
        if df is None:
            continue
        assert_no_replay_label_columns(df, table)
        assert_source_is_historical_replay(df, table)
    candidates = outputs.get("entry_candidate_samples_unlabeled")
    if candidates is not None and not candidates.empty:
        assert_signal_execution_separation(candidates)


def generate_replay_audit_report(
    outputs: dict[str, pd.DataFrame],
    labeled_samples: pd.DataFrame,
    out_path: str | Path | None = None,
    config: HistoricalMLConfig = HistoricalMLConfig(),
) -> str:
    validate_replay_outputs(outputs)

    etf = outputs.get("daily_etf_samples", pd.DataFrame()).copy()
    sector = outputs.get("daily_sector_samples", pd.DataFrame()).copy()
    snapshot = outputs.get("daily_decision_snapshot", pd.DataFrame()).copy()
    candidates = outputs.get("entry_candidate_samples_unlabeled", pd.DataFrame()).copy()
    labeled = labeled_samples.copy()

    lines: list[str] = []
    lines.append("# replay_audit_report")
    lines.append("")
    lines.append("## Replay Window")
    lines.append("")
    lines.append(f"- replay_start: {config.replay_start}")
    lines.append(f"- replay_end: {config.replay_end}")
    lines.append(f"- actual_trading_days: {_trading_day_count(snapshot, etf, candidates)}")
    lines.append("")

    lines.append("## Row Count Distributions")
    lines.append("")
    lines.append("### daily_etf_samples rows per day")
    lines.append(_distribution_markdown(etf, "trade_date"))
    lines.append("")
    lines.append("### entry_candidate_samples rows per day")
    lines.append(_distribution_markdown(candidates, "trade_date"))
    lines.append("")

    lines.append("## Missing Required Values")
    lines.append("")
    missing = _missing_counts(etf, ["trade_date", "code", "name", "sector", "close"])
    lines.append(_dict_table(missing, key_name="field", value_name="missing_count"))
    lines.append("")

    lines.append("## Entry Decision Counts")
    lines.append("")
    decision_counts = {
        "was_candidate": _bool_sum(candidates, "was_candidate"),
        "was_selected": _bool_sum(candidates, "was_selected"),
        "was_bought": _bool_sum(candidates, "was_bought"),
    }
    lines.append(_dict_table(decision_counts, key_name="field", value_name="true_count"))
    lines.append("")

    lines.append("## Replay Guards")
    lines.append("")
    lines.append(f"- source_all_historical_replay: {_source_ok(outputs)}")
    lines.append(f"- unlabeled_has_no_future_labels: {not _future_label_columns(candidates)}")
    lines.append(f"- execution_date_strictly_after_signal_date: {_execution_separation_ok(candidates)}")
    lines.append("")

    lines.append("## Label Status")
    lines.append("")
    lines.append(_value_counts_markdown(labeled, "label_status"))
    lines.append("")

    lines.append("## Last 20 Trading Days")
    lines.append("")
    last20 = _last_20_label_audit(labeled)
    lines.append(_dict_table(last20, key_name="metric", value_name="value"))
    lines.append("")

    report = "\n".join(lines)
    if out_path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
    return report


def _trading_day_count(*frames: pd.DataFrame) -> int:
    dates: set[pd.Timestamp] = set()
    for df in frames:
        if "trade_date" in df.columns:
            dates.update(pd.to_datetime(df["trade_date"], errors="coerce").dropna().dt.normalize().tolist())
    return len(dates)


def _distribution_markdown(df: pd.DataFrame, date_col: str) -> str:
    if df.empty or date_col not in df.columns:
        return "No rows."
    counts = df.groupby(pd.to_datetime(df[date_col], errors="coerce").dt.normalize()).size()
    summary = pd.DataFrame(
        [
            {
                "days": int(counts.count()),
                "min": int(counts.min()),
                "p25": float(counts.quantile(0.25)),
                "median": float(counts.median()),
                "p75": float(counts.quantile(0.75)),
                "max": int(counts.max()),
                "total_rows": int(counts.sum()),
            }
        ]
    )
    return summary.to_markdown(index=False)


def _missing_counts(df: pd.DataFrame, columns: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for col in columns:
        if col not in df.columns:
            counts[col] = len(df)
            continue
        s = df[col]
        counts[col] = int(s.isna().sum() + (s.astype(str).str.strip() == "").sum())
    return counts


def _bool_sum(df: pd.DataFrame, col: str) -> int:
    if df.empty or col not in df.columns:
        return 0
    return int(df[col].fillna(False).astype(bool).sum())


def _source_ok(outputs: dict[str, pd.DataFrame]) -> bool:
    for table in REPLAY_TABLES:
        df = outputs.get(table)
        if df is None or df.empty:
            continue
        if set(df["source"].dropna().astype(str).unique()) != {"historical_replay"}:
            return False
    return True


def _future_label_columns(df: pd.DataFrame) -> list[str]:
    return sorted(
        c
        for c in df.columns
        if c.startswith("future_return_") or c in {"auto_label", "label_status"}
    )


def _execution_separation_ok(df: pd.DataFrame) -> bool:
    if df.empty:
        return True
    sig = pd.to_datetime(df.get("signal_date"), errors="coerce")
    exe = pd.to_datetime(df.get("execution_date"), errors="coerce")
    return bool((exe.isna() | sig.isna() | (exe > sig)).all())


def _value_counts_markdown(df: pd.DataFrame, col: str) -> str:
    if df.empty or col not in df.columns:
        return "No rows."
    counts = df[col].fillna("<missing>").astype(str).value_counts().rename_axis(col).reset_index(name="count")
    return counts.to_markdown(index=False)


def _last_20_label_audit(labeled: pd.DataFrame) -> dict[str, object]:
    if labeled.empty or "trade_date" not in labeled.columns:
        return {
            "last20_rows": 0,
            "insufficient_future_data_rows": 0,
            "misclassified_good_or_bad_rows": 0,
            "all_last20_insufficient_or_unlabeled": True,
        }
    dates = sorted(pd.to_datetime(labeled["trade_date"], errors="coerce").dropna().dt.normalize().unique())
    last_dates = set(dates[-20:])
    sub = labeled.loc[pd.to_datetime(labeled["trade_date"], errors="coerce").dt.normalize().isin(last_dates)]
    status = sub.get("label_status", pd.Series(dtype=str)).fillna("").astype(str)
    auto = sub.get("auto_label", pd.Series(dtype=str)).fillna("").astype(str)
    insufficient = status == "insufficient_future_data"
    bad_auto = auto.isin(["good_entry", "bad_entry"])
    return {
        "last20_rows": int(len(sub)),
        "insufficient_future_data_rows": int(insufficient.sum()),
        "misclassified_good_or_bad_rows": int((insufficient & bad_auto).sum()),
        "all_last20_insufficient_or_unlabeled": bool((insufficient | (auto == "unlabeled")).all()) if len(sub) else True,
    }


def _dict_table(values: dict[str, object], key_name: str, value_name: str) -> str:
    if not values:
        return "No rows."
    return pd.DataFrame([{key_name: k, value_name: v} for k, v in values.items()]).to_markdown(index=False)
