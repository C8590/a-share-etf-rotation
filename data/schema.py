from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


SCHEMA_VERSION = "1.0"
DATA_SCHEMA_VERSION = "1.0"

PRICE_CACHE_REQUIRED_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "symbol",
    "name",
    "source",
]
PRICE_CACHE_FILENAME_DESCRIPTION = "six-digit ETF files under data/cache, for example 510300.csv"
PRICE_COLUMNS = ["open", "high", "low", "close"]
VOLUME_COLUMNS = ["volume", "amount"]

CACHE_METADATA_REQUIRED_FIELDS = [
    "symbol",
    "name",
    "source",
    "adjust",
    "api_name",
    "endpoint",
    "download_method",
    "fallback_used",
    "fallback_chain",
    "cache_file",
    "downloaded_at",
    "start_date",
    "end_date",
    "row_count",
    "cache_schema_version",
    "data_schema_version",
    "created_by",
]
ADJUST_ALLOWED_VALUES = {"qfq", "hfq", "none", "unknown"}

UNIVERSE_CSV_REQUIRED_COLUMNS = ["symbol", "name", "exchange", "asset_class", "category", "tracking_index"]
UNIVERSE_CSV_OPTIONAL_COLUMNS = [
    "spot_amount",
    "spot_date",
    "spot_updated_at",
    "listing_date",
    "latest_date",
    "avg_amount_20",
    "data_rows",
    "is_active",
    "filter_reason",
    "universe_source",
    "fetched_at",
]
UNIVERSE_YAML_REQUIRED_KEYS = ["default_preset", "presets", "filters", "etfs"]
UNIVERSE_YAML_ETF_REQUIRED_KEYS = ["symbol", "name"]
UNIVERSE_YAML_ETF_OPTIONAL_KEYS = ["exchange", "asset_class", "category", "theme", "sector", "tracking_index"]

TRADING_CALENDAR_REQUIRED_COLUMNS = [
    "date",
    "is_open",
    "exchange",
    "source",
    "calendar_version",
    "generated_at",
    "note",
]
TRADING_CALENDAR_SOURCE_ALLOWED_VALUES = {
    "local_snapshot",
    "akshare.tool_trade_date_hist_sina",
    "akshare_runtime",
    "weekday_fallback",
    "unit-test",
}

INDEX_MAP_REQUIRED_COLUMNS = [
    "symbol",
    "etf_name",
    "category",
    "sub_category",
    "tracking_index_name",
    "tracking_index_code",
    "index_source",
    "mapping_method",
    "confidence",
    "requires_manual_review",
    "usable_as_benchmark",
    "notes",
]
INDEX_MAPPING_METHOD_ALLOWED_VALUES = {"metadata_exact", "config_manual", "name_inferred", "unable_to_confirm"}

INDEX_DATA_COVERAGE_REQUIRED_COLUMNS = [
    "tracking_index_code",
    "tracking_index_name",
    "index_source",
    "api_name",
    "source_family",
    "fetch_success",
    "schema_valid",
    "start_date",
    "end_date",
    "row_count",
    "latest_expected_date",
    "end_date_gap_days",
    "missing_required_columns",
    "missing_values_count",
    "duplicate_dates_count",
    "abnormal_return_count",
    "quality_status",
    "usable_as_benchmark",
    "requires_manual_review",
    "failure_reason",
    "notes",
]
INDEX_DATA_QUALITY_ALLOWED_VALUES = {"ok", "warning", "failed"}

INDEX_CACHE_REQUIRED_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "index_code",
    "index_name",
    "source",
]

INDEX_SOURCE_DIAGNOSTICS_REQUIRED_COLUMNS = [
    "run_id",
    "checked_at",
    "index_code",
    "index_name",
    "api_name",
    "source_family",
    "call_success",
    "status_code",
    "row_count",
    "start_date",
    "end_date",
    "latest_expected_date",
    "end_date_gap_days",
    "schema_valid",
    "missing_required_columns",
    "missing_values_count",
    "duplicate_dates_count",
    "abnormal_return_count",
    "failure_type",
    "failure_reason",
    "elapsed_ms",
    "usable_as_index_source",
    "requires_manual_review",
    "suggested_action",
    "notes",
]
INDEX_SOURCE_FAILURE_ALLOWED_VALUES = {"", "proxy_error", "timeout", "http_error", "schema_error", "empty_data", "unknown"}

ETF_METRICS_REQUIRED_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "tracking_index_code",
    "tracking_index_name",
    "benchmark_available",
    "benchmark_status",
    "metric_status",
    "tracking_error",
    "tracking_error_status",
    "relative_return_20d",
    "relative_return_60d",
    "relative_return_120d",
    "benchmark_return_20d",
    "benchmark_return_60d",
    "benchmark_return_120d",
    "etf_return_20d",
    "etf_return_60d",
    "etf_return_120d",
    "discount_premium",
    "discount_premium_status",
    "fund_size",
    "management_fee",
    "custody_fee",
    "latest_amount",
    "computed_at",
    "data_start_date",
    "data_end_date",
    "benchmark_start_date",
    "benchmark_end_date",
    "failure_reason",
    "notes",
]
ETF_METRICS_COVERAGE_REQUIRED_COLUMNS = [
    "metric_name",
    "total_count",
    "computable_count",
    "unable_count",
    "coverage_ratio",
    "main_failure_reason",
    "dependency",
    "importance",
    "notes",
]
ETF_METRIC_STATUS_ALLOWED_VALUES = {
    "ok",
    "unable_to_compute",
    "missing_benchmark",
    "no_index_cache",
    "insufficient_overlap",
    "missing_etf_cache",
    "missing_required_columns",
    "source_unavailable",
    "not_applicable",
    "unknown",
}

