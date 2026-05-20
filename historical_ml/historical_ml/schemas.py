"""Column contracts for historical_ml outputs.

The required fields intentionally keep the feature side and future-label side
separate.  Feature tables are generated with trade_date data only; labels are
attached later by FutureLabeler.
"""

PRICE_REQUIRED_COLUMNS = [
    "date",
    "code",
    "name",
    "close",
    "sector",
]

PRICE_OPTIONAL_COLUMNS = [
    "open",
    "high",
    "low",
    "volume",
    "amount",
    "sector_l1",
]

DAILY_ETF_SAMPLE_COLUMNS = [
    "trade_date",
    "code",
    "name",
    "sector",
    "sector_l1",
    "market_state",
    "sector_state",
    "r20",
    "r60",
    "r120",
    "momentum_score",
    "acceleration_score",
    "entry_score",
    "trend_maturity",
    "abs_trend_score",
    "liquidity_score",
    "risk_score",
    "overheat_score",
    "sector_rank",
    "etf_rank",
    "global_rank",
    "data_quality_flag",
    "source",
]

DAILY_SECTOR_SAMPLE_COLUMNS = [
    "trade_date",
    "sector",
    "sector_l1",
    "market_state",
    "sector_state",
    "sector_rank",
    "sector_momentum_score",
    "sector_acceleration_score",
    "sector_breadth_score",
    "sector_risk_score",
    "sector_entry_success_proxy",
    "candidate_count",
    "source",
]

DAILY_DECISION_SNAPSHOT_COLUMNS = [
    "trade_date",
    "signal_date",
    "execution_date",
    "market_state",
    "etf_count",
    "sector_count",
    "candidate_count",
    "selected_count",
    "bought_count",
    "defense_block_count",
    "filtered_count",
    "data_abnormal_count",
    "source",
]

ENTRY_CANDIDATE_COLUMNS = [
    "trade_date",
    "signal_date",
    "execution_date",
    "code",
    "name",
    "sector",
    "sector_l1",
    "market_state",
    "sector_state",
    "momentum_score",
    "acceleration_score",
    "entry_score",
    "trend_maturity",
    "sector_rank",
    "etf_rank",
    "global_rank",
    "was_candidate",
    "was_selected",
    "was_bought",
    "exclude_reason",
    "source",
]

FUTURE_LABEL_COLUMNS = [
    "label_base_date",
    "future_return_1d",
    "future_return_3d",
    "future_return_5d",
    "future_return_10d",
    "future_return_20d",
    "future_max_gain_10d",
    "future_max_drawdown_10d",
    "market_return_10d",
    "sector_return_10d",
    "outperform_market_10d",
    "outperform_sector_10d",
    "exit_within_3d",
    "auto_label",
    "label_status",
]

REPLAY_FORBIDDEN_LABEL_COLUMNS = FUTURE_LABEL_COLUMNS.copy()

REVIEW_QUEUE_COLUMNS = [
    "review_reason",
    "review_priority",
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
    "exclude_reason",
    "future_return_3d",
    "future_return_10d",
    "future_max_drawdown_10d",
    "auto_label",
    "source",
]

OUTPUT_TABLES = [
    "daily_etf_samples",
    "daily_sector_samples",
    "daily_decision_snapshot",
    "entry_candidate_samples",
    "entry_candidate_samples_labeled",
    "manual_review_queue",
    "entry_calibration_suggestions",
]

CALIBRATION_SUGGESTION_COLUMNS = [
    "suggestion_id",
    "parameter_area",
    "current_pattern",
    "evidence_metric",
    "evidence_value",
    "suggested_action",
    "confidence",
    "affected_market_state",
    "affected_sector_state",
    "sample_count",
    "good_rate",
    "bad_rate",
    "avg_future_return_10d",
    "max_drawdown_warning",
    "notes",
]
