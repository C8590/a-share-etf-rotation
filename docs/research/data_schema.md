# ETF-GAP-001 Data Schema Contract

Version: 1.0

This document freezes the current data-layer and output-layer contracts. It is a compatibility contract, not a data refresh plan. Existing data-quality gates remain unchanged: a failed `qa-check` caused by stale coverage, missing cache, or ETF quality failures is still a real failure.

## Versioning

- `schema_version`: version of the report/container contract, currently `1.0`.
- `data_schema_version`: version of the market-data/cache contract, currently `1.0`.
- `cache_schema_version`: version of one cache sidecar metadata record, currently `1.0`.
- New optional fields may be added without breaking old readers.
- Required fields must not be deleted or renamed without a migration period.
- A renamed field must be dual-written or read through a compatibility alias until UI, tests, and reports have moved.

## A. Price Cache Schema

Object: six-digit ETF price files under `data/cache`, for example `data/cache/510300.csv`.

`data/cache/indicator_cache.csv` is an internal indicator cache, not a raw price cache. It is outside this price-cache contract and should get its own schema if it becomes a stable downstream interface.

Required columns:

| Field | Type | Notes |
| --- | --- | --- |
| `date` | date string | Parseable by pandas; canonical output is `YYYY-MM-DD`. |
| `open` | number | Positive OHLC value. |
| `high` | number | Positive; should be >= open/close/low. |
| `low` | number | Positive; should be <= open/close/high. |
| `close` | number | Positive OHLC value. |
| `volume` | number | Trading volume; zero or negative is a quality warning. |
| `amount` | number | Trading amount; zero or negative is a quality warning. |
| `symbol` | string | Six-digit ETF code, zero-padded. |
| `name` | string | ETF display name. |
| `source` | string | Data source or local-cache marker. |

`source` currently identifies the data path, for example:

- `akshare.fund_etf_hist_sina`
- `akshare.fund_etf_hist_em.qfq`
- `akshare.fund_etf_hist_em.none`
- `local_cache`

Legacy cache limitations:

- A legacy CSV may have `source=local_cache` even when the original download path is unknown.
- Adjustment state cannot be fully trusted unless a matching `data/cache_meta/*.json` file exists.
- Legacy cache must be representable as `adjust=unknown`; tests must not force a refresh to make metadata exist.

## B. Price Cache Metadata Schema

Object: `data/cache_meta/*.json`

Required fields:

| Field | Type | Notes |
| --- | --- | --- |
| `symbol` | string | Six-digit ETF code. |
| `name` | string | ETF display name. |
| `source` | string | Source used for the cached data. |
| `adjust` | enum | One of `qfq`, `hfq`, `none`, `unknown`. |
| `api_name` | string | API family, such as `fund_etf_hist_em`. |
| `endpoint` | string | Full endpoint label when known. |
| `download_method` | string | Internal method, such as `akshare_em_chunked_qfq`. |
| `fallback_used` | bool | True when the preferred source failed and a later source succeeded. |
| `fallback_chain` | list[string] | Ordered attempted/successful source labels. |
| `cache_file` | string | Path to the CSV cache. |
| `downloaded_at` | datetime string | ISO-like timestamp with timezone when available. |
| `start_date` | date string | First cache date. |
| `end_date` | date string | Last cache date. |
| `row_count` | integer | Row count of the cache frame when metadata was written. |
| `cache_schema_version` | string | Metadata record schema version. |
| `data_schema_version` | string | Price data schema version. |
| `created_by` | string | Producer task or component label. |

Legacy cache without metadata:

- `cache_metadata_audit.csv` uses `status=warning_legacy_cache_without_metadata`.
- `adjustment_audit.csv` records `adjust=unknown` and `audit_status=warning_unknown_adjustment`.
- This is a warning/traceability gap, not proof that prices are wrong.

## C. ETF Universe Schema

Objects:

- `data/universe/etf_universe.csv`
- `config/etf_universe.yaml`

`data/universe/etf_universe.csv` required columns:

- `symbol`
- `name`
- `exchange`
- `asset_class`
- `category`
- `tracking_index`

Optional/enrichment columns:

- `spot_amount`
- `spot_date`
- `spot_updated_at`
- `listing_date`
- `latest_date`
- `avg_amount_20`
- `data_rows`
- `is_active`
- `filter_reason`
- `universe_source`
- `fetched_at`

`config/etf_universe.yaml` required top-level keys:

- `default_preset`
- `presets`
- `filters`
- `etfs`

ETF item required fields:

- `symbol`
- `name`

ETF item optional fields:

- `exchange`
- `asset_class`
- `category`
- `theme`
- `sector`
- `tracking_index`

Filter fields currently used:

- `enable_universe_filter`
- `min_trading_days`
- `avg_amount_window`
- `min_avg_amount`
- `min_data_completeness`
- `max_stale_days`
- `max_zero_amount_days`

## D. Trading Calendar Schema

Object: `data/calendar/a_share_trading_calendar.csv`

Required columns:

| Field | Type | Notes |
| --- | --- | --- |
| `date` | date string | Parseable date; canonical output is `YYYY-MM-DD`. |
| `is_open` | bool-like | True/open means trading day. |
| `exchange` | string | Current value is `A_SHARE`. |
| `source` | enum/string | Current authoritative snapshot source is `akshare.tool_trade_date_hist_sina`. |
| `calendar_version` | string | Calendar schema/version marker. |
| `generated_at` | datetime string | Snapshot generation time. |
| `note` | string | Warning or free-form note. |

Allowed source values currently observed or reserved:

- `akshare.tool_trade_date_hist_sina`
- `local_snapshot`
- `akshare_runtime`
- `weekday_fallback`
- `unit-test`

Staleness:

- `latest_open_day` is computed from the latest open day on or before `today`.
- Calendar audit marks stale snapshots as `warning_calendar_stale` when the latest calendar open day is too far behind `today`.
- Weekday fallback is explicit and must be marked `warning_weekday_fallback`.

## E. Output Report Schemas

### `output/data_coverage_report.csv`

Required:

- `symbol`, `name`, `success`, `source`, `start_date`, `end_date`, `rows`, `status`, `failure_reason`

Optional:

- `exchange`, `asset_class`, `category`, `tracking_index`, `listing_date`, `latest_date`, `avg_amount_20`, `data_rows`, `is_active`, `filter_reason`, `theme`, `sector`, `missing_count`, `duplicate_count`, `local_latest_date`, `target_update_date`

### `output/data_quality_report.csv`

Required:

- `symbol`, `name`, `status`, `rows`, `start_date`, `end_date`, `missing_count`, `duplicate_count`, `errors`, `warnings`, `failure_types`, `primary_failure_type`

Allowed `status` values:

- `passed`
- `warning`
- `failed`

### `output/data_failure_summary.csv`

Required:

- `symbol`, `name`, `asset_class`, `category`, `source`, `start_date`, `end_date`, `row_count`, `latest_expected_date`, `end_date_gap_days`, `failure_type`, `failure_reason`, `severity`, `suggested_action`

Known `failure_type` values:

- `download_failed`
- `missing_required_columns`
- `insufficient_rows`
- `stale_end_date`
- `invalid_ohlc`
- `missing_values`
- `duplicate_dates`
- `abnormal_return`
- `zero_or_low_liquidity`
- `filtered_by_universe_rule`
- `unknown`

`unknown` is a valid fallback and must not break parsers.

Allowed `severity` values:

- `severe`
- `warning`

### `output/data_quality_diagnosis.csv`

Current file role: second-pass root-cause diagnosis for ETF rows whose `data_quality_report.csv` status is `failed`. It is generated by `python main.py diagnose-data-quality` and by `qa-check`. It is a remediation plan, not a data refresh command.

Required:

- `symbol`
- `name`
- `category`
- `sub_category`
- `failure_type`
- `primary_failure_type`
- `secondary_failure_type`
- `row_count`
- `min_required_rows`
- `first_date`
- `last_date`
- `latest_expected_date`
- `end_date_gap_days`
- `history_status`
- `cache_status`
- `liquidity_status`
- `price_quality_status`
- `metadata_status`
- `strategy_eligibility`
- `remediation_priority`
- `recommended_action`
- `requires_refresh`
- `requires_manual_review`
- `exclude_from_candidate_pool`
- `reason`
- `notes`

Known `history_status` values:

- `sufficient_history`
- `short_history`
- `very_short_history`
- `unknown`

Known `cache_status` values:

- `fresh`
- `stale`
- `severely_stale`
- `missing`
- `unknown`

Known `strategy_eligibility` values:

- `eligible`
- `observation_only`
- `blocked_short_history`
- `blocked_quality_failed`
- `blocked_missing_cache`
- `blocked_manual_review`

Known `remediation_priority` values:

- `P0_refresh_needed`
- `P0_manual_review`
- `P1_short_history_observe`
- `P1_quality_investigate`
- `P2_low_liquidity_filter`
- `P3_metadata_enrichment`
- `no_action`

Boolean-like fields:

- `requires_refresh`
- `requires_manual_review`
- `exclude_from_candidate_pool`

`blocked_short_history` means the ETF must not enter candidate construction yet. It is not a low factor score and must not be converted into a penalty score.

### `output/data_quality_diagnosis_summary.csv`

Current file role: compact aggregation of the diagnosis report for QA and handoff review.

Required:

- `diagnosis_item`
- `count`
- `ratio`
- `severity`
- `suggested_action`
- `examples`
- `notes`

### `output/qa_status_breakdown.csv`

Current file role: QA failure actionability report. It is generated by `python main.py summarize-qa-status` and by `qa-check`. It only reads existing reports and must not refresh ETF cache, write index cache, change strategy outputs, alter backtest returns, modify UI behavior, clear blockers, or relax QA.

Required:

- `qa_item`
- `raw_status`
- `normalized_status`
- `severity`
- `blocking`
- `actionability`
- `affected_count`
- `affected_ratio`
- `root_cause`
- `governed_by`
- `recommended_action`
- `can_be_fixed_by_refresh`
- `can_be_fixed_by_waiting`
- `requires_manual_review`
- `blocks_candidate_pool`
- `blocks_007b`
- `blocks_008b`
- `notes`

Allowed `actionability` values:

- `refresh_needed`
- `wait_for_history`
- `manual_review`
- `source_unavailable`
- `governance_blocked`
- `already_governed`
- `unknown`

Interpretation:

- `wait_for_history` is not a refresh queue.
- `refresh_needed` means a controlled refresh or source-lag diagnosis may be appropriate, not that this command refreshed data.
- `manual_review` requires a human decision before any unblock.
- `source_unavailable` blocks benchmark-dependent work such as ETF-GAP-007B.
- `governance_blocked` and `already_governed` explain the failure but do not clear QA.

### `output/qa_status_summary.csv`

Current file role: aggregate QA actionability summary.

Required:

- `summary_item`
- `count`
- `severity`
- `finding`
- `suggested_action`
- `examples`
- `notes`

Known `severity` values:

- `info`
- `medium`
- `high`

### `output/candidate_gate.csv`

Current file role: pre-candidate eligibility gate for future candidate research. It reads existing diagnosis, factor score, and factor gate reports. It must not modify formal strategy outputs, backtest returns, `compare_signal`, ETF cache, or index cache.

Required:

- `symbol`
- `name`
- `category`
- `sub_category`
- `candidate_status`
- `eligibility_status`
- `gate_passed`
- `blocked`
- `block_reason`
- `observation_reason`
- `data_quality_status`
- `history_status`
- `cache_status`
- `liquidity_status`
- `price_quality_status`
- `strategy_eligibility`
- `remediation_priority`
- `requires_manual_review`
- `exclude_from_candidate_pool`
- `factor_score_status`
- `factor_gate_status`
- `recommended_action`
- `notes`

Known `candidate_status` values:

- `eligible`
- `observation_only`
- `blocked_short_history`
- `blocked_manual_review`
- `blocked_quality_failed`
- `blocked_no_used_factors`
- `blocked_factor_gate`
- `unknown`

Known `eligibility_status` values:

- `passed`
- `blocked`
- `observation_only`

Boolean-like fields:

- `gate_passed`
- `blocked`
- `requires_manual_review`
- `exclude_from_candidate_pool`

`blocked_no_used_factors` is not a low score. It means the ETF does not have enough usable factor evidence to be scored.

### `output/candidate_gate_summary.csv`

Current file role: compact aggregation of candidate gate status for QA and handoff review.

Required:

- `gate_item`
- `count`
- `ratio`
- `severity`
- `finding`
- `suggested_action`
- `examples`
- `notes`

Known `severity` values:

- `info`
- `medium`
- `high`

### `output/candidate_unblock_plan.csv`

Current file role: per-symbol unblock path plan for the current all-blocked candidate gate. It is generated by `python main.py build-candidate-unblock-plan` and by `qa-check`. It only reads existing reports and must not mark any ETF eligible, create formal candidates, refresh cache, replace `compare_signal`, change backtest returns, modify UI behavior, or relax QA.

Required:

- `symbol`
- `name`
- `current_candidate_status`
- `current_block_reason`
- `unblock_path`
- `unblock_status`
- `unblock_priority`
- `required_conditions`
- `waiting_condition`
- `manual_review_condition`
- `benchmark_condition`
- `factor_gate_condition`
- `metadata_condition`
- `liquidity_condition`
- `estimated_earliest_review_date`
- `can_be_unblocked_by_waiting`
- `can_be_unblocked_by_manual_review`
- `can_be_unblocked_by_refresh`
- `can_be_unblocked_by_benchmark_update`
- `still_blocked_after_primary_fix`
- `next_action`
- `notes`

Allowed `unblock_path` values:

- `wait_for_history`
- `manual_review_required`
- `factor_gate_blocked`
- `no_used_factors`
- `benchmark_dependency_missing`
- `metadata_dependency_missing`
- `liquidity_watch`
- `unknown`

Allowed `unblock_status` values:

- `not_ready`
- `waiting`
- `requires_manual_review`
- `requires_data_dependency`
- `requires_factor_gate_pass`
- `eligible_after_conditions`
- `unknown`

Interpretation:

- `wait_for_history` means row-count accumulation is required and no refresh is implied.
- `manual_review_required` means a human review must complete before any rerun can reconsider the row.
- `benchmark_dependency_missing` means benchmark/index cache work is needed before benchmark-dependent factors can help.
- `still_blocked_after_primary_fix=True` means another blocker remains after the primary row-level condition.

### `output/candidate_unblock_summary.csv`

Current file role: aggregate view of the unblock path plan.

Required:

- `unblock_item`
- `count`
- `ratio`
- `severity`
- `finding`
- `suggested_action`
- `examples`
- `notes`

Known `severity` values:

- `info`
- `medium`
- `high`

### `output/factor_008b_readiness.csv`

Current file role: ETF-GAP-008B precheck and remediation plan. It is generated by `python main.py check-factor-008b-readiness` and by `qa-check`. It only reads existing reports and must not enter 008B, generate `factor_score_candidates.csv`, change factor scores, connect to existing strategy, replace `compare_signal`, alter backtest returns, refresh cache, modify UI behavior, or relax QA.

Required:

- `readiness_item`
- `current_status`
- `passed`
- `blocking`
- `severity`
- `threshold`
- `actual_value`
- `blocker_type`
- `dependency`
- `remediation_action`
- `prerequisite_task`
- `estimated_path`
- `can_be_resolved_by_waiting`
- `can_be_resolved_by_manual_review`
- `can_be_resolved_by_index_cache`
- `can_be_resolved_by_metadata`
- `can_be_resolved_by_nav_iopv`
- `can_be_resolved_by_factor_config`
- `notes`

Known `readiness_item` values include:

- `candidate_eligible_count`
- `factor_gate_status`
- `min_computable_ratio`
- `max_unable_to_score_ratio`
- `short_history_bias`
- `no_used_factors`
- `tracking_error_dependency`
- `relative_return_dependency`
- `discount_premium_dependency`
- `fund_size_dependency`
- `management_fee_dependency`
- `factor_coverage_minimum`
- `manual_review_required`
- `benchmark_dependency`
- `metadata_dependency`

Interpretation:

- `blocking=True` means 008B must not start.
- `short_history` blockers are resolved by waiting and rerunning governance, not by scoring them low.
- `no_used_factors` is missing evidence, not a low score.
- `benchmark_dependency` requires schema-valid index cache.
- `nav_iopv_dependency` requires NAV or IOPV and must not be approximated from price.
- `metadata_dependency` requires coverage and config review before enabling metadata factors.

### `output/factor_008b_readiness_summary.csv`

Current file role: aggregate view of 008B readiness blockers and warnings.

Required:

- `summary_item`
- `count`
- `severity`
- `finding`
- `suggested_action`
- `examples`
- `notes`

Known `severity` values:

- `info`
- `warning`
- `medium`
- `high`

### `output/index_007b_readiness.csv`

Current file role: ETF-GAP-007B benchmark/index readiness precheck. It is generated by `python main.py check-index-007b-readiness` and by `qa-check`. It only reads existing outputs and local cache state; it must not enter 007B, calculate real `tracking_error`, calculate real `relative_return`, refresh ETF cache, refresh index cache, change strategy output, replace `compare_signal`, alter backtest returns, modify UI behavior, or relax QA.

Required:

- `readiness_item`
- `current_status`
- `passed`
- `blocking`
- `severity`
- `threshold`
- `actual_value`
- `blocker_type`
- `dependency`
- `remediation_action`
- `prerequisite_task`
- `estimated_path`
- `can_be_resolved_by_network`
- `can_be_resolved_by_index_update`
- `can_be_resolved_by_manual_mapping`
- `can_be_resolved_by_schema_fix`
- `notes`

Known `readiness_item` values include:

- `usable_benchmark_count`
- `index_cache_exists`
- `index_cache_schema_valid`
- `index_data_fetch_success`
- `benchmark_mapping_confidence`
- `etf_to_benchmark_mapping_available`
- `overlap_days_available`
- `tracking_error_computable_count`
- `relative_return_computable_count`
- `index_source_network_available`
- `eastmoney_proxy_failure`
- `csindex_available`
- `no_fake_benchmark_guard`
- `partial_index_cache_missing_count`
- `missing_benchmark_count`
- `discount_premium_available_count`

Interpretation:

- `blocking=True` means the applicable 007B scope must not start.
- `usable_benchmark_count` is computed first from `output/index_data_coverage.csv` rows where `usable_as_benchmark`, `schema_valid`, and `fetch_success` are true. `qa_report.json` is fallback only when coverage is missing.
- `usable_benchmark_count == 0` blocks all 007B.
- `tracking_error_computable_count == 0` and `relative_return_computable_count == 0` block real benchmark-relative metrics.
- `partial_index_cache_missing_count > 0` and `missing_benchmark_count > 0` are warnings for small-scope 007B and blockers for full-scope 007B.
- `readiness_status=ready_small_scope` means only ETFs with confirmed benchmark mappings, schema-valid index cache, and positive benchmark-relative metric computability may enter 007B validation.
- `allowed_to_enter_007b_scope=small_scope` is not full-market permission and does not connect 007B to strategy scoring, candidate pools, backtests, UI, or `compare_signal`.
- `name_inferred` and `unable_to_confirm` mappings are not hard benchmarks.
- `no_fake_benchmark_guard` must always pass.

### `output/index_007b_unlock_plan.csv`

Current file role: ETF-level path for unlocking small-scope 007B after confirmed mapping, real index cache, and ETF metrics become available.

Required:

- `symbol`
- `name`
- `tracking_index_code`
- `tracking_index_name`
- `mapping_method`
- `mapping_confidence`
- `usable_as_benchmark`
- `index_cache_exists`
- `index_cache_schema_valid`
- `index_fetch_success`
- `benchmark_status`
- `etf_metrics_status`
- `tracking_error_status`
- `relative_return_status`
- `required_action`
- `unlock_priority`
- `eligible_for_007b_after_unlock`
- `notes`

Known `unlock_priority` values:

- `P0_get_index_cache`
- `P1_validate_mapping`
- `P1_fix_index_schema`
- `P2_wait_for_network`
- `P3_manual_review`
- `no_action`

### `output/index_007b_readiness_summary.csv`

Current file role: aggregate view of 007B readiness blockers, warnings, and ETF-level unlock priorities.

Required:

- `summary_item`
- `count`
- `severity`
- `finding`
- `suggested_action`
- `examples`
- `notes`

Known `severity` values:

- `info`
- `warning`
- `medium`
- `high`

### `output/etf_007b_metrics_report.csv`

Current file role: small-scope ETF-GAP-007B research report. It reads existing
`output/etf_metrics.csv` and local cache dates for validation context. It must
not refresh ETF cache, refresh index cache, change strategy outputs, alter
backtest returns, update UI, replace `compare_signal`, generate factor
candidates, or relax QA.

Required columns:

- `symbol`
- `name`
- `tracking_index_code`
- `tracking_index_name`
- `benchmark_available`
- `benchmark_status`
- `tracking_error`
- `tracking_error_status`
- `relative_return_20d`
- `relative_return_60d`
- `relative_return_120d`
- `benchmark_return_20d`
- `benchmark_return_60d`
- `benchmark_return_120d`
- `etf_return_20d`
- `etf_return_60d`
- `etf_return_120d`
- `overlap_days`
- `data_start_date`
- `data_end_date`
- `benchmark_start_date`
- `benchmark_end_date`
- `computation_status`
- `validation_status`
- `failure_reason`
- `notes`

Known `validation_status` values:

- `computed_valid`
- `no_index_cache`
- `missing_benchmark`
- `insufficient_overlap`
- `schema_invalid`
- `source_unavailable`
- `unknown`

Interpretation:

- `computed_valid` requires real `tracking_error`, real `relative_return_20d/60d/120d`, and real benchmark return fields.
- `no_index_cache`, `missing_benchmark`, and `insufficient_overlap` remain unavailable states and must not be filled with zero.
- `computed_valid` rows are small-scope research rows only. They are not factor-score inputs and are not candidate-gate inputs.
- Full-scope 007B remains unavailable while any confirmed benchmark cache is missing/schema-invalid or any ETF lacks a confirmed benchmark mapping.

### `output/etf_007b_metrics_summary.csv`

Current file role: aggregate view of small-scope ETF-GAP-007B metric validation.

Required:

- `summary_item`
- `count`
- `severity`
- `finding`
- `suggested_action`
- `examples`
- `notes`

Known `severity` values:

- `info`
- `warning`
- `medium`
- `high`

### `output/short_history_observation_pool.csv`

Current file role: watchlist for ETFs blocked by short or very short history. It reads existing diagnosis, candidate gate, metadata, and trading-calendar files. It must not refresh cache, change strategy outputs, replace `compare_signal`, modify backtest returns, or relax QA.

Required:

- `symbol`
- `name`
- `category`
- `sub_category`
- `row_count`
- `min_required_rows`
- `rows_needed`
- `first_date`
- `last_date`
- `latest_expected_date`
- `end_date_gap_days`
- `history_status`
- `liquidity_status`
- `observation_status`
- `observation_priority`
- `estimated_trading_days_until_eligible`
- `estimated_calendar_date_until_eligible`
- `requires_manual_review`
- `manual_review_reason`
- `low_liquidity_flag`
- `abnormal_return_flag`
- `candidate_status`
- `recommended_action`
- `notes`

Known `observation_status` values:

- `waiting_for_history`
- `very_short_history`
- `waiting_but_low_liquidity`
- `manual_review_required`
- `unknown`

Known `observation_priority` values:

- `P0_manual_review`
- `P1_wait_for_history`
- `P2_low_liquidity_watch`
- `P3_archive_watch`

`rows_needed = max(min_required_rows - row_count, 0)`. `estimated_calendar_date_until_eligible` is based on future open days in `data/calendar/a_share_trading_calendar.csv`; it is `unknown` when the calendar snapshot does not reach far enough. This estimate is a tracking aid, not candidate eligibility.

### `output/short_history_observation_summary.csv`

Current file role: compact aggregation of the short-history observation pool for QA and handoff review.

Required:

- `summary_item`
- `count`
- `ratio`
- `severity`
- `examples`
- `suggested_action`
- `notes`

Known `severity` values:

- `info`
- `medium`
- `high`

### `output/manual_review_list.csv`

Current file role: report-only list of ETFs with `requires_manual_review=True`. It explains why human confirmation is needed, which evidence should be checked, and what candidate-handling directions may follow. It must not refresh cache, clear `manual_review_required`, change candidate status, modify strategy outputs, replace `compare_signal`, alter backtest returns, or relax QA.

Required:

- `symbol`
- `name`
- `category`
- `sub_category`
- `review_priority`
- `review_status`
- `manual_review_reason`
- `primary_failure_type`
- `secondary_failure_type`
- `history_status`
- `row_count`
- `min_required_rows`
- `rows_needed`
- `first_date`
- `last_date`
- `latest_expected_date`
- `end_date_gap_days`
- `liquidity_status`
- `abnormal_return_flag`
- `low_liquidity_flag`
- `missing_cache_flag`
- `cache_status`
- `candidate_status`
- `observation_status`
- `evidence_fields`
- `recommended_checks`
- `possible_outcomes`
- `recommended_action`
- `notes`

Known `review_priority` values:

- `P0_manual_review`
- `P1_data_watch`
- `P2_metadata_check`

Known `review_status` values:

- `pending_manual_review`
- `evidence_incomplete`
- `ready_for_review`
- `blocked_until_review`
- `unknown`

`possible_outcomes` is a semicolon-separated list of handling directions, not a decision. Current directions include `keep_blocked`, `observe_until_history_sufficient`, `investigate_source_data`, `exclude_from_candidate_pool`, and `refresh_after_manual_confirmation`.

### `output/manual_review_summary.csv`

Current file role: compact aggregation of the manual-review list for QA and handoff review.

Required:

- `review_item`
- `count`
- `ratio`
- `severity`
- `examples`
- `suggested_action`
- `notes`

Known `severity` values:

- `info`
- `medium`
- `high`

### `output/data_governance_status.json`

Current file role: machine-readable status overview for ETF-GAP-DQ data governance. It is generated by `python main.py summarize-data-governance` and by `qa-check`. It only reads existing outputs and must not refresh cache, change strategy outputs, replace `compare_signal`, alter backtest returns, modify UI behavior, clear blockers, or relax QA.

Required fields:

- `generated_at`
- `qa_exit_status`
- `data_quality_failed_count`
- `end_date_coverage_gap_days`
- `candidate_total`
- `candidate_eligible_count`
- `candidate_blocked_count`
- `blocked_short_history_count`
- `blocked_manual_review_count`
- `blocked_no_used_factors_count`
- `observation_pool_count`
- `very_short_history_count`
- `estimated_eligible_within_20d_count`
- `estimated_eligible_within_60d_count`
- `manual_review_count`
- `factor_gate_status`
- `allowed_to_enter_008b`
- `allowed_to_enter_007b`
- `next_recommended_action`
- `blocking_reasons`
- `report_paths`

Optional QA-status fields:

- `qa_status`
- `governed_failures`
- `actionable_failures`
- `next_refresh_action`
- `next_manual_review_action`

Optional candidate-unblock fields:

- `candidate_unblock_status`
- `immediate_eligible_count`
- `estimated_unblockable_by_waiting_count`
- `candidate_next_action`

Optional factor-008B readiness fields:

- `factor_008b_readiness_status`
- `factor_008b_blockers`
- `factor_008b_next_action`

Optional index-007B readiness fields:

- `index_007b_readiness_status`
- `allowed_to_enter_007b_scope`
- `index_007b_full_scope_available`
- `index_007b_blockers`
- `index_007b_next_action`

Optional ETF-007B metrics fields:

- `etf_007b_status`
- `etf_007b_scope`
- `etf_007b_computable_count`
- `etf_007b_full_scope_available`
- `etf_007b_next_action`

Allowed `qa_exit_status` values:

- `passed`
- `failed`

`allowed_to_enter_008b` is false unless QA, candidate gate, manual review, short-history blockers, no-used-factors blockers, and factor score gate are all clean.

`allowed_to_enter_007b` is false when benchmark/index evidence is not usable, including `usable_benchmark_count == 0`. When true with `allowed_to_enter_007b_scope=small_scope`, it authorizes only the independent 007B research report for computed-valid rows, not full-market use.

### `output/adjustment_audit.csv`

Required:

- `symbol`, `name`, `source`, `adjust`, `download_method`, `fallback_used`, `cache_file`, `start_date`, `end_date`, `row_count`, `abnormal_return_count`, `max_abs_return`, `max_return_date`, `possible_adjustment_issue`, `audit_status`, `audit_reason`

Important status values:

- `ok`
- `warning_unknown_adjustment`
- `warning_fallback_used`
- `warning_abnormal_return`
- `error_missing_adjustment`
- `error_mixed_adjustment`
- `unknown`

### `output/cache_metadata_audit.csv`

Required:

- `symbol`, `name`, `cache_file`, `metadata_file`, `metadata_exists`, `source`, `adjust`, `api_name`, `download_method`, `fallback_used`, `downloaded_at`, `row_count`, `status`, `reason`

Important status values:

- `ok`
- `warning_legacy_cache_without_metadata`
- `warning_metadata_cache_mismatch`
- `warning_unknown_adjustment`
- `error_missing_cache`
- `unknown`

### `output/trading_calendar_audit.csv`

Required:

- `calendar_file`, `exists`, `source`, `start_date`, `end_date`, `row_count`, `open_day_count`, `latest_open_day`, `today`, `coverage_gap_days`, `used_fallback`, `status`, `reason`

Important status values:

- `ok`
- `warning_calendar_stale`
- `warning_using_akshare_runtime`
- `warning_weekday_fallback`
- `error_missing_calendar`
- `error_invalid_calendar`
- `unknown`

### `output/compare_signal.csv`

Current file role: ranking output for the main signal strategy.

Required:

- `symbol`
- `name`
- `latest_date`
- `score`
- `rank`
- `final_signal`

Optional:

- `exchange`
- `asset_class`
- `category`
- `tracking_index`
- `momentum_20`
- `momentum_60`
- `momentum_120`
- `volatility_20`
- `max_drawdown_60`

### `output/strategy_compare_signal.csv`

Current file role: per-strategy signal summary consumed by the Streamlit UI.

Required:

- `strategy_name`
- `strategy_status`
- `effective_signal_date`
- `latest_data_date`
- `target_symbols`
- `suggested_buy`
- `suggested_sell`
- `rank_table`

Additional current fields include execution dates, trade plans, cash fields, JSON-encoded plan tables, and risk notes. UI readers should use field-existence checks before optional display logic.

### `output/performance.json`

Required core keys when generated:

- `total_return`
- `annual_return`
- `max_drawdown`
- `trade_count`
- `start_date`
- `end_date`
- `final_equity`
- `effective_etf_count`
- `min_effective_etf_count`

Common optional keys:

- `sharpe_ratio`, `sharpe`
- `calmar_ratio`, `calmar`
- `win_rate`
- `annual_turnover`
- `yearly_turnover`
- `current_holdings`
- `last_rebalance_date`
- `is_complete_backtest`
- `warning`
- `data_quality_warning`
- `test_only`

### `output/cache_refresh_plan.csv`

Current file role: dry-run plan for future legacy cache refresh batches. It is generated by `python main.py plan-cache-refresh` and by `qa-check`. It must not imply that a real refresh has been executed.

Required:

- `symbol`
- `name`
- `source`
- `current_adjust`
- `cache_file`
- `metadata_file`
- `cache_exists`
- `metadata_exists`
- `latest_cache_date`
- `latest_expected_date`
- `end_date_gap_days`
- `quality_failed`
- `primary_failure_type`
- `adjustment_audit_status`
- `possible_adjustment_issue`
- `refresh_reason`
- `refresh_priority`
- `recommended_action`
- `requires_backup`
- `requires_manual_review`
- `safe_to_auto_refresh`
- `notes`

Known `refresh_reason` values:

- `legacy_cache_without_metadata`
- `unknown_adjustment`
- `possible_adjustment_issue`
- `missing_cache`
- `stale_end_date`
- `data_quality_failed`
- `download_failed`

Known `refresh_priority` values:

- `P0_missing_cache`
- `P0_stale_end_date`
- `P0_quality_failed`
- `P1_legacy_unknown_adjustment`
- `P1_possible_adjustment_issue`
- `P2_optional_refresh`

Boolean-like fields:

- `cache_exists`
- `metadata_exists`
- `quality_failed`
- `possible_adjustment_issue`
- `requires_backup`
- `requires_manual_review`
- `safe_to_auto_refresh`

`safe_to_auto_refresh=True` only means a symbol may be eligible for a future controlled batch after backup and comparison support exists. It does not authorize this command to overwrite cache files.

### `output/pilot_refresh_report.csv`

Current file role: per-symbol report for a small pilot refresh run. It is generated by `python main.py pilot-refresh ...`.

Required:

- `run_id`
- `symbol`
- `name`
- `refresh_attempted`
- `refresh_skipped`
- `skip_reason`
- `backup_created`
- `old_cache_exists`
- `old_metadata_exists`
- `new_cache_exists`
- `new_metadata_exists`
- `old_start_date`
- `old_end_date`
- `new_start_date`
- `new_end_date`
- `old_row_count`
- `new_row_count`
- `end_date_improved`
- `row_count_delta`
- `max_abs_close_diff`
- `abnormal_return_before`
- `abnormal_return_after`
- `old_adjust`
- `new_adjust`
- `metadata_written`
- `refresh_status`
- `refresh_reason`
- `requires_manual_review`
- `notes`

Known `refresh_status` values:

- `refreshed_ok`
- `skipped_manual_review`
- `skipped_not_in_plan`
- `skipped_over_limit`
- `download_failed`
- `compare_failed`
- `metadata_missing_after_refresh`
- `unknown`

Boolean-like fields:

- `refresh_attempted`
- `refresh_skipped`
- `backup_created`
- `old_cache_exists`
- `old_metadata_exists`
- `new_cache_exists`
- `new_metadata_exists`
- `end_date_improved`
- `metadata_written`
- `requires_manual_review`

### `data/cache_backup/*/refresh_manifest.json`

Current file role: backup manifest for a real pilot refresh run.

Required fields:

- `run_id`: timestamped id such as `pilot_20260514_153000`
- `created_at`: ISO-like timestamp
- `symbols`: attempted symbols
- `backup_files`: copied cache and metadata file records
- `command`: command string that launched the run
- `notes`: free-form run note