FACTOR_SCORE_REPORT_REQUIRED_COLUMNS = [
    "symbol",
    "name",
    "total_score",
    "score_status",
    "enabled_factor_count",
    "used_factor_count",
    "skipped_factor_count",
    "failed_factor_count",
    "missing_required_factor_count",
    "rank",
    "computed_at",
    "notes",
]
FACTOR_SCORE_DETAIL_REQUIRED_COLUMNS = [
    "symbol",
    "name",
    "factor_name",
    "raw_value",
    "normalized_value",
    "weight",
    "direction",
    "weighted_score",
    "factor_status",
    "missing_policy",
    "source",
    "reason",
]
FACTOR_SCORE_AUDIT_REQUIRED_COLUMNS = [
    "audit_item",
    "status",
    "severity",
    "count",
    "ratio",
    "affected_symbols",
    "finding",
    "suggested_action",
    "notes",
]
FACTOR_SCORE_GATE_REQUIRED_COLUMNS = [
    "gate_item",
    "status",
    "severity",
    "threshold",
    "actual_value",
    "passed",
    "blocking",
    "finding",
    "suggested_action",
    "notes",
]
FACTOR_STATUS_ALLOWED_VALUES = {
    "used",
    "skipped_missing_optional",
    "missing_required",
    "disabled",
    "source_unavailable",
    "invalid_value",
    "insufficient_coverage",
    "unknown",
}
FACTOR_SCORE_STATUS_ALLOWED_VALUES = {"ok", "unable_to_score", "missing_required_factor", "no_used_factors", "unknown"}

QUALITY_DIAGNOSIS_REQUIRED_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "failure_type",
    "primary_failure_type",
    "secondary_failure_type",
    "row_count",
    "min_required_rows",
    "first_date",
    "last_date",
    "latest_expected_date",
    "end_date_gap_days",
    "history_status",
    "cache_status",
    "liquidity_status",
    "price_quality_status",
    "metadata_status",
    "strategy_eligibility",
    "remediation_priority",
    "recommended_action",
    "requires_refresh",
    "requires_manual_review",
    "exclude_from_candidate_pool",
    "reason",
    "notes",
]
QUALITY_DIAGNOSIS_SUMMARY_REQUIRED_COLUMNS = [
    "diagnosis_item",
    "count",
    "ratio",
    "severity",
    "suggested_action",
    "examples",
    "notes",
]
QUALITY_HISTORY_STATUS_ALLOWED_VALUES = {"sufficient_history", "short_history", "very_short_history", "unknown"}
QUALITY_CACHE_STATUS_ALLOWED_VALUES = {"fresh", "stale", "severely_stale", "missing", "unknown"}
QUALITY_STRATEGY_ELIGIBILITY_ALLOWED_VALUES = {
    "eligible",
    "observation_only",
    "blocked_short_history",
    "blocked_quality_failed",
    "blocked_missing_cache",
    "blocked_manual_review",
}
QUALITY_REMEDIATION_PRIORITY_ALLOWED_VALUES = {
    "P0_refresh_needed",
    "P0_manual_review",
    "P1_short_history_observe",
    "P1_quality_investigate",
    "P2_low_liquidity_filter",
    "P3_metadata_enrichment",
    "no_action",
}
CANDIDATE_GATE_REQUIRED_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "candidate_status",
    "eligibility_status",
    "gate_passed",
    "blocked",
    "block_reason",
    "observation_reason",
    "data_quality_status",
    "history_status",
    "cache_status",
    "liquidity_status",
    "price_quality_status",
    "strategy_eligibility",
    "remediation_priority",
    "requires_manual_review",
    "exclude_from_candidate_pool",
    "factor_score_status",
    "factor_gate_status",
    "recommended_action",
    "notes",
]
CANDIDATE_GATE_SUMMARY_REQUIRED_COLUMNS = [
    "gate_item",
    "count",
    "ratio",
    "severity",
    "finding",
    "suggested_action",
    "examples",
    "notes",
]
CANDIDATE_STATUS_ALLOWED_VALUES = {
    "eligible",
    "observation_only",
    "blocked_short_history",
    "blocked_manual_review",
    "blocked_quality_failed",
    "blocked_no_used_factors",
    "blocked_factor_gate",
    "unknown",
}
OBSERVATION_POOL_REQUIRED_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "row_count",
    "min_required_rows",
    "rows_needed",
    "first_date",
    "last_date",
    "latest_expected_date",
    "end_date_gap_days",
    "history_status",
    "liquidity_status",
    "observation_status",
    "observation_priority",
    "estimated_trading_days_until_eligible",
    "estimated_calendar_date_until_eligible",
    "requires_manual_review",
    "manual_review_reason",
    "low_liquidity_flag",
    "abnormal_return_flag",
    "candidate_status",
    "recommended_action",
    "notes",
]
OBSERVATION_SUMMARY_REQUIRED_COLUMNS = [
    "summary_item",
    "count",
    "ratio",
    "severity",
    "examples",
    "suggested_action",
    "notes",
]
OBSERVATION_STATUS_ALLOWED_VALUES = {
    "waiting_for_history",
    "very_short_history",
    "waiting_but_low_liquidity",
    "manual_review_required",
    "unknown",
}
OBSERVATION_PRIORITY_ALLOWED_VALUES = {
    "P0_manual_review",
    "P1_wait_for_history",
    "P2_low_liquidity_watch",
    "P3_archive_watch",
}
MANUAL_REVIEW_REQUIRED_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "review_priority",
    "review_status",
    "manual_review_reason",
    "primary_failure_type",
    "secondary_failure_type",
    "history_status",
    "row_count",
    "min_required_rows",
    "rows_needed",
    "first_date",
    "last_date",
    "latest_expected_date",
    "end_date_gap_days",
    "liquidity_status",
    "abnormal_return_flag",
    "low_liquidity_flag",
    "missing_cache_flag",
    "cache_status",
    "candidate_status",
    "observation_status",
    "evidence_fields",
    "recommended_checks",
    "possible_outcomes",
    "recommended_action",
    "notes",
]
MANUAL_REVIEW_SUMMARY_REQUIRED_COLUMNS = [
    "review_item",
    "count",
    "ratio",
    "severity",
    "examples",
    "suggested_action",
    "notes",
]
MANUAL_REVIEW_PRIORITY_ALLOWED_VALUES = {
    "P0_manual_review",
    "P1_data_watch",
    "P2_metadata_check",
}
MANUAL_REVIEW_STATUS_ALLOWED_VALUES = {
    "pending_manual_review",
    "evidence_incomplete",
    "ready_for_review",
    "blocked_until_review",
    "unknown",
}
DATA_GOVERNANCE_STATUS_REQUIRED_FIELDS = [
    "generated_at",
    "qa_exit_status",
    "data_quality_failed_count",
    "end_date_coverage_gap_days",
    "candidate_total",
    "candidate_eligible_count",
    "candidate_blocked_count",
    "blocked_short_history_count",
    "blocked_manual_review_count",
    "blocked_no_used_factors_count",
    "observation_pool_count",
    "very_short_history_count",
    "estimated_eligible_within_20d_count",
    "estimated_eligible_within_60d_count",
    "manual_review_count",
    "factor_gate_status",
    "allowed_to_enter_008b",
    "allowed_to_enter_007b",
    "next_recommended_action",
    "blocking_reasons",
    "report_paths",
]

