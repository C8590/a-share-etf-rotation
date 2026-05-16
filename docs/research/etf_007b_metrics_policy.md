# ETF-GAP-007B Metrics Policy

ETF-GAP-007B is currently a small-scope research report. It validates real
tracking error and relative return results already computed in
`output/etf_metrics.csv`. It does not refresh ETF cache, refresh index cache,
change strategy output, replace `compare_signal`, alter backtest returns, update
UI, generate factor candidates, relax QA, or feed `factor_score` /
`candidate_gate`.

## Current Small Scope

The current small-scope 007B set has six ETF/index pairs:

- `159928` 消费ETF汇添富 -> `000932`
- `510300` 沪深300ETF华泰柏瑞 -> `000300`
- `510500` 中证500ETF南方 -> `000905`
- `510880` 红利ETF华泰柏瑞 -> `000015`
- `512100` 中证1000ETF南方 -> `000852`
- `512880` 证券ETF国泰 -> `399975`

These rows may be marked `computed_valid` only when `tracking_error_status=ok`,
`tracking_error` is present, all `relative_return_20d/60d/120d` fields are
present, and the matching `benchmark_return_20d/60d/120d` fields are present.

## Unavailable Rows

Rows with `no_index_cache` remain unavailable. The current high-confidence
benchmark codes still unavailable are:

- `399006`
- `000688`
- `931865`

Rows with `missing_benchmark` remain unavailable because there is no confirmed,
usable ETF-to-index benchmark mapping. The current missing benchmark count is
41. These rows must not receive fake tracking error, fake benchmark returns, or
relative returns copied from ETF standalone returns.

Rows with `insufficient_overlap` remain unavailable until ETF and benchmark
histories have enough aligned observations.

## Discount And Premium

Discount/premium is not part of this 007B scope. It requires NAV, IOPV, or
another reviewed fair-value source. Price-only data cannot produce a trustworthy
discount or premium, so `discount_premium_available_count=0` remains expected.

## Strategy Separation

007B metrics are research evidence only. They must not be connected to:

- factor score inputs;
- candidate gate or candidate construction;
- strategy selection;
- backtest outputs;
- UI behavior;
- `compare_signal`.

Small-scope results must not be promoted to full-market evidence.

## Full-Scope Conditions

Full-scope 007B can open only after all of the following are true:

- every confirmed benchmark dependency has real schema-valid index cache;
- missing benchmark mappings are resolved by trusted metadata or manual config;
- `no_index_cache_count = 0`;
- `missing_benchmark_count = 0`;
- benchmark-relative metric coverage is positive for the intended universe;
- `no_fake_benchmark_guard` remains clean;
- QA remains strict and unchanged.
