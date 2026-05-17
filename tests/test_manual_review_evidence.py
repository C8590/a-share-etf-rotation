from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
import unittest

import pandas as pd

from data.manual_review_evidence import build_and_write_manual_review_evidence


SYMBOLS = ["159231", "159246", "159287", "159387", "560320"]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _hash_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    parts: list[str] = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            parts.append(f"{item.relative_to(path)}:{hashlib.sha256(item.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _manual_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        very_short = symbol == "560320"
        rows.append(
            {
                "symbol": symbol,
                "name": f"ETF{symbol}",
                "row_count": 1 if very_short else 200,
                "first_date": "2026-05-13" if very_short else "2025-07-10",
                "last_date": "2026-05-13" if very_short else "2026-05-12",
                "history_status": "very_short_history" if very_short else "short_history",
                "abnormal_return_flag": symbol != "560320",
                "low_liquidity_flag": symbol in {"159231", "159287"},
                "manual_review_reason": "unknown_quality_finding;very_short_history" if very_short else "abnormal_return;requires_review",
                "review_status": "blocked_until_review",
                "notes": "manual review report only",
            }
        )
    return rows


def _seed_reports(root: Path) -> None:
    output = root / "output"
    manual = _manual_rows()
    _write_csv(output / "manual_review_list.csv", manual)
    _write_csv(
        output / "data_quality_diagnosis.csv",
        [
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "failure_type": "insufficient_rows;unknown" if row["symbol"] == "560320" else "insufficient_rows;abnormal_return",
                "secondary_failure_type": "insufficient_rows;unknown" if row["symbol"] == "560320" else "insufficient_rows;abnormal_return",
                "row_count": row["row_count"],
                "first_date": row["first_date"],
                "last_date": row["last_date"],
                "history_status": row["history_status"],
                "liquidity_status": "low_liquidity" if row["symbol"] in {"159231", "159287"} else "ok",
                "reason": "too few rows",
            }
            for row in manual
        ],
    )
    _write_csv(
        output / "data_quality_report.csv",
        [
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "status": "failed",
                "warnings": "daily close return exceeds 20% on 1 day(s)" if row["symbol"] != "560320" else "close equals high at an unusually high ratio",
                "failure_types": "abnormal_return" if row["symbol"] != "560320" else "unknown",
            }
            for row in manual
        ],
    )
    _write_csv(
        output / "data_failure_summary.csv",
        [
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "failure_type": "insufficient_rows",
                "failure_reason": "too few rows",
            }
            for row in manual
        ],
    )
    _write_csv(
        output / "short_history_observation_pool.csv",
        [
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "row_count": row["row_count"],
                "first_date": row["first_date"],
                "last_date": row["last_date"],
                "history_status": row["history_status"],
                "observation_status": "manual_review_required",
                "requires_manual_review": True,
                "manual_review_reason": row["manual_review_reason"],
                "low_liquidity_flag": row["low_liquidity_flag"],
                "abnormal_return_flag": row["abnormal_return_flag"],
            }
            for row in manual
        ],
    )
    _write_csv(
        output / "candidate_gate.csv",
        [
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "candidate_status": "blocked_manual_review",
                "eligibility_status": "blocked",
                "block_reason": "manual_review_required;exclude_from_candidate_pool",
                "requires_manual_review": True,
            }
            for row in manual
        ],
    )
    _write_json(output / "qa_report.json", {"qa_exit_status": "failed"})
    for row in manual:
        symbol = str(row["symbol"])
        cache_rows = [
            {"date": "2026-05-11", "close": 1.0, "amount": 0 if symbol == "159231" else 10_000_000},
            {"date": "2026-05-12", "close": 1.25 if symbol != "560320" else 1.0, "amount": 12_000_000},
        ]
        if symbol == "560320":
            cache_rows = [{"date": "2026-05-13", "close": 1.0, "amount": 8_000_000}]
        _write_csv(root / "data" / "cache" / f"{symbol}.csv", cache_rows)


class ManualReviewEvidenceTest(unittest.TestCase):
    def test_all_five_manual_review_etfs_enter_evidence_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_reports(root)
            evidence_path, decision_path, _, _ = build_and_write_manual_review_evidence(project_root=root)
            evidence = pd.read_csv(evidence_path, dtype={"symbol": str})
            decision = pd.read_csv(decision_path, dtype={"symbol": str})
            self.assertEqual(set(evidence["symbol"]), set(SYMBOLS))
            self.assertEqual(set(decision["symbol"]), set(SYMBOLS))

    def test_default_decision_is_keep_blocked_and_no_unblock_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_reports(root)
            _, decision_path, _, _ = build_and_write_manual_review_evidence(project_root=root)
            decision = pd.read_csv(decision_path, dtype={"symbol": str})
            self.assertEqual(set(decision["review_decision"]), {"keep_blocked"})
            self.assertEqual(set(decision["review_status"]), {"blocked_until_review"})
            self.assertFalse(decision["unblock_allowed"].astype(str).str.lower().isin({"true", "1", "yes"}).any())
            self.assertNotIn("eligible", set(decision["review_decision"]))

    def test_very_short_history_and_abnormal_return_do_not_auto_unblock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_reports(root)
            evidence_path, decision_path, _, _ = build_and_write_manual_review_evidence(project_root=root)
            evidence = pd.read_csv(evidence_path, dtype={"symbol": str})
            decision = pd.read_csv(decision_path, dtype={"symbol": str})
            very_short = decision[decision["symbol"].eq("560320")].iloc[0]
            abnormal = evidence[evidence["symbol"].eq("159246")].iloc[0]
            self.assertEqual(very_short["review_decision"], "keep_blocked")
            self.assertFalse(str(very_short["unblock_allowed"]).lower() in {"true", "1", "yes"})
            self.assertTrue(str(abnormal["abnormal_return_flag"]).lower() in {"true", "1", "yes"})
            self.assertEqual(decision[decision["symbol"].eq("159246")].iloc[0]["review_decision"], "keep_blocked")

    def test_does_not_modify_cache_index_cache_compare_or_backtest_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_reports(root)
            protected = [
                root / "data" / "cache",
                root / "data" / "index_cache",
                root / "output" / "compare_signal.csv",
                root / "output" / "compare_signal.txt",
                root / "output" / "equity_curve.csv",
                root / "output" / "performance.json",
            ]
            (root / "data" / "index_cache").mkdir(parents=True, exist_ok=True)
            for path in protected[2:]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("stable", encoding="utf-8")
            before = {path: _hash_path(path) for path in protected}
            build_and_write_manual_review_evidence(project_root=root)
            after = {path: _hash_path(path) for path in protected}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
