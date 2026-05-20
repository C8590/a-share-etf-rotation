from __future__ import annotations

import time
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import yaml

from risk_warning.event_store import RiskEventStore
from risk_warning.gate import apply_risk_gate
from risk_warning.learning_adapter import get_learning_risk_context
from risk_warning.models import RiskEvent
from risk_warning.scorer import calculate_next_day_risk, write_risk_outputs


def _event(level: str, **overrides: object) -> RiskEvent:
    payload = {
        "event_date": "2026-05-15",
        "event_type": "export_control",
        "title": "出口管制风险升高",
        "description": "外部出口管制相关消息可能影响半导体方向。",
        "source": "manual",
        "risk_level": level,
        "affected_assets": [],
        "affected_sectors": ["半导体", "出口链"],
        "expected_duration": "3d",
        "status": "active",
        "expire_date": "2026-05-20",
        "manual_confirmed": True,
        "explain": "半导体和出口链相关 ETF 可能面临外部扰动，普通新开仓需要暂停并等待人工复核。",
    }
    payload.update(overrides)
    return RiskEvent.from_mapping(payload)


class RiskWarningTest(unittest.TestCase):
    def test_no_event_outputs_r0(self) -> None:
        gate = calculate_next_day_risk("2026-05-16", events=[], output_dir=Path("missing-output"))
        self.assertEqual(gate.risk_level, "R0")
        self.assertFalse(gate.freeze_entry)
        self.assertEqual(gate.equity_cap_override, 1.0)

    def test_r1_event_only_warns(self) -> None:
        gate = calculate_next_day_risk("2026-05-16", events=[_event("R1")], output_dir=Path("missing-output"))
        self.assertEqual(gate.risk_level, "R1")
        self.assertFalse(gate.freeze_entry)
        self.assertFalse(gate.require_manual_review)

    def test_r2_event_reduces_equity_cap(self) -> None:
        gate = calculate_next_day_risk("2026-05-16", events=[_event("R2")], output_dir=Path("missing-output"))
        self.assertEqual(gate.risk_level, "R2")
        self.assertFalse(gate.freeze_entry)
        self.assertEqual(gate.equity_cap_override, 0.60)

    def test_risk_gate_json_has_complete_contract_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            output = Path(tmp)
            gate = calculate_next_day_risk("2026-05-16", events=[], output_dir=output)
            write_risk_outputs(gate, output_dir=output)
            payload = json.loads((output / "risk_gate.json").read_text(encoding="utf-8"))
        expected = {
            "risk_date",
            "risk_score",
            "risk_level",
            "overnight_risk",
            "event_risk",
            "market_fragility",
            "portfolio_exposure",
            "affected_sectors",
            "freeze_entry",
            "equity_cap_override",
            "require_manual_review",
            "manual_takeover_required",
            "explain",
        }
        self.assertTrue(expected.issubset(payload.keys()))
        self.assertIsInstance(payload["affected_sectors"], list)
        self.assertTrue(str(payload["explain"]).strip())

    def test_r2_gate_caps_total_entry_weight_not_each_row_only(self) -> None:
        gate = calculate_next_day_risk("2026-05-16", events=[_event("R2")], output_dir=Path("missing-output")).to_dict()
        rows = [
            {"symbol": "159915", "buy_action": "标准买入", "position_size": 0.5},
            {"symbol": "512480", "buy_action": "加仓买入", "position_size": 0.5},
            {"symbol": "510300", "sell_action": "减仓卖出", "reduce_ratio": 0.5},
        ]
        gated = apply_risk_gate(rows, gate)
        total = sum(float(row.get("position_size", 0) or 0) for row in gated if row.get("buy_action"))
        self.assertLessEqual(total, 0.60)
        self.assertEqual(gated[2]["sell_action"], "减仓卖出")

    def test_r2_gate_caps_total_buy_plan_weight(self) -> None:
        gate = calculate_next_day_risk("2026-05-16", events=[_event("R2")], output_dir=Path("missing-output")).to_dict()
        row = {
            "suggested_buy": "159915,512480",
            "buy_plan": json.dumps(
                [
                    {"ETF代码": "159915", "交易动作": "标准买入", "建议仓位": 0.5},
                    {"ETF代码": "512480", "交易动作": "加仓买入", "建议仓位": 0.5},
                ],
                ensure_ascii=False,
            ),
        }
        gated = apply_risk_gate(row, gate)
        plan = json.loads(gated["buy_plan"])
        total = sum(float(item.get("建议仓位", 0) or 0) for item in plan)
        self.assertLessEqual(total, 0.60)

    def test_r3_event_freezes_entry_and_requires_review(self) -> None:
        gate = calculate_next_day_risk("2026-05-16", events=[_event("R3")], output_dir=Path("missing-output"))
        self.assertEqual(gate.risk_level, "R3")
        self.assertTrue(gate.freeze_entry)
        self.assertTrue(gate.require_manual_review)
        self.assertFalse(gate.manual_takeover_required)

    def test_r4_event_forces_p0_even_when_weighted_score_is_low(self) -> None:
        gate = calculate_next_day_risk("2026-05-16", events=[_event("R4")], output_dir=Path("missing-output"))
        self.assertEqual(gate.risk_level, "R4")
        self.assertGreaterEqual(gate.risk_score, 80)
        self.assertTrue(gate.freeze_entry)
        self.assertTrue(gate.manual_takeover_required)

    def test_expired_event_no_longer_participates(self) -> None:
        event = _event("R4", event_date="2026-05-01", expected_duration="1d", expire_date="")
        gate = calculate_next_day_risk("2026-05-08", events=[event], output_dir=Path("missing-output"))
        self.assertEqual(gate.risk_level, "R0")

    def test_resolved_and_ignored_events_do_not_participate(self) -> None:
        events = [_event("R4", status="resolved"), _event("R3", status="ignored")]
        gate = calculate_next_day_risk("2026-05-16", events=events, output_dir=Path("missing-output"))
        self.assertEqual(gate.risk_level, "R0")

    def test_gate_does_not_block_sell_reduce_stop_or_exit(self) -> None:
        gate = calculate_next_day_risk("2026-05-16", events=[_event("R4")], output_dir=Path("missing-output")).to_dict()
        rows = [
            {"symbol": "159915", "buy_action": "标准买入", "position_size": 0.8},
            {"symbol": "159915", "sell_action": "止损卖出", "reduce_ratio": 1.0},
            {"symbol": "510300", "action": "reduce", "position_size": 0.5},
        ]
        gated = apply_risk_gate(rows, gate)
        self.assertEqual(gated[0]["buy_action"], "P0 风险预警，entry 已冻结，建议人工接管。")
        self.assertEqual(gated[0]["position_size"], 0.0)
        self.assertEqual(gated[1]["sell_action"], "止损卖出")
        self.assertEqual(gated[1]["reduce_ratio"], 1.0)
        self.assertEqual(gated[2]["action"], "reduce")
        self.assertEqual(gated[2]["position_size"], 0.5)

    def test_learning_adapter_writes_risk_context(self) -> None:
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "risk_learning_context.csv"
            gate = calculate_next_day_risk("2026-05-16", events=[_event("R3")], output_dir=Path("missing-output"))
            context = get_learning_risk_context(gate.risk_date, gate=gate, output_path=output)
            self.assertEqual(context["risk_event_active"], "是")
            self.assertEqual(context["risk_level"], "R3")
            self.assertEqual(context["risk_event_type"], "export_control")
            self.assertIn("半导体", context["affected_sectors"])
            self.assertTrue(output.exists())

    def test_explain_is_natural_chinese_without_raw_bool_or_traceback(self) -> None:
        gate = calculate_next_day_risk("2026-05-16", events=[_event("R3", manual_confirmed=False)], output_dir=Path("missing-output"))
        self.assertIn("风险", gate.explain)
        for forbidden in ("true", "false", "None", "NaN", "traceback"):
            self.assertNotIn(forbidden, gate.explain)

    def test_single_day_score_is_fast_and_writes_outputs(self) -> None:
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            started = time.perf_counter()
            gate = calculate_next_day_risk("2026-05-16", events=[_event("R2")], output_dir=output)
            elapsed = time.perf_counter() - started
            write_risk_outputs(gate, output_dir=output)
            self.assertLess(elapsed, 1.0)
            self.assertTrue((output / "risk_gate.json").exists())
            self.assertTrue((output / "risk_warning_next_day.csv").exists())

    def test_event_store_add_list_and_expire(self) -> None:
        with TemporaryDirectory() as tmp:
            store = RiskEventStore(Path(tmp) / "events.yaml", Path(tmp) / "events.csv")
            store.add_event(_event("R2").to_dict())
            self.assertEqual(len(store.load_events()), 1)
            self.assertEqual(len(store.active_events("2026-05-16")), 1)
            expired = store.expire_events("2026-05-25")
            self.assertEqual(expired, 1)
            rows = yaml.safe_load((Path(tmp) / "events.yaml").read_text(encoding="utf-8"))["events"]
            self.assertEqual(rows[-1]["status"], "expired")

    def test_portfolio_overlap_raises_exposure(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            current_position = tmp_path / "current_position.yaml"
            current_position.write_text(
                yaml.safe_dump(
                    {
                        "current_empty": False,
                        "holdings": [{"symbol": "512480", "name": "半导体ETF", "shares": 100}],
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            universe = tmp_path / "universe.yaml"
            universe.write_text(
                yaml.safe_dump({"etfs": [{"symbol": "512480", "name": "半导体ETF", "sector": "半导体"}]}, allow_unicode=True),
                encoding="utf-8",
            )
            gate = calculate_next_day_risk(
                "2026-05-16",
                events=[_event("R2", affected_assets=["512480"])],
                current_position_path=current_position,
                portfolio_path=tmp_path / "missing.csv",
                universe_path=universe,
                output_dir=tmp_path / "output",
            )
            self.assertGreaterEqual(gate.portfolio_exposure, 80)


if __name__ == "__main__":
    unittest.main()
