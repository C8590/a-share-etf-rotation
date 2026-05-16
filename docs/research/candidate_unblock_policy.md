# Candidate Unblock Policy

`candidate_unblock_plan.csv` is an explanation and planning report. It does not
clear `candidate_gate.csv`, does not produce formal candidates, and does not
change strategy output, backtest output, UI, cache, or QA hard gates.

## Why Candidate Gate Cannot Be Cleared Directly

The current candidate gate has `eligible=0` because every row still has at least
one data-governance or factor-governance blocker. This is an intended protective
state, not a strategy failure. The gate prevents short-history, manual-review,
unscoreable, and factor-gate-blocked rows from entering candidate construction.

## Short History

`blocked_short_history` rows can be reconsidered only after `row_count >=
min_required_rows`. They remain excluded until data-quality diagnosis,
observation pool, and candidate gate are rerun from current reports. Short
history is not a low score and is not a full-market refresh task.

## Manual Review

`blocked_manual_review` rows require a human review conclusion before any
unblock. The review must explain abnormal return, low liquidity, very short
history, source, or adjustment evidence. This report must not auto-clear the
manual-review blocker.

## No Used Factors

`blocked_no_used_factors` means the ETF is unscoreable with current enabled
factor evidence. It must not be interpreted as bearish or low score. It can be
reconsidered only after at least one enabled factor becomes usable and the factor
score report is rerun.

## Factor Gate And Candidate Gate

Candidate gate is row-level. Factor gate is global. Even if a row-level blocker
is fixed, `factor_gate_status=blocked_for_strategy_use` still prevents ETF-GAP
008B and candidate construction from using factor scores.

## Benchmark And Metadata Dependencies

Benchmark-dependent factors require schema-valid index cache and usable
benchmark coverage. If `usable_benchmark_count=0`, ETF-GAP-007B remains blocked
and benchmark-dependent factor evidence remains unavailable. Metadata-dependent
factors require independently confirmed metadata coverage before promotion.

## Rerun Conditions

Rerun `build-candidate-gate` only after upstream reports change: new history
rows, manual review evidence, factor-score reports, factor-score gate, benchmark
cache, or metadata coverage. Rerun `build-candidate-unblock-plan` after
candidate gate or dependency reports change to refresh the route map.

ETF-GAP-008B can be considered only when QA hard gates pass, candidate gate has
eligible rows, no blocking candidate rows remain, manual review is clear, and
factor gate passes for candidate research.