OUTPUT_FILE_SCHEMAS: dict[str, dict[str, Any]] = {
    "data_coverage_report": {
        "required": [
            "symbol",
            "name",
            "success",
            "source",
            "start_date",
            "end_date",
            "rows",
            "status",
            "failure_reason",
        ],
        "optional": [
            "exchange",
            "asset_class",
            "category",
            "tracking_index",
            "listing_date",
            "latest_date",
            "avg_amount_20",
            "data_rows",
            "is_active",
            "filter_reason",
            "theme",
            "sector",
            "missing_count",
            "duplicate_count",
            "local_latest_date",
            "target_update_date",
        ],
    },
    "data_quality_report": {
        "required": [
            "symbol",
            "name",
            "status",
            "rows",
            "start_date",
            "end_date",
            "missing_count",
            "duplicate_count",
            "errors",
            "warnings",
            "failure_types",
            "primary_failure_type",
        ],
        "allowed": {"status": {"passed", "warning", "failed"}},
    },
    "data_failure_summary": {
        "required": [
            "symbol",
            "name",
            "asset_class",
            "category",
            "source",
            "start_date",
            "end_date",
            "row_count",
            "latest_expected_date",
            "end_date_gap_days",
            "failure_type",
            "failure_reason",
            "severity",
            "suggested_action",
        ],
        "allowed": {"severity": {"severe", "warning"}},
    },
    "data_quality_diagnosis": {
        "required": QUALITY_DIAGNOSIS_REQUIRED_COLUMNS,
        "allowed": {
            "history_status": QUALITY_HISTORY_STATUS_ALLOWED_VALUES,
            "cache_status": QUALITY_CACHE_STATUS_ALLOWED_VALUES,
            "strategy_eligibility": QUALITY_STRATEGY_ELIGIBILITY_ALLOWED_VALUES,
            "remediation_priority": QUALITY_REMEDIATION_PRIORITY_ALLOWED_VALUES,
        },
    },
    "data_quality_diagnosis_summary": {
        "required": QUALITY_DIAGNOSIS_SUMMARY_REQUIRED_COLUMNS,
        "allowed": {"severity": {"info", "medium", "high"}},
    },
    "candidate_gate": {
        "required": CANDIDATE_GATE_REQUIRED_COLUMNS,
        "allowed": {
            "candidate_status": CANDIDATE_STATUS_ALLOWED_VALUES,
            "eligibility_status": {"passed", "blocked", "observation_only"},
        },
    },
    "candidate_gate_summary": {
        "required": CANDIDATE_GATE_SUMMARY_REQUIRED_COLUMNS,
        "allowed": {"severity": {"info", "medium", "high"}},
    },
    "short_history_observation_pool": {
        "required": OBSERVATION_POOL_REQUIRED_COLUMNS,
        "allowed": {
            "history_status": {"short_history", "very_short_history", "unknown"},
            "observation_status": OBSERVATION_STATUS_ALLOWED_VALUES,
            "observation_priority": OBSERVATION_PRIORITY_ALLOWED_VALUES,
            "candidate_status": CANDIDATE_STATUS_ALLOWED_VALUES,
        },
    },
    "short_history_observation_summary": {
        "required": OBSERVATION_SUMMARY_REQUIRED_COLUMNS,
        "allowed": {"severity": {"info", "medium", "high"}},
    },
    "manual_review_list": {
        "required": MANUAL_REVIEW_REQUIRED_COLUMNS,
        "allowed": {
            "review_priority": MANUAL_REVIEW_PRIORITY_ALLOWED_VALUES,
            "review_status": MANUAL_REVIEW_STATUS_ALLOWED_VALUES,
            "history_status": QUALITY_HISTORY_STATUS_ALLOWED_VALUES,
            "cache_status": QUALITY_CACHE_STATUS_ALLOWED_VALUES,
            "candidate_status": CANDIDATE_STATUS_ALLOWED_VALUES,
            "observation_status": OBSERVATION_STATUS_ALLOWED_VALUES,
        },
    },
    "manual_review_summary": {
        "required": MANUAL_REVIEW_SUMMARY_REQUIRED_COLUMNS,
        "allowed": {"severity": {"info", "medium", "high"}},
    },
    "adjustment_audit": {
        "required": [
            "symbol",
            "name",
            "source",
            "adjust",
            "download_method",
            "fallback_used",
            "cache_file",
            "start_date",
            "end_date",
            "row_count",
            "abnormal_return_count",
            "max_abs_return",
            "max_return_date",
            "possible_adjustment_issue",
            "audit_status",
            "audit_reason",
        ],
    },
    "cache_metadata_audit": {
        "required": [
            "symbol",
            "name",
            "cache_file",
            "metadata_file",
            "metadata_exists",
            "source",
            "adjust",
            "api_name",
            "download_method",
            "fallback_used",
            "downloaded_at",
            "row_count",
            "status",
            "reason",
        ],
    },
    "trading_calendar_audit": {
        "required": [
            "calendar_file",
            "exists",
            "source",
            "start_date",
            "end_date",
            "row_count",
            "open_day_count",
            "latest_open_day",
            "today",
            "coverage_gap_days",
            "used_fallback",
            "status",
            "reason",
        ],
    },
    "cache_refresh_plan": {
        "required": [
            "symbol",
            "name",
            "source",
            "current_adjust",
            "cache_file",
            "metadata_file",
            "cache_exists",
            "metadata_exists",
            "latest_cache_date",
            "latest_expected_date",
            "end_date_gap_days",
            "quality_failed",
            "primary_failure_type",
            "adjustment_audit_status",
            "possible_adjustment_issue",
            "refresh_reason",
            "refresh_priority",
            "recommended_action",
            "requires_backup",
            "requires_manual_review",
            "safe_to_auto_refresh",
            "notes",
        ],
        "allowed": {
            "refresh_priority": {
                "P0_missing_cache",
                "P0_stale_end_date",
                "P0_quality_failed",
                "P1_legacy_unknown_adjustment",
                "P1_possible_adjustment_issue",
                "P2_optional_refresh",
            }
        },
    },
    "pilot_refresh_report": {
        "required": [
            "run_id",
            "symbol",
            "name",
            "refresh_attempted",
            "refresh_skipped",
            "skip_reason",
            "backup_created",
            "old_cache_exists",
            "old_metadata_exists",
            "new_cache_exists",
            "new_metadata_exists",
            "old_start_date",
            "old_end_date",
            "new_start_date",
            "new_end_date",
            "old_row_count",
            "new_row_count",
            "end_date_improved",
            "row_count_delta",
            "max_abs_close_diff",
            "abnormal_return_before",
            "abnormal_return_after",
            "old_adjust",
            "new_adjust",
            "metadata_written",
            "refresh_status",
            "refresh_reason",
            "requires_manual_review",
            "notes",
        ],
        "allowed": {
            "refresh_status": {
                "refreshed_ok",
                "skipped_manual_review",
                "skipped_not_in_plan",
                "skipped_over_limit",
                "download_failed",
                "compare_failed",
                "metadata_missing_after_refresh",
                "unknown",
            }
        },
    },
    "missing_cache_repair_report": {
        "required": [
            "run_id",
            "symbol",
            "name",
            "repair_attempted",
            "repair_skipped",
            "skip_reason",
            "old_cache_exists",
            "old_metadata_exists",
            "backup_created",
            "new_cache_exists",
            "new_metadata_exists",
            "new_start_date",
            "new_end_date",
            "new_row_count",
            "new_source",
            "new_adjust",
            "download_method",
            "fallback_used",
            "fallback_chain",
            "repair_status",
            "failure_reason",
            "metadata_written",
            "quality_after_repair",
            "still_missing_cache",
            "requires_manual_review",
            "notes",
        ],
        "allowed": {
            "repair_status": {
                "repaired_ok",
                "download_failed",
                "skipped_existing_cache",
                "skipped_not_missing_cache",
                "metadata_missing_after_repair",
                "quality_failed_after_repair",
                "unknown",
            }
        },
    },
    "source_preference_audit": {
        "required": [
            "symbol",
            "source_candidate",
            "adjust",
            "fetch_success",
            "row_count",
            "end_date",
            "quality_passed",
            "preferred_candidate",
            "safe_to_promote",
            "requires_manual_review",
        ],
    },
    "source_diagnostics_report": {
        "required": [
            "run_id",
            "checked_at",
            "symbol",
            "check_type",
            "endpoint",
            "proxy_env_detected",
            "http_proxy",
            "https_proxy",
            "akshare_call",
            "adjust",
            "success",
            "status_code",
            "row_count",
            "error_type",
            "error_message",
            "elapsed_ms",
            "retry_count",
            "diagnosis",
            "suggested_action",
        ],
        "allowed": {
            "check_type": {
                "akshare_em_qfq",
                "akshare_em_none",
                "akshare_sina",
                "raw_endpoint_probe",
                "proxy_env",
            }
        },
    },
    "etf_metadata": {
        "required": [
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
        ],
        "allowed": {"data_quality_status": {"passed", "warning", "failed"}},
    },
    "etf_metadata_coverage": {
        "required": [
            "field_name",
            "total_count",
            "non_null_count",
            "missing_count",
            "coverage_ratio",
            "source",
            "importance",
            "notes",
        ],
        "allowed": {"importance": {"required", "recommended", "optional"}},
    },
    "index_map": {
        "required": INDEX_MAP_REQUIRED_COLUMNS,
        "allowed": {"mapping_method": INDEX_MAPPING_METHOD_ALLOWED_VALUES},
    },
    "index_data_coverage": {
        "required": INDEX_DATA_COVERAGE_REQUIRED_COLUMNS,
        "allowed": {"quality_status": INDEX_DATA_QUALITY_ALLOWED_VALUES},
    },
    "index_source_diagnostics": {
        "required": INDEX_SOURCE_DIAGNOSTICS_REQUIRED_COLUMNS,
        "allowed": {"failure_type": INDEX_SOURCE_FAILURE_ALLOWED_VALUES},
    },
    "etf_metrics": {
        "required": ETF_METRICS_REQUIRED_COLUMNS,
        "allowed": {
            "benchmark_status": ETF_METRIC_STATUS_ALLOWED_VALUES,
            "metric_status": ETF_METRIC_STATUS_ALLOWED_VALUES,
            "tracking_error_status": ETF_METRIC_STATUS_ALLOWED_VALUES,
            "discount_premium_status": ETF_METRIC_STATUS_ALLOWED_VALUES,
        },
    },
    "etf_metrics_coverage": {
        "required": ETF_METRICS_COVERAGE_REQUIRED_COLUMNS,
        "allowed": {"importance": {"P1", "P2"}},
    },
    "factor_score_report": {
        "required": FACTOR_SCORE_REPORT_REQUIRED_COLUMNS,
        "allowed": {"score_status": FACTOR_SCORE_STATUS_ALLOWED_VALUES},
    },
    "factor_score_detail": {
        "required": FACTOR_SCORE_DETAIL_REQUIRED_COLUMNS,
        "allowed": {
            "factor_status": FACTOR_STATUS_ALLOWED_VALUES,
            "direction": {"higher_better", "lower_better"},
            "missing_policy": {"skip", "fail", "neutral"},
            "source": {"strategy_signal", "etf_metrics", "etf_metadata", "data_quality"},
        },
    },
    "factor_score_audit": {
        "required": FACTOR_SCORE_AUDIT_REQUIRED_COLUMNS,
        "allowed": {
            "severity": {"info", "warning", "high"},
            "status": {"ok", "info", "warning", "blocked", "disabled"},
        },
    },
    "factor_score_gate": {
        "required": FACTOR_SCORE_GATE_REQUIRED_COLUMNS,
        "allowed": {
            "severity": {"info", "warning", "high"},
            "status": {"passed", "warning", "blocked", "not_run"},
        },
    },
    "compare_signal": {
        "required": [
            "symbol",
            "name",
            "latest_date",
            "score",
            "rank",
            "final_signal",
        ],
        "optional": [
            "exchange",
            "asset_class",
            "category",
            "tracking_index",
            "momentum_20",
            "momentum_60",
            "momentum_120",
            "volatility_20",
            "max_drawdown_60",
        ],
    },
    "strategy_compare_signal": {
        "required": [
            "strategy_name",
            "strategy_status",
            "effective_signal_date",
            "latest_data_date",
            "target_symbols",
            "suggested_buy",
            "suggested_sell",
            "rank_table",
        ],
    },
    "data_governance_status": {
        "required": DATA_GOVERNANCE_STATUS_REQUIRED_FIELDS,
    },
}

