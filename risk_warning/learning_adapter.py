from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .event_store import RiskEventStore
from .models import RiskGate
from .scorer import calculate_next_day_risk


LEARNING_OUTPUT = Path("output") / "risk_learning_context.csv"


def get_learning_risk_context(
    risk_date: str,
    *,
    gate: RiskGate | Mapping[str, Any] | None = None,
    event_store: RiskEventStore | None = None,
    output_path: str | Path = LEARNING_OUTPUT,
) -> dict[str, Any]:
    if gate is None:
        gate_payload = calculate_next_day_risk(risk_date, event_store=event_store).to_dict()
    elif isinstance(gate, RiskGate):
        gate_payload = gate.to_dict()
    else:
        gate_payload = dict(gate)

    active_events = gate_payload.get("active_events", []) or []
    event_types = _unique(str(event.get("event_type") or "") for event in active_events if isinstance(event, Mapping))
    sectors = gate_payload.get("affected_sectors", []) or []
    context = {
        "risk_date": gate_payload.get("risk_date", risk_date),
        "risk_event_active": "是" if active_events else "否",
        "risk_level": gate_payload.get("risk_level", "R0"),
        "risk_event_type": "、".join(event_types),
        "affected_sectors": "、".join(str(item) for item in sectors),
        "risk_score": gate_payload.get("risk_score", 0),
        "freeze_entry": "是" if gate_payload.get("freeze_entry") else "否",
        "manual_takeover_required": "是" if gate_payload.get("manual_takeover_required") else "否",
        "explain": gate_payload.get("explain", ""),
    }
    write_learning_risk_context(context, output_path=output_path)
    return context


def write_learning_risk_context(context: Mapping[str, Any], output_path: str | Path = LEARNING_OUTPUT) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([dict(context)])
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _unique(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
