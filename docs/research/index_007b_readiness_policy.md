# ETF-GAP-007B Index Readiness Policy

`index_007b_readiness.csv` is a precheck for ETF-GAP-007B. It does not enter
007B, calculate real `tracking_error`, calculate real `relative_return`, refresh
ETF cache, refresh index cache, alter strategy output, replace `compare_signal`,
change backtest returns, update UI, or relax QA.

## Why 007B Is Currently Blocked

007B requires a real benchmark time series. The current reports show confirmed
ETF-to-index mappings, but `data/index_cache` has no schema-valid benchmark
cache, `usable_benchmark_count = 0`, and ETF metrics coverage has
`tracking_error_computable_count = 0` and `relative_return_computable_count = 0`.

This means benchmark-relative metrics cannot be trusted or computed yet. ETF
standalone returns are not a substitute for benchmark returns.

## Entry Conditions

ETF-GAP-007B can be considered only when all hard conditions are true:

- `usable_benchmark_count > 0`.
- At least one confirmed `data/index_cache/{index_code}.csv` exists.
- At least one confirmed index cache passes schema validation.
- At least one ETF has real ETF/index overlap after `compute-etf-metrics`.
- `tracking_error_computable_count > 0` or `relative_return_computable_count > 0`.
- `no_fake_benchmark_guard` passes with zero violations.

If any of these remain false, 007B stays blocked.

## Benchmark Evidence

`usable_benchmark_count` means a benchmark is confirmed by mapping evidence and
backed by real schema-valid index cache. A mapping row alone is not enough.

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
and positive metric computability after unlock. Other ETFs remain blocked.

Current high-confidence mapping rows with missing cache should be treated as
priority unlock candidates, not as immediately usable benchmarks.

## Pause Conditions

Pause 007B when:

- `data/index_cache` is empty or all cache files are schema-invalid.
- `usable_benchmark_count == 0`.
- `tracking_error_computable_count == 0`.
- `relative_return_computable_count == 0`.
- only `name_inferred` or `unable_to_confirm` mappings are available.
- source failures are network/proxy related and no valid cache exists.
- `no_fake_benchmark_guard` has any violation.

In all pause states, real `tracking_error` and real `relative_return` remain
forbidden.