QA_REPORT_REQUIRED_TOP_LEVEL = [
    "data_layer",
    "strategy_layer",
    "output_layer",
    "allow_small_observation",
    "blocking_reasons",
    "recommended_for_observation",
    "not_recommended",
    "defensive_only",
    "risk_note",
]
QA_REPORT_REQUIRED_DATA_LAYER = [
    "passed",
    "effective_etf_count",
    "latest_date",
    "reasons",
    "coverage_report",
    "quality_report",
    "trading_calendar_report",
    "trading_calendar",
    "failure_summary_report",
    "failure_summary",
    "cache_metadata_audit_report",
    "cache_metadata_audit",
    "adjustment_audit_report",
    "adjustment_audit",
]
QA_REPORT_REQUIRED_SUMMARIES = {
    "failure_summary": ["total_failed", "failure_type_counts", "severe_failed", "warning_failed", "top_examples"],
    "data_quality_diagnosis": [
        "total_failed",
        "short_history_count",
        "stale_cache_count",
        "missing_cache_count",
        "abnormal_return_count",
        "low_liquidity_count",
        "severe_quality_issue_count",
        "candidate_excluded_count",
        "manual_review_required_count",
        "refresh_needed_count",
        "history_status_counts",
        "cache_status_counts",
        "strategy_eligibility_counts",
        "remediation_priority_counts",
        "top_blocking_reasons",
        "top_examples",
    ],
    "adjustment_audit": [
        "total_checked",
        "unknown_adjustment_count",
        "fallback_used_count",
        "abnormal_return_symbols",
        "possible_adjustment_issue_count",
        "top_examples",
    ],
    "cache_metadata_audit": [
        "total_cache_files",
        "metadata_exists_count",
        "legacy_cache_without_metadata_count",
        "unknown_adjustment_count",
        "metadata_cache_mismatch_count",
        "top_examples",
    ],
    "trading_calendar": [
        "calendar_file",
        "status",
        "source",
        "start_date",
        "end_date",
        "latest_open_day",
        "coverage_gap_days",
        "used_fallback",
        "reason",
    ],
    "cache_refresh_plan": [
        "total_candidates",
        "priority_counts",
        "reason_counts",
        "safe_to_auto_refresh_count",
        "manual_review_required_count",
        "top_examples",
    ],
    "pilot_refresh": [
        "status",
        "report",
        "last_run_id",
        "attempted_count",
        "refreshed_ok_count",
        "skipped_count",
        "failed_count",
        "metadata_written_count",
        "end_date_improved_count",
        "top_examples",
    ],
    "missing_cache_repair": [
        "status",
        "report",
        "last_run_id",
        "attempted_count",
        "repaired_ok_count",
        "download_failed_count",
        "still_missing_cache_count",
        "metadata_written_count",
        "quality_failed_after_repair_count",
        "top_examples",
    ],
    "source_preference_audit": [
        "status",
        "report",
        "total_symbols",
        "total_candidates",
        "em_qfq_success_count",
        "sina_success_count",
        "em_qfq_safe_to_promote_count",
        "manual_review_required_count",
        "preferred_candidate_counts",
        "top_examples",
    ],
    "source_diagnostics": [
        "status",
        "report",
        "total_symbols",
        "total_checks",
        "em_qfq_success_count",
        "em_none_success_count",
        "sina_success_count",
        "proxy_error_count",
        "timeout_count",
        "suggested_action",
        "top_examples",
    ],
    "etf_metadata": [
        "status",
        "etf_metadata_report",
        "etf_metadata_coverage_report",
        "total_etfs",
        "required_field_coverage",
        "recommended_field_coverage",
        "missing_required_fields",
        "low_coverage_fields",
        "metadata_source",
        "top_examples",
    ],
    "index_data": [
        "status",
        "index_map_report",
        "index_data_coverage_report",
        "total_index_mappings",
        "index_cache_written_count",
        "usable_benchmark_count",
        "fetch_success_count",
        "fetch_failed_count",
        "csindex_success_count",
        "eastmoney_failure_count",
        "schema_invalid_count",
        "manual_review_required_count",
        "low_coverage_indexes",
        "top_examples",
    ],
    "index_source_diagnostics": [
        "status",
        "index_source_diagnostics_report",
        "total_indexes_checked",
        "total_api_candidates",
        "success_count",
        "usable_source_count",
        "eastmoney_failure_count",
        "proxy_error_count",
        "timeout_count",
        "preferred_api_candidates",
        "top_examples",
        "suggested_action",
    ],
    "etf_metrics": [
        "status",
        "etf_metrics_report",
        "etf_metrics_coverage_report",
        "total_etfs",
        "metrics_computable_count",
        "tracking_error_computable_count",
        "relative_return_computable_count",
        "discount_premium_available_count",
        "no_index_cache_count",
        "missing_benchmark_count",
        "insufficient_overlap_count",
        "source_unavailable_count",
        "top_examples",
    ],
    "observation_pool": [
        "total_observation_count",
        "very_short_history_count",
        "low_liquidity_watch_count",
        "manual_review_required_count",
        "estimated_eligible_within_20d_count",
        "estimated_eligible_within_60d_count",
        "unknown_estimate_count",
        "top_examples",
    ],
    "manual_review": [
        "manual_review_count",
        "p0_manual_review_count",
        "abnormal_return_review_count",
        "low_liquidity_review_count",
        "very_short_history_review_count",
        "top_examples",
    ],
    "data_governance": [
        "data_governance_runbook",
        "data_governance_status_report",
        "allowed_to_enter_008b",
        "allowed_to_enter_007b",
        "next_recommended_action",
        "blocking_reasons",
    ],
}
QA_REPORT_REQUIRED_STRATEGY_SUMMARIES = {
    "factor_score": [
        "status",
        "factor_score_report",
        "factor_score_detail_report",
        "factor_score_audit_report",
        "factor_score_gate_report",
        "total_symbols",
        "score_computable_count",
        "unable_to_score_count",
        "enabled_factor_count",
        "used_factor_counts",
        "skipped_factor_counts",
        "missing_required_factor_count",
        "audit_status",
        "high_severity_findings",
        "warning_findings",
        "computable_ratio",
        "top_blocking_reasons",
        "gate_status",
        "blocking_findings",
        "passed_gate_count",
        "failed_gate_count",
        "top_examples",
    ],
    "candidate_gate": [
        "total_symbols",
        "eligible_count",
        "observation_only_count",
        "blocked_count",
        "blocked_short_history_count",
        "blocked_manual_review_count",
        "blocked_factor_gate_count",
        "top_blocking_reasons",
        "top_examples",
    ],
}


