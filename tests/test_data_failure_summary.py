from __future__ import annotations

import unittest

import pandas as pd

from data.quality import QualityResult, analyze_single_etf, build_data_failure_summary, summarize_failure_summary


def _frame(**overrides: object) -> pd.DataFrame:
    data: dict[str, object] = {
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "open": [10.0, 10.2, 10.3],
        "high": [10.5, 10.6, 10.7],
        "low": [9.9, 10.0, 10.1],
        "close": [10.3, 10.4, 10.5],
        "volume": [1000, 1100, 1200],
        "amount": [10000, 11440, 12600],
        "symbol": ["510300", "510300", "510300"],
        "name": ["ETF A", "ETF A", "ETF A"],
        "source": ["unit-test", "unit-test", "unit-test"],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def _summary_for(result: QualityResult, **kwargs: object) -> list[dict[str, object]]:
    etf_pool = [{"symbol": result.symbol, "name": result.name, "asset_class": "equity", "category": "test"}]
    return build_data_failure_summary(
        etf_pool,
        [result],
        coverage_rows=[
            {
                "symbol": result.symbol,
                "name": result.name,
                "asset_class": "equity",
                "category": "test",
                "source": "unit-test",
                "start_date": result.start_date,
                "end_date": result.end_date,
                "rows": result.rows,
                "success": "True",
            }
        ],
        latest_expected_date=str(kwargs.pop("latest_expected_date", result.end_date or "2024-01-04")),
        min_avg_amount=float(kwargs.pop("min_avg_amount", 0.0)),
        **kwargs,
    )


class DataFailureSummaryTest(unittest.TestCase):
    def assert_has_type(self, summary: list[dict[str, object]], failure_type: str) -> None:
        self.assertIn(failure_type, {str(row["failure_type"]) for row in summary})

    def test_missing_required_columns(self) -> None:
        result = analyze_single_etf("510300", "ETF A", _frame().drop(columns=["close"]), min_rows=1)
        summary = _summary_for(result)
        self.assert_has_type(summary, "missing_required_columns")

    def test_insufficient_rows(self) -> None:
        result = analyze_single_etf("510300", "ETF A", _frame(), min_rows=10)
        summary = _summary_for(result)
        self.assert_has_type(summary, "insufficient_rows")

    def test_stale_end_date(self) -> None:
        result = analyze_single_etf("510300", "ETF A", _frame(), min_rows=1)
        summary = _summary_for(result, latest_expected_date="2024-01-20", max_end_date_gap_days=10)
        self.assert_has_type(summary, "stale_end_date")

    def test_invalid_ohlc(self) -> None:
        result = analyze_single_etf("510300", "ETF A", _frame(high=[10.1, 10.2, 10.3]), min_rows=1)
        summary = _summary_for(result)
        self.assert_has_type(summary, "invalid_ohlc")

    def test_missing_values(self) -> None:
        result = analyze_single_etf("510300", "ETF A", _frame(close=[10.3, None, 10.5]), min_rows=1)
        summary = _summary_for(result)
        self.assert_has_type(summary, "missing_values")

    def test_duplicate_dates(self) -> None:
        result = analyze_single_etf("510300", "ETF A", _frame(date=["2024-01-02", "2024-01-02", "2024-01-04"]), min_rows=1)
        summary = _summary_for(result)
        self.assert_has_type(summary, "duplicate_dates")

    def test_abnormal_return_is_warning(self) -> None:
        result = analyze_single_etf("510300", "ETF A", _frame(close=[10.3, 20.4, 20.5]), min_rows=1)
        summary = _summary_for(result)
        self.assert_has_type(summary, "abnormal_return")
        abnormal = [row for row in summary if row["failure_type"] == "abnormal_return"][0]
        self.assertEqual(abnormal["severity"], "warning")

    def test_zero_or_low_liquidity(self) -> None:
        result = analyze_single_etf("510300", "ETF A", _frame(volume=[1000, 0, 1200]), min_rows=1)
        summary = _summary_for(result)
        self.assert_has_type(summary, "zero_or_low_liquidity")

    def test_unknown_fallback(self) -> None:
        result = QualityResult(
            symbol="510300",
            name="ETF A",
            status="failed",
            rows=3,
            start_date="2024-01-02",
            end_date="2024-01-04",
            missing_count=0,
            duplicate_count=0,
            errors=["mystery validation failure"],
            warnings=[],
            failure_types=["unknown"],
        )
        summary = _summary_for(result)
        self.assert_has_type(summary, "unknown")
        qa_summary = summarize_failure_summary(summary)
        self.assertEqual(qa_summary["total_failed"], 1)


if __name__ == "__main__":
    unittest.main()