Each `backup_files` item should include:

- `symbol`
- `kind`: `cache` or `metadata`
- `source`
- `backup`
- `exists`
- `copied`

### `output/missing_cache_repair_report.csv`

Current file role: per-symbol report for targeted `P0_missing_cache` repair runs. It is generated by `python main.py repair-missing-cache`.

Required:

- `run_id`
- `symbol`
- `name`
- `repair_attempted`
- `repair_skipped`
- `skip_reason`
- `old_cache_exists`
- `old_metadata_exists`
- `backup_created`
- `new_cache_exists`
- `new_metadata_exists`
- `new_start_date`
- `new_end_date`
- `new_row_count`
- `new_source`
- `new_adjust`
- `download_method`
- `fallback_used`
- `fallback_chain`
- `repair_status`
- `failure_reason`
- `metadata_written`
- `quality_after_repair`
- `still_missing_cache`
- `requires_manual_review`
- `notes`

Known `repair_status` values:

- `repaired_ok`
- `download_failed`
- `skipped_existing_cache`
- `skipped_not_missing_cache`
- `metadata_missing_after_repair`
- `quality_failed_after_repair`
- `unknown`

Boolean-like fields:

- `repair_attempted`
- `repair_skipped`
- `old_cache_exists`
- `old_metadata_exists`
- `backup_created`
- `new_cache_exists`
- `new_metadata_exists`
- `fallback_used`
- `metadata_written`
- `still_missing_cache`
- `requires_manual_review`

## F. `qa_report.json` Schema

Top-level required fields:

- `schema_version`
- `data_schema_version`
- `data_layer`
- `strategy_layer`
- `output_layer`
- `allow_small_observation`
- `blocking_reasons`
- `recommended_for_observation`
- `not_recommended`
- `defensive_only`
- `risk_note`

Legacy reports may not have `schema_version` or `data_schema_version`; readers should treat missing values as pre-ETF-GAP-001.

`data_layer` required fields:

- `passed`
- `effective_etf_count`
- `latest_date`
- `reasons`
- `coverage_report`
- `quality_report`
- `trading_calendar_report`
- `trading_calendar`
- `failure_summary_report`
- `failure_summary`
- `cache_metadata_audit_report`
- `cache_metadata_audit`
- `adjustment_audit_report`
- `adjustment_audit`

`failure_summary` summary required fields:

- `total_failed`
- `failure_type_counts`
- `severe_failed`
- `warning_failed`
- `top_examples`

`adjustment_audit` summary required fields:

- `total_checked`
- `unknown_adjustment_count`
- `fallback_used_count`
- `abnormal_return_symbols`
- `possible_adjustment_issue_count`
- `top_examples`

`cache_metadata_audit` summary required fields:

- `total_cache_files`
- `metadata_exists_count`
- `legacy_cache_without_metadata_count`
- `unknown_adjustment_count`
- `metadata_cache_mismatch_count`
- `top_examples`

`trading_calendar` summary required fields:

- `calendar_file`
- `status`
- `source`
- `start_date`
- `end_date`
- `latest_open_day`
- `coverage_gap_days`
- `used_fallback`
- `reason`

`cache_refresh_plan` summary fields in ETF-GAP-003C and later reports:

- `total_candidates`
- `priority_counts`
- `reason_counts`
- `safe_to_auto_refresh_count`
- `manual_review_required_count`
- `top_examples`

`pilot_refresh` summary fields in ETF-GAP-003D and later reports:

- `status`
- `report`
- `last_run_id`
- `attempted_count`
- `refreshed_ok_count`
- `skipped_count`
- `failed_count`
- `metadata_written_count`
- `end_date_improved_count`
- `top_examples`

If no pilot refresh has run, `status` should be `not_run` and counts should be zero.

`missing_cache_repair` summary fields in ETF-GAP-003E and later reports:

- `status`
- `report`
- `last_run_id`
- `attempted_count`
- `repaired_ok_count`
- `download_failed_count`
- `still_missing_cache_count`
- `metadata_written_count`
- `quality_failed_after_repair_count`
- `top_examples`

If no missing-cache repair has run, `status` should be `not_run` and counts should be zero.

`observation_pool` summary fields in ETF-GAP-DQ-003 and later reports:

- `total_observation_count`
- `very_short_history_count`
- `low_liquidity_watch_count`
- `manual_review_required_count`
- `estimated_eligible_within_20d_count`
- `estimated_eligible_within_60d_count`
- `unknown_estimate_count`
- `observation_status_counts`
- `observation_priority_counts`
- `top_examples`

The data layer also records:

- `short_history_observation_pool_report`
- `short_history_observation_summary_report`

These fields are observation-only. They must not alter existing QA hard gates or strategy-layer eligibility.

`manual_review` summary fields in ETF-GAP-DQ-004 and later reports:

- `manual_review_count`
- `p0_manual_review_count`
- `abnormal_return_review_count`
- `low_liquidity_review_count`
- `very_short_history_review_count`
- `review_status_counts`
- `review_priority_counts`
- `top_examples`

The data layer also records:

- `manual_review_list_report`
- `manual_review_summary_report`

These fields are report-only. They must not clear `manual_review_required`, change candidate-gate status, or weaken QA hard gates.

`data_governance` summary fields in ETF-GAP-DQ-005 and later reports:

- `data_governance_runbook`
- `data_governance_status_report`
- `allowed_to_enter_008b`
- `allowed_to_enter_007b`
- `next_recommended_action`
- `blocking_reasons`

The same fields may also be duplicated directly under `data_layer` for simple readers. They are summary flags only and do not change QA pass/fail semantics.

`qa_status` summary fields in ETF-GAP-QA-001 and later reports:

- `qa_status_breakdown_report`
- `qa_status_summary_report`
- `hard_failure_count`
- `governed_failure_count`
- `refresh_action_count`
- `wait_for_history_count`
- `manual_review_action_count`
- `blocks_007b`
- `blocks_008b`
- `next_recommended_action`

The same fields may also be duplicated directly under `data_layer`. They explain remediation paths and stage blockers; they must not change `qa-check` exit-code behavior.

`index_007b_readiness` summary fields after `check-index-007b-readiness` or `qa-check`:

- `index_007b_readiness_report`
- `index_007b_unlock_plan_report`
- `index_007b_readiness_summary_report`
- `readiness_status`
- `allowed_to_enter_007b`
- `blocking_items`
- `warning_items`
- `usable_benchmark_count`
- `index_cache_valid_count`
- `tracking_error_computable_count`
- `relative_return_computable_count`
- `top_blockers`
- `next_recommended_action`

These fields live under `data_layer`. They are precheck outputs only and must not calculate real `tracking_error` or `relative_return`.

`candidate_unblock` summary fields in ETF-GAP-CAND-001 and later reports:

- `candidate_unblock_plan_report`
- `candidate_unblock_summary_report`
- `total_symbols`
- `wait_for_history_count`
- `manual_review_required_count`
- `no_used_factors_count`
- `factor_gate_blocked_count`
- `benchmark_dependency_missing_count`
- `estimated_unblockable_by_waiting_count`
- `immediate_eligible_count`
- `top_examples`
- `next_recommended_action`

These fields live under `strategy_layer`. They are a planning layer only and must not change candidate eligibility, factor scores, formal strategy output, or QA pass/fail semantics.

`factor_score` may include ETF-GAP-008B readiness fields after `check-factor-008b-readiness` or `qa-check`:

- `factor_008b_readiness_report`
- `factor_008b_readiness_summary_report`
- `readiness_status`
- `allowed_to_enter_008b`
- `blocking_items`
- `warning_items`
- `top_blockers`
- `next_recommended_action`

These fields are precheck outputs only. They must not trigger candidate generation or alter existing strategy behavior.

`strategy_layer` required fields:

- `passed`
- `checks`
- `reasons`

`output_layer` required fields:

- `passed`
- `reasons`
- `required_files`

Pass/fail and exit-code semantics:

- `data_layer.passed=false` is a hard QA failure.
- `strategy_layer.passed=false` is a hard QA failure.
- `output_layer.passed=false` is a hard QA failure.
- `main.py qa-check` exits with code `1` when `allow_small_observation=false`.
- Existing hard data failures, such as `data quality failed for 244 ETF(s)` and `ETF end-date coverage gap is 14 days`, remain hard failures.
- Warnings such as legacy cache without metadata, unknown adjustment, fallback source usage, abnormal return, or stale calendar must be represented in audit summaries and may contribute to hard failure only through the existing QA gate logic.

## G. Field Compatibility Strategy

Adding fields:

- Additive fields are allowed.
- New readers must tolerate unknown fields.
- New writers should keep existing required fields unless a migration is explicitly planned.