class SchemaValidationError(ValueError):
    pass


def validate_required_columns(df: pd.DataFrame, required_columns: list[str] | set[str], object_name: str) -> None:
    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise SchemaValidationError(f"{object_name} missing required columns: {', '.join(missing)}")


def validate_allowed_values(series: pd.Series, allowed_values: set[str], field_name: str, *, allow_blank: bool = False) -> None:
    values = series.dropna().astype(str).str.strip()
    if allow_blank:
        values = values[values != ""]
    invalid = sorted(set(values) - set(allowed_values))
    if invalid:
        raise SchemaValidationError(f"{field_name} has invalid values: {', '.join(invalid[:20])}")


def validate_parseable_dates(series: pd.Series, field_name: str, *, allow_blank: bool = False) -> None:
    values = series.dropna()
    if allow_blank:
        values = values[values.astype(str).str.strip() != ""]
    parsed = pd.to_datetime(values, errors="coerce")
    if parsed.isna().any():
        raise SchemaValidationError(f"{field_name} contains unparseable date values")


def validate_bool_like(series: pd.Series, field_name: str, *, allow_blank: bool = False) -> None:
    values = series.dropna().astype(str).str.strip().str.lower()
    if allow_blank:
        values = values[values != ""]
    allowed = {"true", "false", "1", "0", "yes", "no", "y", "n", "open"}
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise SchemaValidationError(f"{field_name} contains non-bool-like values: {', '.join(invalid[:20])}")


