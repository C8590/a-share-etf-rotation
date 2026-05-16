# ETF-GAP-006 Index Data Policy

ETF research needs an index benchmark layer because ETF returns only become explainable when compared with the exposure the fund is meant to track. This layer prepares future tracking error, relative return, benchmark curves, and peer comparison work. It is not a strategy-scoring change and does not modify ETF price cache, backtest returns, source priority, or UI behavior.

## Name And Code

`tracking_index_name` is the human-readable benchmark name, such as `沪深300` or `中证500`.

`tracking_index_code` is the market data identifier used to fetch index history, such as `000300` or `000905`.

The name is useful for research display and review. The code is operational: a wrong code creates a wrong benchmark curve. Therefore a name alone is not enough to mark an ETF as benchmark-ready.

## Mapping Methods

`metadata_exact` means the ETF metadata layer confirmed both index name and index code from a reliable source.

`config_manual` means `config/index_map.yaml` explicitly records a reviewed mapping. It is allowed as a benchmark only when confidence is high and `requires_manual_review=False`.

`name_inferred` means the candidate was derived from ETF name rules. This can help find likely mappings, but it is not authoritative and defaults to `requires_manual_review=True`.

`unable_to_confirm` means the current data layer cannot safely identify an index benchmark. These rows remain visible for audit but are never used as a hard benchmark.

## Why Name Inference Is Not A Real Code

ETF names can omit the formal index, contain manager branding, use marketing terms, or describe a theme that has multiple possible indices. For example, a name containing `半导体` may not by itself prove the exact CSI index code. Name inference can suggest a candidate, but it must not pretend to be source-confirmed metadata.

## Benchmark Usability

`usable_as_benchmark=True` requires all of the following:

- `mapping_method` is `metadata_exact` or `config_manual`;
- `tracking_index_code` is present and not `unable_to_confirm`;
- `confidence >= 0.80`;
- `requires_manual_review=False`;
- fetched index history passes required-column and missing-value checks.

Low-confidence mappings, name-inferred mappings, and manual-review rows are excluded from benchmark usage even if they contain a candidate code.

## Data Failures

If AKShare or another index source fails, the failure reason is written to `output/index_data_coverage.csv`. No synthetic index rows are created. A failed index fetch is a P1 capability gap, not a price-data QA gate failure.

If a code cannot be confirmed, the row remains `unable_to_confirm`. Downstream tracking-error or relative-return jobs should skip it until a confirmed mapping and valid index cache exist.

## ETF-GAP-006B Controlled CSIndex Path

ETF-GAP-006A showed that the prior formal update path was too dependent on EastMoney: EastMoney candidates failed repeatedly in the current environment, mainly with proxy errors. Sina and Tencent returned some index histories, but their frames lacked either `volume` or `amount`, so they cannot be written to the formal benchmark cache under the current schema.

ETF-GAP-006B therefore connects `akshare.stock_zh_index_hist_csindex` as the preferred formal source for the controlled high-confidence benchmark set. EastMoney remains a fallback candidate and its failures are recorded, but it is no longer the only formal path.

The expected usable CSIndex set from the diagnostic run is:

- `000015`
- `000300`
- `000852`
- `000905`
- `000932`
- `399975`

The following codes remain unavailable unless the current run proves otherwise:

- `399006`: CSIndex path did not return usable data in diagnostics.
- `000688`: CSIndex returned data but required values were missing.
- `931865`: CSIndex returned data but required values were missing.

Formal cache files under `data/index_cache/{index_code}.csv` are written only when the fetched history has all required columns, no missing required values, and acceptable quality status. Missing `volume` or `amount` must not be filled with zero or fabricated estimates, because those fields are part of the auditable cache contract and downstream liquidity or quality checks would treat fabricated values as real market evidence.

`usable_benchmark_count` can be smaller than the high-confidence mapping count. A mapping says which benchmark an ETF should use; coverage says whether real, schema-valid history was actually fetched for that benchmark in this runtime. ETF-GAP-007 should not compute real tracking error, relative return, or benchmark curves until both mapping and cache coverage are present for the target indexes.

## Source Diagnostics

Index mapping success and benchmark history availability are separate checks. `output/index_map.csv` can prove that an ETF should use `000300`, `000905`, or another reviewed code, but it does not prove that the current runtime can fetch clean OHLCV history for that code. ETF-GAP-006A therefore adds `python main.py diagnose-index-source` as a diagnostic-only command.

The command writes `output/index_source_diagnostics.csv` and does not write `data/index_cache/`, ETF price cache, strategy outputs, UI files, or tracking-error artifacts. By default it only checks the current high-confidence benchmark codes, up to 10 indexes, and evaluates multiple lightweight AKShare candidates such as EastMoney, Sina, Tencent, and CSI-family paths when available.

Read the diagnostic report as follows:

- `call_success=False` records transport or API failure, with `failure_type` split into `proxy_error`, `timeout`, `http_error`, `schema_error`, `empty_data`, or `unknown`.
- `schema_valid=False` means the returned frame cannot be used as benchmark cache because required OHLCV/date fields are missing or invalid.
- `end_date_gap_days` compares the candidate's latest date with the latest expected A-share trading day.
- `usable_as_index_source=True` means the candidate returned nonempty, schema-valid, fresh-enough OHLCV data in this run. It is still diagnostic evidence, not an automatic source switch.

It is reasonable to rerun `update-index-data` only after at least one candidate is usable for the needed benchmark codes and the source choice has been reviewed. If every candidate fails, ETF-GAP-007 may only add guarded interfaces or report skeletons. It must not calculate real tracking error, relative returns, or benchmark curves from missing or fabricated index history.

Full ETF-GAP-007 work should start only when both conditions hold: benchmark mappings are confirmed, and real index history has been fetched into `data/index_cache/` through a reviewed source path with coverage recorded in `output/index_data_coverage.csv`.

## Future Use

The index cache under `data/index_cache/` will support:

- tracking error between ETF returns and benchmark returns;
- relative return over rolling windows;
- benchmark curves in reports;
- peer grouping by shared benchmark or category.

Those future features should consume only rows with `usable_as_benchmark=True` and should preserve the mapping audit fields when reporting results.