Deleting fields:

- Required fields must not be deleted in a normal feature change.
- Optional fields should be removed only after confirming UI, reports, and tests do not use them.

Renaming fields:

- Renames require compatibility aliases or dual-write.
- The old field should remain available until downstream readers and tests have migrated.

UI parser rules:

- Check field existence before accessing optional columns.
- Missing required fields should produce an explicit error, not an empty dashboard or silent fallback.
- `compare_signal.csv` and `strategy_compare_signal.csv` have different roles; parsers must distinguish ranking rows from strategy summary rows.
- JSON-in-CSV fields such as `rank_table`, `buy_plan`, and `sell_plan` must be parsed defensively.

Version use:

- `schema_version` protects the container/report shape.
- `data_schema_version` protects price cache and data-layer meanings.
- `cache_schema_version` protects metadata sidecar shape.
- A reader seeing an unknown major version should fail loudly or enter read-only diagnostic mode.

## Implementation Hooks

The lightweight definitions and validators live in `data/schema.py`. They intentionally use plain Python constants and small functions instead of pydantic or marshmallow.

Current test coverage is in `tests/test_output_schema.py` and validates:

- price cache CSV required fields and parseability;
- cache metadata sidecar schema with fixture coverage even when no current sidecars exist;
- trading calendar snapshot and audit schema;
- failure summary, adjustment audit, cache metadata audit, trading calendar audit, `qa_report.json`, `compare_signal.csv`, `strategy_compare_signal.csv`, and `performance.json` when present.

Future schema extensions should cover ETF master data, index data, tracking error, premium/discount, fee fields, fund company fields, and multi-factor research reports once those outputs exist.

### `output/source_preference_audit.csv`

Current file role: audit-only comparison of Sina, EM qfq, and EM none history sources. It is generated by `python main.py eval-source-preference` and must not imply a formal cache refresh or source-priority switch.

Required core fields:

- `symbol`
- `source_candidate`
- `adjust`
- `fetch_success`
- `row_count`
- `end_date`
- `quality_passed`
- `preferred_candidate`
- `safe_to_promote`
- `requires_manual_review`

Full audit fields include source metadata, date coverage, row count, latest expected date, end-date gap, missing required columns, abnormal return count, duplicate dates, missing values, zero amount days, schema/quality status, overlap days versus Sina, maximum close difference versus Sina, maximum return difference versus Sina, preference reason, and notes.

Known `source_candidate` values:

- `sina_unknown`
- `em_qfq`
- `em_none`

`safe_to_promote=True` means EM qfq is a candidate for a future controlled source-priority change for that symbol. It does not modify the current downloader. `requires_manual_review=True` means no automatic promotion should happen until overlap differences, row-count gaps, freshness gaps, or quality warnings are explained.

`qa_report.json` may include `data_layer.source_preference_audit_report` and `data_layer.source_preference_audit`. If no source evaluation has run, the summary should be parseable with `status=not_run` and zero counts.

### `output/source_diagnostics_report.csv`

Current file role: diagnostic-only connectivity report for Sina, AKShare EM qfq/none, raw EastMoney endpoint reachability, and proxy environment. It is generated by `python main.py diagnose-source` and must not imply a formal cache refresh or source-priority switch.

Required columns:

- `run_id`
- `checked_at`
- `symbol`
- `check_type`
- `endpoint`
- `proxy_env_detected`
- `http_proxy`
- `https_proxy`
- `akshare_call`
- `adjust`
- `success`
- `status_code`
- `row_count`
- `error_type`
- `error_message`
- `elapsed_ms`
- `retry_count`
- `diagnosis`
- `suggested_action`

Allowed `check_type` values:

- `akshare_em_qfq`
- `akshare_em_none`
- `akshare_sina`
- `raw_endpoint_probe`
- `proxy_env`

Interpretation:

- `akshare_sina` proves whether the current Sina path is still usable.
- `akshare_em_qfq` checks the explicit front-adjusted EM request through AKShare.
- `akshare_em_none` checks whether the same EM family works without adjustment.
- `raw_endpoint_probe` checks the EastMoney kline endpoint directly enough to separate endpoint/proxy reachability from AKShare response normalization.
- `proxy_env` records whether `HTTP_PROXY`, `HTTPS_PROXY`, or related proxy variables are present.

`diagnosis=proxy_or_network_blocked`, `timeout_or_endpoint_slow`, `http_error`, or `akshare_parameter_error` means EM qfq must remain a candidate source only. In that state Sina remains the current primary path, and ETF-GAP-003G must not start.

`qa_report.json` includes:

- `data_layer.source_diagnostics_report`
- `data_layer.source_diagnostics.status`
- `data_layer.source_diagnostics.em_qfq_success_count`
- `data_layer.source_diagnostics.em_none_success_count`
- `data_layer.source_diagnostics.sina_success_count`
- `data_layer.source_diagnostics.proxy_error_count`
- `data_layer.source_diagnostics.timeout_count`
- `data_layer.source_diagnostics.suggested_action`

If diagnostics have not run, the summary is parseable with `status=not_run` and zero counts.

### `output/etf_metadata.csv`

Current file role: ETF master-data table for screening and explanation fields. It is generated by `python main.py update-etf-metadata`. It must not change price cache, strategy scores, backtest returns, or UI behavior.

Required columns:

- `symbol`
- `name`
- `exchange`
- `asset_class`
- `category`
- `sub_category`
- `fund_company`
- `inception_date`
- `tracking_index_name`
- `tracking_index_code`
- `fund_size`
- `fund_size_date`
- `management_fee`
- `custody_fee`
- `latest_amount`
- `latest_price`
- `is_cross_border`
- `is_commodity`
- `is_bond`
- `is_money_market`
- `is_broad_based`
- `is_industry`
- `is_theme`
- `is_dividend`
- `is_sci_tech`
- `is_chinext`
- `inferred_category`
- `inferred_tags`
- `metadata_source`
- `metadata_updated_at`
- `field_completeness`
- `missing_fields`
- `data_quality_status`
- `notes`

Real fields and inferred fields are separate:

- Real fields come only from a source column or another explicit authoritative feed.
- `inferred_category` and `inferred_tags` are name-derived aids for research triage.
- Name-derived tags must not be copied into real fields such as `fund_company`, `tracking_index_code`, `management_fee`, or `custody_fee`.

Missing markers:

- `unknown`: the current source did not provide a confirmed value.
- `missing`: a structurally required source value, such as symbol or name, was absent.
- `unable_to_confirm`: the field may exist in the real world, but the current source does not confirm it.

Current AKShare-backed implementation uses `akshare.fund_etf_spot_em` for `symbol`, `name`, `latest_price`, and `latest_amount`, and `akshare.fund_name_em` when available for `sub_category`. Fund company, inception date, formal tracking index, fund size, management fee, and custody fee are not fabricated.

### `output/etf_metadata_coverage.csv`

Current file role: field-level coverage report for `output/etf_metadata.csv`.

Required columns:

- `field_name`
- `total_count`
- `non_null_count`
- `missing_count`
- `coverage_ratio`
- `source`
- `importance`
- `notes`

Allowed `importance` values:

- `required`
- `recommended`
- `optional`

`coverage_ratio` treats `unknown`, `missing`, `unable_to_confirm`, and blanks as missing. Low coverage in recommended fields is a metadata warning, not an automatic price-data failure.

`qa_report.json` includes:

- `data_layer.etf_metadata_report`
- `data_layer.etf_metadata_coverage_report`
- `data_layer.etf_metadata.status`
- `data_layer.etf_metadata.total_etfs`
- `data_layer.etf_metadata.required_field_coverage`
- `data_layer.etf_metadata.recommended_field_coverage`
- `data_layer.etf_metadata.missing_required_fields`
- `data_layer.etf_metadata.low_coverage_fields`
- `data_layer.etf_metadata.metadata_source`
- `data_layer.etf_metadata.top_examples`

If ETF metadata has not run, the summary is parseable with `status=not_run`, `total_etfs=0`, and empty coverage maps.

### `output/index_map.csv`

Current file role: ETF-to-index benchmark mapping table for ETF-GAP-006. It is generated by `python main.py update-index-data` and must not change ETF price cache, strategy scores, backtest returns, or UI behavior.

Required columns:

- `symbol`
- `etf_name`
- `category`
- `sub_category`
- `tracking_index_name`
- `tracking_index_code`
- `index_source`
- `mapping_method`
- `confidence`
- `requires_manual_review`
- `usable_as_benchmark`
- `notes`

Allowed `mapping_method` values:

- `metadata_exact`: ETF metadata confirmed both the index name and code.
- `config_manual`: `config/index_map.yaml` explicitly supplies a reviewed mapping.
- `name_inferred`: name rules produced a candidate; this defaults to manual review and is not a hard benchmark.
- `unable_to_confirm`: no reliable mapping is available.