def validate_numeric_columns(df: pd.DataFrame, columns: list[str], object_name: str, *, allow_blank: bool = False) -> None:
    for column in columns:
        if column not in df.columns:
            raise SchemaValidationError(f"{object_name} missing numeric column: {column}")
        values = df[column].dropna()
        if allow_blank:
            values = values[values.astype(str).str.strip() != ""]
        parsed = pd.to_numeric(values, errors="coerce")
        if parsed.isna().any():
            raise SchemaValidationError(f"{object_name}.{column} contains non-numeric values")


def validate_price_cache_frame(df: pd.DataFrame, object_name: str = "price cache") -> None:
    validate_required_columns(df, PRICE_CACHE_REQUIRED_COLUMNS, object_name)
    validate_parseable_dates(df["date"], f"{object_name}.date")
    validate_numeric_columns(df, [*PRICE_COLUMNS, *VOLUME_COLUMNS], object_name)


def validate_index_cache_frame(df: pd.DataFrame, object_name: str = "index cache") -> None:
    validate_required_columns(df, INDEX_CACHE_REQUIRED_COLUMNS, object_name)
    validate_parseable_dates(df["date"], f"{object_name}.date")
    validate_numeric_columns(df, [*PRICE_COLUMNS, *VOLUME_COLUMNS], object_name)


