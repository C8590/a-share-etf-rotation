# ETF-GAP-DQ-004 Manual Review Policy

This policy defines the manual-review list for ETF rows with `requires_manual_review=True`. It is a report-only workflow. It does not refresh cache, repair data, clear blockers, change strategy output, replace `compare_signal`, alter backtest returns, modify UI behavior, relax QA gates, delete ETFs, or write synthetic data.

## Why Manual Review Rows Cannot Enter Candidates

Manual-review rows have evidence that may invalidate the price history or the interpretation of that history. Examples include abnormal returns, unknown quality findings, suspicious OHLC behavior, missing fields, duplicate dates, or source/adjustment ambiguity.

Because candidate scoring depends on clean and interpretable data, these rows must remain blocked until a human review conclusion is recorded and the normal QA and candidate gate are rerun.

## Manual Review Versus Ordinary Short History

Ordinary `short_history` means the ETF has too few rows for stable factor evidence. It is usually handled by waiting for more trading history.

`manual_review_required=True` is stronger. It means waiting for more rows is not enough by itself. The suspicious evidence must be reviewed first. A row can be both short-history and manual-review; in that case manual review remains `P0_manual_review`.

## Evidence And Priority

Manual-review rows are reported with `review_priority=P0_manual_review`. The report also records secondary evidence:

- `abnormal_return` requires checking daily return outliers, adjustment state, and source consistency.
- `low_liquidity` requires checking `avg_amount_20` and practical tradability; it is not a low factor score.
- `very_short_history` requires confirming the listing/new-fund state and waiting for the minimum history requirement.
- `unknown` quality findings require inspection of quality errors and warnings before any data action.

These evidence types can coexist. The report lists them in `evidence_fields` and turns them into concrete `recommended_checks`.

## Possible Outcomes

`possible_outcomes` describes candidate-handling directions only. It is not a decision and does not change any gate. Allowed directions include:

- `keep_blocked`
- `observe_until_history_sufficient`
- `investigate_source_data`
- `exclude_from_candidate_pool`
- `refresh_after_manual_confirmation`

Refresh is only a possible future action after manual confirmation. This task does not perform that refresh.

## Why This Task Does Not Clear Blocks

The report is generated from existing evidence. It cannot prove whether an abnormal return is a real market event, an adjustment issue, a source issue, or a bad local row. It also cannot prove that a low-liquidity ETF is tradable for a strategy.

For that reason, the task never sets `manual_review_required=False`, never changes `candidate_status`, and never modifies QA gates. A clean manual conclusion must be recorded separately and followed by the normal diagnosis, observation-pool, and candidate-gate rebuild.

## Recording Future Review Conclusions

Future manual conclusions should be recorded in a separate audit artifact rather than by editing the generated list by hand. A review record should include:

- reviewer and review timestamp
- symbol and name
- evidence checked
- conclusion
- accepted action, such as keep blocked, observe, investigate source, or targeted refresh
- links or notes for supporting evidence

Only after that audit trail exists should a later task decide whether to repair data, refresh a selected cache, or rerun the candidate gate.
