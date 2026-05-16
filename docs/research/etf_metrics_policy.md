# ETF-GAP-007A ETF Metrics Policy

ETF-GAP-007A adds guarded ETF metric interfaces and reports. It does not claim full ETF-GAP-007 benchmark analytics are ready. The current environment has confirmed ETF-to-index mappings, but no usable files under `data/index_cache/`, so benchmark-dependent metrics must report explicit unavailable states.

## No Benchmark, No Tracking Error

Tracking error measures the volatility of ETF daily return minus benchmark daily return. Without a confirmed benchmark history, the calculation has no valid second return series. The system must not:

- use ETF price as its own benchmark;
- fill missing index cache with synthetic rows;
- treat `name_inferred` mappings as hard benchmark mappings;
- calculate placeholder tracking error values.

When an ETF has a confirmed mapping but no `data/index_cache/{tracking_index_code}.csv`, the status is `no_index_cache`. When it has no confirmed usable mapping, the status is `missing_benchmark`.

## Tracking Error Preconditions

Tracking error may be computed only when all conditions are true:

- ETF price cache exists and has parseable `date` and `close`;
- `index_map.csv` has a confirmed usable benchmark mapping;
- the matching index cache exists under `data/index_cache/`;
- ETF and benchmark dates can be aligned;
- the aligned daily return observations meet `min_overlap_days`.

If overlap is too small, the status is `insufficient_overlap`.

## Relative Return Preconditions

`relative_return_Nd = etf_return_Nd - benchmark_return_Nd`.

This requires both ETF and benchmark returns over the same aligned date window. If the benchmark return is unavailable, relative return must be blank. It is not equivalent to ETF standalone return.

## Benchmark Return Preconditions

`benchmark_return_Nd` uses the benchmark index cache only. It is blank when index cache is missing or does not contain enough rows.

## ETF Return Is Separate

`etf_return_Nd` can be computed from ETF price cache alone. It is useful as a standalone ETF movement measure, but it does not prove benchmark-relative performance and must not be copied into `relative_return_Nd`.

## Discount And Premium

ETF discount or premium requires NAV, IOPV, or another reviewed fair-value source. Exchange price alone is insufficient because price cannot be compared with itself to infer premium or discount. Until NAV/IOPV data is available, `discount_premium_status` should be `source_unavailable` or `not_applicable`.

## Current Report Interpretation

The current ETF-GAP-007B small-scope state has six ETFs with real
benchmark-relative metrics. Those rows have `tracking_error_status=ok`, real
`tracking_error`, real `benchmark_return_*`, and real `relative_return_*`.

The rest of the universe remains guarded:

- `tracking_error_status=no_index_cache` for ETFs with confirmed mappings but missing or invalid benchmark cache;
- `tracking_error_status=missing_benchmark` for ETFs without confirmed usable mappings;
- blank `relative_return_*` and `benchmark_return_*` where benchmark evidence is unavailable;
- possible `etf_return_*` values where ETF cache is present and long enough;
- `discount_premium_status=source_unavailable` until NAV/IOPV data exists.

This mix is expected behavior. Six computable rows are small-scope research
evidence, not full-market 007B readiness.

## Future Upgrade Path

After a reviewed source can write schema-valid index cache files, rerun:

```powershell
.\.venv\Scripts\python.exe main.py update-index-data
.\.venv\Scripts\python.exe main.py compute-etf-metrics
```

At that point, ETFs with confirmed mappings, valid benchmark cache, and enough overlap can produce real tracking error, benchmark returns, and relative returns. The existing ETF price-data QA gate remains unchanged.