def validate_cache_metadata(metadata: dict[str, Any], object_name: str = "cache metadata") -> None:
    missing = [field for field in CACHE_METADATA_REQUIRED_FIELDS if field not in metadata]
    if missing:
        raise SchemaValidationError(f"{object_name} missing required fields: {', '.join(missing)}")
    adjust = str(metadata.get("adjust", "")).strip()
    if adjust not in ADJUST_ALLOWED_VALUES:
        raise SchemaValidationError(f"{object_name}.adjust has invalid value: {adjust}")
    if not isinstance(metadata.get("fallback_used"), bool):
        raise SchemaValidationError(f"{object_name}.fallback_used must be bool")
    if not isinstance(metadata.get("fallback_chain"), list):
        raise SchemaValidationError(f"{object_name}.fallback_chain must be list")
    try:
        int(metadata.get("row_count"))
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"{object_name}.row_count must be numeric") from exc


def validate_qa_report_schema(report: dict[str, Any]) -> None:
    missing = [field for field in QA_REPORT_REQUIRED_TOP_LEVEL if field not in report]
    if missing:
        raise SchemaValidationError(f"qa_report missing top-level fields: {', '.join(missing)}")
    for layer_name in ["data_layer", "strategy_layer", "output_layer"]:
        if not isinstance(report.get(layer_name), dict):
            raise SchemaValidationError(f"qa_report.{layer_name} must be an object")
        if "passed" not in report[layer_name]:
            raise SchemaValidationError(f"qa_report.{layer_name}.passed is required")
    data_layer = report["data_layer"]
    missing_data = [field for field in QA_REPORT_REQUIRED_DATA_LAYER if field not in data_layer]
    if missing_data:
        raise SchemaValidationError(f"qa_report.data_layer missing fields: {', '.join(missing_data)}")
    for summary_name, required_fields in QA_REPORT_REQUIRED_SUMMARIES.items():
        if summary_name in {"data_quality_diagnosis", "cache_refresh_plan", "pilot_refresh", "missing_cache_repair", "source_preference_audit", "source_diagnostics", "etf_metadata", "index_data", "index_source_diagnostics", "etf_metrics", "observation_pool", "manual_review", "data_governance"} and summary_name not in data_layer:
            continue
        summary = data_layer.get(summary_name)
        if not isinstance(summary, dict):
            raise SchemaValidationError(f"qa_report.data_layer.{summary_name} must be an object")
        missing_summary = [field for field in required_fields if field not in summary]
        if missing_summary:
            raise SchemaValidationError(f"qa_report.data_layer.{summary_name} missing fields: {', '.join(missing_summary)}")
    strategy_layer = report["strategy_layer"]
    for summary_name, required_fields in QA_REPORT_REQUIRED_STRATEGY_SUMMARIES.items():
        if summary_name not in strategy_layer:
            continue
        summary = strategy_layer.get(summary_name)
        if not isinstance(summary, dict):
            raise SchemaValidationError(f"qa_report.strategy_layer.{summary_name} must be an object")
        missing_summary = [field for field in required_fields if field not in summary]
        if missing_summary:
            raise SchemaValidationError(f"qa_report.strategy_layer.{summary_name} missing fields: {', '.join(missing_summary)}")
    if "schema_version" in report and not isinstance(report["schema_version"], str):
        raise SchemaValidationError("qa_report.schema_version must be a string when present")
    if "data_schema_version" in report and not isinstance(report["data_schema_version"], str):
        raise SchemaValidationError("qa_report.data_schema_version must be a string when present")