`confidence` is a 0-1 score for the mapping. `requires_manual_review=True` means downstream benchmark consumers must skip the mapping until it is reviewed. `usable_as_benchmark=True` requires a confirmed method, a present code, `confidence >= 0.80`, and no manual review requirement.

### `output/index_data_coverage.csv`

Current file role: per-index fetch and quality report for benchmark history.

Required columns:

- `tracking_index_code`
- `tracking_index_name`
- `index_source`
- `api_name`
- `source_family`
- `fetch_success`
- `schema_valid`
- `start_date`
- `end_date`
- `row_count`
- `latest_expected_date`
- `end_date_gap_days`
- `missing_required_columns`
- `missing_values_count`
- `duplicate_dates_count`
- `abnormal_return_count`
- `quality_status`
- `usable_as_benchmark`
- `requires_manual_review`
- `failure_reason`
- `notes`

How to read it:

- `api_name` records the AKShare function that produced the accepted raw frame or the preferred failed candidate.
- `source_family` records the broad source family, such as `csindex` or `eastmoney`.
- `fetch_success=False` means no source returned a nonempty raw history; inspect `failure_reason`.
- `schema_valid=False` means the returned data cannot be written as formal benchmark cache. Missing required columns or missing required values in `date/open/high/low/close/volume/amount` are schema blockers.
- `end_date_gap_days` compares the latest index date with the latest expected A-share trading day.
- `missing_values_count`, `duplicate_dates_count`, and `abnormal_return_count` are data-quality diagnostics.
- `quality_status=failed` prevents benchmark use. `warning` rows are visible but should be reviewed before sensitive analytics.
- `usable_as_benchmark=True` requires `fetch_success=True`, `schema_valid=True`, and `quality_status` of `ok` or `warning`. It also requires the underlying index mapping to be usable.
- `notes` may include fallback evidence such as EastMoney failure counts. These notes are audit evidence only and must not be parsed as market data.

ETF-GAP-006B makes `akshare.stock_zh_index_hist_csindex` the preferred controlled formal path for the current high-confidence benchmark set. EastMoney candidates remain fallbacks because the current environment has observed proxy failures. The current expected CSIndex-writeable set is `000015`, `000300`, `000852`, `000905`, `000932`, and `399975`. `000688`, `931865`, and `399006` must remain non-usable unless the formal run produces schema-valid, quality-acceptable data.

### `output/index_source_diagnostics.csv`

Current file role: diagnostic-only source availability report for index benchmark history. It is generated by `python main.py diagnose-index-source` and must not write `data/index_cache/`, ETF price cache, tracking-error outputs, source-priority changes, strategy scores, backtest returns, or UI changes.

Required columns:

- `run_id`
- `checked_at`
- `index_code`
- `index_name`
- `api_name`
- `source_family`
- `call_success`
- `status_code`
- `row_count`
- `start_date`
- `end_date`
- `latest_expected_date`
- `end_date_gap_days`
- `schema_valid`
- `missing_required_columns`
- `missing_values_count`
- `duplicate_dates_count`
- `abnormal_return_count`
- `failure_type`
- `failure_reason`
- `elapsed_ms`
- `usable_as_index_source`
- `requires_manual_review`
- `suggested_action`
- `notes`

Known `failure_type` values:

- `proxy_error`
- `timeout`
- `http_error`
- `schema_error`
- `empty_data`
- `unknown`

Interpretation:

- `index_map.csv` can confirm which benchmark code belongs to an ETF, but only this diagnostic and `index_data_coverage.csv` can show whether history is actually retrievable.
- `source_family=eastmoney`, `sina`, `tencent`, `csindex`, or `unknown` records the broad source family behind the AKShare function.
- `call_success=True` means the API returned a DataFrame; it does not guarantee the schema is usable.
- `usable_as_index_source=True` requires nonempty, schema-valid, fresh-enough OHLCV data in the current run.
- When every candidate fails, downstream jobs must not compute real tracking error or relative returns. They may only expose guarded skeletons that clearly state benchmark history is unavailable.

`python main.py update-index-data` should be rerun only after a candidate source is usable and reviewed for the target codes. Full ETF-GAP-007 should start only after reviewed mappings and real index cache coverage both exist.

### `data/index_cache/*.csv`

Current file role: normalized index OHLCV history for confirmed benchmarks.

Required columns:

- `date`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `amount`
- `index_code`
- `index_name`
- `source`

Index cache files are separate from ETF price cache files under `data/cache/`. ETF-GAP-006 must not refresh or overwrite ETF cache files.

### `qa_report.json` Index Data Summary

`qa_report.json` includes:

- `data_layer.index_map_report`
- `data_layer.index_data_coverage_report`
- `data_layer.index_data.status`
- `data_layer.index_data.total_index_mappings`
- `data_layer.index_data.index_cache_written_count`
- `data_layer.index_data.usable_benchmark_count`
- `data_layer.index_data.fetch_success_count`
- `data_layer.index_data.fetch_failed_count`
- `data_layer.index_data.csindex_success_count`
- `data_layer.index_data.eastmoney_failure_count`
- `data_layer.index_data.schema_invalid_count`
- `data_layer.index_data.manual_review_required_count`
- `data_layer.index_data.low_coverage_indexes`
- `data_layer.index_data.top_examples`

If index data has not run, the summary is parseable with `status=not_run`, zero counts, and empty examples. Index data is a P1 research capability and does not change existing hard QA gates.

### `qa_report.json` Index Source Diagnostics Summary

`qa_report.json` includes:

- `data_layer.index_source_diagnostics_report`
- `data_layer.index_source_diagnostics.status`
- `data_layer.index_source_diagnostics.index_source_diagnostics_report`
- `data_layer.index_source_diagnostics.total_indexes_checked`
- `data_layer.index_source_diagnostics.total_api_candidates`
- `data_layer.index_source_diagnostics.success_count`
- `data_layer.index_source_diagnostics.usable_source_count`
- `data_layer.index_source_diagnostics.eastmoney_failure_count`
- `data_layer.index_source_diagnostics.proxy_error_count`
- `data_layer.index_source_diagnostics.timeout_count`
- `data_layer.index_source_diagnostics.preferred_api_candidates`
- `data_layer.index_source_diagnostics.top_examples`
- `data_layer.index_source_diagnostics.suggested_action`

If `diagnose-index-source` has not run, the summary is parseable with `status=not_run`, zero counts, and empty examples. This summary is informational and does not relax or tighten existing ETF data QA gates.

### `output/etf_metrics.csv`

Current file role: guarded ETF-specific metric report for ETF-GAP-007A. It is generated by `python main.py compute-etf-metrics` and must not refresh ETF price cache, refresh index cache, change strategy scores, change backtest returns, or update UI behavior.

Required columns:

- `symbol`
- `name`
- `category`
- `sub_category`
- `tracking_index_code`
- `tracking_index_name`
- `benchmark_available`
- `benchmark_status`
- `metric_status`
- `tracking_error`
- `tracking_error_status`
- `relative_return_20d`
- `relative_return_60d`
- `relative_return_120d`
- `benchmark_return_20d`
- `benchmark_return_60d`
- `benchmark_return_120d`
- `etf_return_20d`
- `etf_return_60d`
- `etf_return_120d`
- `discount_premium`
- `discount_premium_status`
- `fund_size`
- `management_fee`
- `custody_fee`
- `latest_amount`
- `computed_at`
- `data_start_date`
- `data_end_date`
- `benchmark_start_date`
- `benchmark_end_date`
- `failure_reason`
- `notes`

Status enum meanings:

- `ok`: the metric is computed from valid dependencies.
- `unable_to_compute`: a dependency or calculation guard prevented output.
- `missing_benchmark`: there is no confirmed, usable benchmark mapping. Name-inferred mappings remain excluded.
- `no_index_cache`: a confirmed mapping exists, but `data/index_cache/{code}.csv` is absent.
- `insufficient_overlap`: ETF and benchmark histories do not share enough return observations.
- `missing_etf_cache`: the ETF price cache file is absent.
- `missing_required_columns`: a cache file is present but lacks required fields.
- `source_unavailable`: the required data source is not present in this layer, such as NAV/IOPV for discount or premium.
- `not_applicable`: the metric does not apply to the instrument or context.
- `unknown`: reserved fallback for defensive parsers.

Benchmark-dependent fields (`tracking_error`, `relative_return_*`, `benchmark_return_*`) must remain blank unless a confirmed benchmark mapping and real index cache are both available. `etf_return_*` is standalone and may be computed from ETF price cache even when benchmark fields are unavailable.

