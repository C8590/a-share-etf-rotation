# ETF-GAP-008 Factor Score Policy

ETF-GAP-008 introduces a configurable factor scoring skeleton. It is an independent research output, not a replacement for the current strategy ranking, and it does not change backtest returns, strategy selection, UI display, ETF cache, or index cache.

## Why Configure Factors

The current momentum rotation score is hard-coded in strategy code. Configuration makes the scoring recipe easier to audit:

- factors can be enabled or disabled without editing strategy logic;
- weights, directions, and missing-data rules are visible;
- every symbol gets a per-factor explanation;
- future ETF-specific metrics can be added only after their data is trustworthy.

## Missing Data Must Not Enter Score

Missing values are not neutral facts. Filling missing tracking error, fee, fund size, or discount/premium with zero would create false evidence. ETF-GAP-008 therefore scores only `factor_status=used` rows. Optional missing factors are skipped and do not enter the denominator.

## Required And Optional Factors

`required=true` means the ETF cannot receive a valid score if the factor is missing. This should be used sparingly and only for fields with dependable coverage.

`required=false` means the factor can be skipped when unavailable. Current benchmark-relative and NAV/IOPV-dependent fields must be optional until their sources are proven.

## Missing Policy

`missing_policy=skip` is the safe default for optional factors.

`missing_policy=fail` treats a missing value as a scoring blocker.

`missing_policy=neutral` assigns a neutral normalized value only when that behavior is explicitly safe. It must not be used for tracking error, relative return, discount/premium, fund size, or fees while those fields are unconfirmed.

## Current Unavailable Factors

`tracking_error` depends on a confirmed benchmark mapping and schema-valid index cache. Current `usable_benchmark_count=0`, so it must be skipped or marked source unavailable.

`discount_premium` requires NAV or IOPV. Exchange price alone cannot measure premium or discount.

`fund_size` and `management_fee` are present in the schema but current metadata marks them `unable_to_confirm`. They are disabled by default in `config/factor_score.yaml`.

`relative_return_60d` depends on benchmark return. It remains optional and skipped while index cache is empty.

## Reading The Reports

`output/factor_score_report.csv` has one row per ETF. `total_score` is the weighted average of used factors only. `used_factor_count`, `skipped_factor_count`, and `missing_required_factor_count` show whether the score is well-supported.

`output/factor_score_detail.csv` has one row per ETF per configured factor. It explains raw value, normalized value, weighted contribution, status, source, and reason.

`output/factor_score_audit.csv` audits score coverage, missing reasons, unavailable dependencies, and likely bias.

`output/factor_score_gate.csv` is the pre-candidate gate. It decides whether factor score may be used as a basis for a future independent candidate strategy. A failed gate does not change scores; it only keeps the score in observation-report mode.

## Candidate Gate Policy

Factor score must remain an observation report unless the gate is clean enough for candidate research. The default gate requires:

- `computable_ratio >= 0.80`
- `unable_to_score_ratio <= 0.20`
- no full-coverage short-history bias among scoreable symbols
- missing required factor count equals zero
- enabled benchmark-dependent factors are not fully `source_unavailable`
- enabled NAV/IOPV-dependent factors are not fully `source_unavailable`
- enabled metadata-dependent factors have confirmed coverage before use
- no enabled core factor is fully `source_unavailable`
- minimum enabled factor coverage is broad enough, with at least 30 scoreable ETFs before candidate research

`no_used_factors` is not a low score. It means the ETF has no usable enabled factor contribution, so it is outside the scoring evidence set.

The current gate status is expected to be `blocked_for_strategy_use` because coverage is below threshold, all scoreable symbols have short-history risk, and benchmark/NAV-dependent factors are unavailable. This explicitly prevents ETF-GAP-008B from starting from the current score output.

## Future Benchmark Upgrade

After `update-index-data` writes schema-valid index cache and `compute-etf-metrics` produces real `tracking_error`, the factor can be safely enabled as a normal optional or required factor. The config should still keep a coverage threshold and the detail report should show any ETF where benchmark overlap remains insufficient.

## Avoiding Overfitting

Factor weights should not be tuned frequently to chase recent performance. Keep changes small, documented, and tested out of sample. Prefer stable economic reasons, broad coverage, and transparent explanations over short-term score improvements.
