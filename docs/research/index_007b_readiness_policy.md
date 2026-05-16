# ETF-GAP-007B Index Readiness Policy

`index_007b_readiness.csv` is a precheck for ETF-GAP-007B. It does not enter
007B, calculate real `tracking_error`, calculate real `relative_return`, refresh
ETF cache, refresh index cache, alter strategy output, replace `compare_signal`,
change backtest returns, update UI, or relax QA.

## Current State

007B requires a real benchmark time series. The current reports now show a
small usable benchmark subset:

- `usable_benchmark_count = 6`
- `index_cache_valid_count = 6`
- `tracking_error_computable_count = 6`
- `relative_return_computable_count = 6`

This unlocks small-scope 007B validation only. It does not make the full ETF
universe benchmark-ready, and it does not connect 007B to strategy scoring,
candidate pools, backtests, UI, or `compare_signal`.

Remaining gaps are still real: `399006`, `000688`, and `931865` do not have
usable schema-valid index cache; 41 ETF rows still have `missing_benchmark`; and
`discount_premium_available_count = 0`.

## Entry Conditions

ETF-GAP-007B small-scope can be considered when all hard conditions are true:

- `usable_benchmark_count > 0`.
- At least one confirmed `data/index_cache/{index_code}.csv` exists.
- At least one confirmed index cache passes schema validation.
- At least one ETF has real ETF/index overlap after `compute-etf-metrics`.
- `tracking_error_computable_count > 0` or `relative_return_computable_count > 0`.
- `no_fake_benchmark_guard` passes with zero violations.

If any of these remain false, 007B stays blocked. If these pass while other
confirmed indexes or ETF mappings remain unavailable, readiness status should be
`ready_small_scope`, not full-scope ready.

## Benchmark Evidence

`usable_benchmark_count` is counted from `output/index_data_coverage.csv` rows
where `usable_as_benchmark`, `schema_valid`, and `fetch_success` are all true.
`qa_report.json` is only a fallback when coverage is missing, because QA
summaries can lag behind `update-index-data`.

A mapping row alone is not enough.

Schema-valid index cache means the CSV has required index OHLCV fields, parseable
dates, numeric prices/volume/amount, `index_code`, `index_name`, and `source`,
and the `index_code` matches the cache filename.

`mapping_method=name_inferred` and `unable_to_confirm` are review evidence only.
They must not unlock hard benchmark calculations.

## No Fake Benchmark Rule

ETF prices must never be used as benchmark substitutes. Doing so would make
`tracking_error` and `relative_return` circular: the ETF would be compared to
itself rather than to the tracked index. Missing benchmark data must remain
missing until a real index cache exists.

## Source Roles

CSIndex and EastMoney are index source candidates. They may help populate
`data/index_cache`, but source diagnostics alone do not unlock 007B. A source
must fetch schema-valid data and `update-index-data` must write real cache before
ETF metrics can consume it.

EastMoney proxy/network failures are environment blockers. They should be fixed
in a network/proxy-enabled environment, not bypassed with placeholder cache.

## Network Rerun Order

When network/proxy access is available, use this sequence:

1. `python main.py diagnose-index-source`
2. `python main.py update-index-data`
3. Confirm `data/index_cache/{index_code}.csv` exists and is schema-valid.
4. `python main.py compute-etf-metrics`
5. `python main.py check-index-007b-readiness`

Only after benchmark-relative computable counts become positive may 007B be
considered.

## Small-Scope 007B

If only some indexes become available, 007B may be limited to ETFs whose rows in
`index_007b_unlock_plan.csv` have confirmed mapping, schema-valid index cache,
and positive metric computability. Other ETFs remain blocked from the small
scope and must not receive fake benchmark values.

Current small-scope benchmark codes are `000015`, `000300`, `000852`, `000905`,
`000932`, and `399975`.

Current high-confidence benchmark codes still unavailable are `399006`,
`000688`, and `931865`; they should be treated as priority unlock candidates,
not as immediately usable benchmarks.

## Pause Conditions

Pause all 007B when:

- `data/index_cache` is empty or all cache files are schema-invalid.
- `usable_benchmark_count == 0`.
- `tracking_error_computable_count == 0`.
- `relative_return_computable_count == 0`.
- only `name_inferred` or `unable_to_confirm` mappings are available.
- source failures are network/proxy related and no valid cache exists.
- `no_fake_benchmark_guard` has any violation.

In all pause states, real `tracking_error` and real `relative_return` remain
forbidden.

Pause full-scope 007B, while still allowing small-scope validation, when:

- `partial_index_cache_missing_count > 0`.
- `missing_benchmark_count > 0`.
- any confirmed benchmark code is missing schema-valid index cache.
- `discount_premium_available_count == 0` for NAV/IOPV-dependent work.