def validate_data_governance_status_schema(report: dict[str, Any]) -> None:
    missing = [field for field in DATA_GOVERNANCE_STATUS_REQUIRED_FIELDS if field not in report]
    if missing:
        raise SchemaValidationError(f"data_governance_status missing fields: {', '.join(missing)}")
    for field in [
        "data_quality_failed_count",
        "end_date_coverage_gap_days",
        "candidate_total",
        "candidate_eligible_count",
        "candidate_blocked_count",
        "blocked_short_history_count",
        "blocked_manual_review_count",
        "blocked_no_used_factors_count",
        "observation_pool_count",
        "very_short_history_count",
        "estimated_eligible_within_20d_count",
        "estimated_eligible_within_60d_count",
        "manual_review_count",
    ]:
        if not isinstance(report.get(field), int):
            raise SchemaValidationError(f"data_governance_status.{field} must be int")
    for field in ["allowed_to_enter_008b", "allowed_to_enter_007b"]:
        if not isinstance(report.get(field), bool):
            raise SchemaValidationError(f"data_governance_status.{field} must be bool")
    if not isinstance(report.get("blocking_reasons"), list):
        raise SchemaValidationError("data_governance_status.blocking_reasons must be list")
    if not isinstance(report.get("report_paths"), dict):
        raise SchemaValidationError("data_governance_status.report_paths must be object")
    if str(report.get("qa_exit_status")) not in {"passed", "failed"}:
        raise SchemaValidationError("data_governance_status.qa_exit_status must be passed or failed")


def validate_output_file_schema(path: str | Path, schema_name: str) -> None:
    file_path = Path(path)
    if schema_name == "qa_report":
        report = json.loads(file_path.read_text(encoding="utf-8"))
        validate_qa_report_schema(report)
        return
    if schema_name == "data_governance_status":
        report = json.loads(file_path.read_text(encoding="utf-8"))
        validate_data_governance_status_schema(report)
        return
    schema = OUTPUT_FILE_SCHEMAS[schema_name]
    frame = pd.read_csv(file_path, dtype=str, encoding="utf-8-sig").fillna("")
    validate_required_columns(frame, schema["required"], schema_name)
    for field_name, allowed in schema.get("allowed", {}).items():
        if field_name in frame.columns:
            validate_allowed_values(frame[field_name], set(allowed), f"{schema_name}.{field_name}", allow_blank=False)