### `output/etf_metrics_coverage.csv`

Current file role: metric-level coverage report for `output/etf_metrics.csv`.

Required columns:

- `metric_name`
- `total_count`
- `computable_count`
- `unable_count`
- `coverage_ratio`
- `main_failure_reason`
- `dependency`
- `importance`
- `notes`

`coverage_ratio = computable_count / total_count`. `main_failure_reason` is the dominant guard status for that metric. `dependency` explains whether the metric needs only ETF cache, confirmed index cache, or a NAV/IOPV source. `importance` is currently `P1` for benchmark metrics and `P2` for standalone or secondary ETF metrics.

### `qa_report.json` ETF Metrics Summary

`qa_report.json` includes:

- `data_layer.etf_metrics_report`
- `data_layer.etf_metrics_coverage_report`
- `data_layer.etf_metrics.status`
- `data_layer.etf_metrics.total_etfs`
- `data_layer.etf_metrics.metrics_computable_count`
- `data_layer.etf_metrics.tracking_error_computable_count`
- `data_layer.etf_metrics.relative_return_computable_count`
- `data_layer.etf_metrics.discount_premium_available_count`
- `data_layer.etf_metrics.no_index_cache_count`
- `data_layer.etf_metrics.missing_benchmark_count`
- `data_layer.etf_metrics.insufficient_overlap_count`
- `data_layer.etf_metrics.source_unavailable_count`
- `data_layer.etf_metrics.top_examples`

If ETF metrics have not run, the summary is parseable with `status=not_run`, zero counts, and empty examples. ETF metrics are a P1/P2 research capability and do not relax the existing ETF price-data hard QA gate.

### `qa_report.json` ETF 007B Metrics Summary

`qa_report.json` may include:

- `data_layer.etf_007b_metrics.etf_007b_metrics_report`
- `data_layer.etf_007b_metrics.etf_007b_metrics_summary_report`
- `data_layer.etf_007b_metrics.total_etfs`
- `data_layer.etf_007b_metrics.computed_valid_count`
- `data_layer.etf_007b_metrics.tracking_error_valid_count`
- `data_layer.etf_007b_metrics.relative_return_valid_count`
- `data_layer.etf_007b_metrics.no_index_cache_count`
- `data_layer.etf_007b_metrics.missing_benchmark_count`
- `data_layer.etf_007b_metrics.insufficient_overlap_count`
- `data_layer.etf_007b_metrics.scope`
- `data_layer.etf_007b_metrics.full_scope_available`
- `data_layer.etf_007b_metrics.top_examples`

This summary is informational. It does not change existing QA hard gates and
does not feed strategy, factor score, candidate gate, backtest, UI, or
`compare_signal`.

### `output/factor_score_report.csv`

Current file role: independent configurable multi-factor score report for ETF-GAP-008. It is generated by `python main.py compute-factor-score`. It must not overwrite ETF cache, index cache, `compare_signal.csv`, backtest outputs, strategy selections, or UI behavior.

Required columns:

- `symbol`
- `name`
- `total_score`
- `score_status`
- `enabled_factor_count`
- `used_factor_count`
- `skipped_factor_count`
- `failed_factor_count`
- `missing_required_factor_count`
- `rank`
- `computed_at`
- `notes`

Known `score_status` values:

- `ok`: at least one enabled factor was used and no required factor is missing.
- `missing_required_factor`: one or more required factors are missing.
- `unable_to_score`: reserved for defensive readers when the score cannot be produced.
- `no_used_factors`: every enabled factor was skipped or unavailable.
- `unknown`: reserved fallback.

`total_score` is a weighted average over used factors only. Skipped optional factors do not contribute zero and do not enter the denominator.

### `output/factor_score_detail.csv`

Current file role: per-symbol, per-factor explanation table for ETF-GAP-008.

Required columns:

- `symbol`
- `name`
- `factor_name`
- `raw_value`
- `normalized_value`
- `weight`
- `direction`
- `weighted_score`
- `factor_status`
- `missing_policy`
- `source`
- `reason`

Known `factor_status` values:

- `used`: the factor had a valid raw value and contributed to score.
- `skipped_missing_optional`: optional factor value was missing and did not contribute.
- `missing_required`: required factor value was missing.
- `disabled`: factor is present in config but disabled.
- `source_unavailable`: configured source or field is unavailable, or the upstream metric status says it cannot be used.
- `invalid_value`: raw value exists but is not numeric or cannot be normalized.
- `insufficient_coverage`: the factor's available coverage is below `min_coverage_required`.
- `unknown`: reserved fallback.

Allowed `direction` values:

- `higher_better`
- `lower_better`

Allowed `missing_policy` values:

- `skip`
- `fail`
- `neutral`

`missing_policy=neutral` must be explicitly configured and should be rare. It must not be used to hide unavailable benchmark or NAV/IOPV data.

### `output/factor_score_audit.csv`

Current file role: factor score coverage, explainability, missing-reason, and bias audit for ETF-GAP-008A.

Required columns:

- `audit_item`
- `status`
- `severity`
- `count`
- `ratio`
- `affected_symbols`
- `finding`
- `suggested_action`
- `notes`

Known `severity` values:

- `info`
- `warning`
- `high`

This file is diagnostic only. It does not create candidates and does not alter strategy selection.

### `output/factor_score_gate.csv`

Current file role: pre-candidate gate for factor score. It is generated by `python main.py compute-factor-score` alongside report/detail/audit. It prevents factor score from being used as candidate-strategy evidence when coverage or dependencies are not ready.

Required columns:

- `gate_item`
- `status`
- `severity`
- `threshold`
- `actual_value`
- `passed`
- `blocking`
- `finding`
- `suggested_action`
- `notes`

Known `gate_item` values include:

- `min_computable_ratio`
- `max_unable_to_score_ratio`
- `no_short_history_bias`
- `no_missing_required_factors`
- `benchmark_dependency_available`
- `nav_iopv_dependency_available`
- `metadata_dependency_available`
- `no_source_unavailable_core_factors`
- `factor_coverage_minimum`

Known `status` values:

- `passed`
- `warning`
- `blocked`
- `not_run`

`passed=false` with `blocking=true` means factor score must remain observation-only and must not be used to generate independent candidates. `no_used_factors` must be interpreted as unscoreable, not as a low score.

### `qa_report.json` Factor Score Summary

`qa_report.json` may include the following under `strategy_layer`:

- `strategy_layer.factor_score_report`
- `strategy_layer.factor_score_detail_report`
- `strategy_layer.factor_score.factor_score_audit_report`
- `strategy_layer.factor_score.factor_score_gate_report`
- `strategy_layer.factor_score.status`
- `strategy_layer.factor_score.total_symbols`
- `strategy_layer.factor_score.score_computable_count`
- `strategy_layer.factor_score.unable_to_score_count`
- `strategy_layer.factor_score.enabled_factor_count`
- `strategy_layer.factor_score.used_factor_counts`
- `strategy_layer.factor_score.skipped_factor_counts`
- `strategy_layer.factor_score.missing_required_factor_count`
- `strategy_layer.factor_score.audit_status`
- `strategy_layer.factor_score.gate_status`
- `strategy_layer.factor_score.blocking_findings`
- `strategy_layer.factor_score.warning_findings`
- `strategy_layer.factor_score.passed_gate_count`
- `strategy_layer.factor_score.failed_gate_count`
- `strategy_layer.factor_score.top_examples`

If factor scoring has not run, the summary is parseable with `status=not_run`, zero counts, and empty examples. Factor scoring and gating are informational in ETF-GAP-008A-FIX and do not relax existing QA hard gates.
# Source Lag Reports

`output/source_lag_report.csv` records single-symbol coverage-gap drivers that are not ordinary full-market refresh tasks.

Required columns:

- `symbol`
- `name`
- `source`
- `cache_end_date`
- `latest_expected_date`
- `end_date_gap_days`
- `market_max_cache_date`
- `gap_vs_market_max_days`
- `sina_end_date`
- `eastmoney_qfq_status`
- `eastmoney_none_status`
- `source_lag_status`
- `blocker_type`
- `can_be_fixed_by_refresh`
- `can_be_fixed_by_waiting`
- `requires_source_diagnosis`
- `exclude_from_candidate_pool`
- `recommended_action`
- `notes`

`output/source_lag_summary.csv` aggregates source-lag blocker counts and suggested source-diagnosis action.

Allowed `source_lag_status` values:

- `source_lag_confirmed`
- `source_unavailable`
- `provider_stale`
- `proxy_blocked`
- `market_wide_lag`
- `unknown`

`qa_report.json -> data_layer.source_lag` and `data_governance_status.json` expose source-lag counts, symbols, coverage-gap drivers, and next source-lag action. These fields explain the blocker; they do not relax QA.
