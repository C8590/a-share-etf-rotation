from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .models import RiskEvent, parse_date


DEFAULT_EVENT_YAML = Path("data") / "risk_warning" / "risk_events.yaml"
DEFAULT_EVENT_CSV = Path("data") / "risk_warning" / "risk_events.csv"

EVENT_FIELDS = (
    "event_date",
    "event_type",
    "title",
    "description",
    "source",
    "risk_level",
    "affected_assets",
    "affected_sectors",
    "expected_duration",
    "status",
    "expire_date",
    "manual_confirmed",
    "explain",
)


class RiskEventStore:
    """Read and maintain local manually curated risk events."""

    def __init__(
        self,
        yaml_path: str | Path = DEFAULT_EVENT_YAML,
        csv_path: str | Path = DEFAULT_EVENT_CSV,
    ) -> None:
        self.yaml_path = Path(yaml_path)
        self.csv_path = Path(csv_path)

    def load_events(self) -> list[RiskEvent]:
        events: list[RiskEvent] = []
        seen: set[tuple[str, str, str, str]] = set()
        for row in self._read_yaml_rows():
            event = RiskEvent.from_mapping(row)
            key = (event.event_date.isoformat(), event.event_type, event.title, event.risk_level)
            if key not in seen:
                events.append(event)
                seen.add(key)
        for row in self._read_csv_rows():
            event = RiskEvent.from_mapping(row)
            key = (event.event_date.isoformat(), event.event_type, event.title, event.risk_level)
            if key not in seen:
                events.append(event)
                seen.add(key)
        return events

    def active_events(self, risk_date: str | Any) -> list[RiskEvent]:
        parsed = parse_date(risk_date)
        if parsed is None:
            raise ValueError("risk_date must use YYYY-MM-DD format")
        return [event for event in self.load_events() if event.is_effective_on(parsed)]

    def add_event(self, payload: Mapping[str, Any]) -> RiskEvent:
        event = RiskEvent.from_mapping(payload)
        rows = [item.to_dict() for item in self._read_yaml_events()]
        rows.append(event.to_dict())
        self._write_yaml_rows(rows)
        self._write_csv_rows(rows)
        return event

    def expire_events(self, risk_date: str | Any) -> int:
        parsed = parse_date(risk_date)
        if parsed is None:
            raise ValueError("risk_date must use YYYY-MM-DD format")
        events = self._read_yaml_events()
        changed = 0
        rows: list[dict[str, Any]] = []
        for event in events:
            row = event.to_dict()
            if event.status in {"watch", "active"} and parsed > event.effective_expire_date:
                row["status"] = "expired"
                changed += 1
            rows.append(row)
        self._write_yaml_rows(rows)
        self._write_csv_rows(rows)
        return changed

    def _read_yaml_rows(self) -> list[dict[str, Any]]:
        if not self.yaml_path.exists():
            return []
        raw = yaml.safe_load(self.yaml_path.read_text(encoding="utf-8")) or {}
        rows = raw.get("events", raw) if isinstance(raw, dict) else raw
        return [dict(row) for row in rows or [] if isinstance(row, Mapping)]

    def _read_yaml_events(self) -> list[RiskEvent]:
        return [RiskEvent.from_mapping(row) for row in self._read_yaml_rows()]

    def _read_csv_rows(self) -> list[dict[str, Any]]:
        if not self.csv_path.exists():
            return []
        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [self._decode_csv_row(dict(row)) for row in csv.DictReader(handle)]

    def _write_yaml_rows(self, rows: Iterable[Mapping[str, Any]]) -> None:
        self.yaml_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": [dict(row) for row in rows]}
        self.yaml_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def _write_csv_rows(self, rows: Iterable[Mapping[str, Any]]) -> None:
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(EVENT_FIELDS), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(self._encode_csv_row(row))

    @staticmethod
    def _encode_csv_row(row: Mapping[str, Any]) -> dict[str, Any]:
        encoded = {field: row.get(field, "") for field in EVENT_FIELDS}
        for field in ("affected_assets", "affected_sectors"):
            value = encoded.get(field)
            if isinstance(value, (list, tuple, set)):
                encoded[field] = ",".join(str(item) for item in value)
        return encoded

    @staticmethod
    def _decode_csv_row(row: Mapping[str, Any]) -> dict[str, Any]:
        decoded = dict(row)
        for field in ("affected_assets", "affected_sectors"):
            value = decoded.get(field)
            if isinstance(value, str):
                decoded[field] = [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
        return decoded
