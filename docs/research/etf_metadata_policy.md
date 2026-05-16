# ETF-GAP-005 ETF Metadata Policy

ETF master data is separate from ETF price history. Price history answers whether an ETF can be loaded and backtested. Master data answers what the ETF is, how it should be screened, and which missing descriptive fields still need better sources.

## Why Metadata Matters

ETF screening needs more than OHLCV:

- asset class and category decide whether a fund belongs in an equity, bond, commodity, money-market, or cross-border universe;
- tracking index fields explain what exposure the ETF is supposed to follow;
- fund size, latest amount, and price help with liquidity and practical execution review;
- management and custody fees help explain long-term holding cost;
- tags such as broad-based, industry, theme, dividend, sci-tech, and ChiNext help research grouping.

This layer is governance, not return optimization. It must not change strategy scoring, backtest returns, source priority, or QA thresholds by itself.

## Field Classes

Screening fields:

- `symbol`
- `exchange`
- `asset_class`
- `category`
- `sub_category`
- `latest_amount`
- `is_cross_border`
- `is_commodity`
- `is_bond`
- `is_money_market`
- `is_broad_based`
- `is_industry`
- `is_theme`

Explanation fields:

- `name`
- `fund_company`
- `inception_date`
- `tracking_index_name`
- `tracking_index_code`
- `fund_size`
- `fund_size_date`
- `management_fee`
- `custody_fee`
- `latest_price`
- `is_dividend`
- `is_sci_tech`
- `is_chinext`

Audit fields:

- `inferred_category`
- `inferred_tags`
- `metadata_source`
- `metadata_updated_at`
- `field_completeness`
- `missing_fields`
- `data_quality_status`
- `notes`

## No Fabricated Real Fields

Some fields cannot be safely inferred from the ETF name. Do not fill these from name parsing:

- `fund_company`
- `inception_date`
- `tracking_index_name`
- `tracking_index_code`
- `fund_size`
- `management_fee`
- `custody_fee`

If the current source does not confirm them, keep `unknown`, `missing`, or `unable_to_confirm`. This is noisier, but it is honest and keeps future source upgrades explainable.

## Inferred Fields

`inferred_category` and `inferred_tags` are allowed to use ETF names. They are useful for triage, grouping, and finding likely candidates, but they are not authoritative.

Risks:

- a fund name may omit a real exposure;
- a fund name may contain marketing language rather than index taxonomy;
- a name can suggest a theme without confirming the actual tracking index;
- suffixes and manager names can confuse simple keyword rules.

Therefore inferred fields may guide review, but they must not overwrite real metadata fields.

## Missing Values

Use these markers consistently:

- `unknown`: current source does not provide a confirmed value.
- `missing`: a structurally expected value is absent from the source.
- `unable_to_confirm`: the value may exist, but the current source cannot verify it.

`output/etf_metadata_coverage.csv` treats all three as missing for coverage purposes.

## Fund Size, Fees, And Tracking Index

The current AKShare implementation uses the ETF spot list and fund-name list. That is enough for symbol, name, exchange, latest price, latest amount, and basic fund type, but it is not enough to safely confirm official fund company, inception date, tracking index code, management fee, custody fee, or formal fund scale.

When those fields are missing:

- do not block price-data QA;
- do not remove the ETF from price-history workflows;
- do mark field coverage as low;
- do use the coverage report to prioritize a future authoritative source.

## Future Sources

Future ETF-GAP work can add more authoritative sources, such as exchange ETF product files, fund prospectus/F10 pages, official fund-company data, or a curated local master-data file. Any new source should preserve `metadata_source`, keep before/after coverage comparable, and avoid overwriting source-confirmed fields with name inference.

## QA Relationship

Metadata gaps are warnings for ETF-GAP-005. They are not equivalent to price-history quality failures because a missing management fee or tracking index code does not prove that OHLCV data is unusable. Price QA remains governed by data coverage, source traceability, cache metadata, adjustment audit, and trading-calendar freshness.
