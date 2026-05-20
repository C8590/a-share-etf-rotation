from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from .qmt_adapter import QmtAdapter


DEFAULT_CONFIG = Path("config/qmt_execution.example.yaml")


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def build_adapter(config: dict[str, Any]) -> QmtAdapter:
    return QmtAdapter(
        userdata_mini_path=str(config.get("miniqmt_user_data_path", "")),
        account_id=str(config.get("account_id", "")),
        session_id=int(config.get("session_id", 1001)),
        trading_env=str(config.get("trading_env", "SIM")),
        qmt_submit_enabled=bool(config.get("qmt_submit_enabled", False)),
        read_only=bool(config.get("read_only", True)),
        allow_place_order=bool(config.get("allow_place_order", False)),
        allow_cancel_order=bool(config.get("allow_cancel_order", False)),
    )


def run_smoke(config_path: Path, snapshot_path: Path | None = None) -> Path:
    config = load_config(config_path)
    if not bool(config.get("enabled", False)):
        raise RuntimeError(f"QMT readonly smoke is disabled by config: {config_path}")
    if not bool(config.get("read_only", True)):
        raise RuntimeError("readonly smoke requires read_only=true")
    if bool(config.get("qmt_submit_enabled", False)):
        raise RuntimeError("readonly smoke requires qmt_submit_enabled=false")
    if bool(config.get("allow_place_order", False)) or bool(config.get("allow_cancel_order", False)):
        raise RuntimeError("readonly smoke requires place/cancel permissions to remain disabled")

    out_path = snapshot_path or Path(str(config.get("snapshot_path", "runtime/qmt_execution/qmt_readonly_snapshot.json")))
    adapter = build_adapter(config)

    snapshot: dict[str, Any] = {
        "broker_name": config.get("broker_name", ""),
        "trading_env": config.get("trading_env", "SIM"),
        "read_only": True,
        "qmt_submit_enabled": False,
    }

    try:
        adapter.connect()
        snapshot["account"] = _serialize(adapter.get_account())
        snapshot["positions"] = [_serialize(item) for item in adapter.get_positions()]
        snapshot["orders"] = [_serialize(item) for item in adapter.get_orders()]
        snapshot["trades"] = [_serialize(item) for item in adapter.get_trades()]
    finally:
        adapter.disconnect()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run readonly QMT account/position/order/trade smoke checks.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--snapshot", type=Path, default=None)
    args = parser.parse_args()

    path = run_smoke(args.config, args.snapshot)
    print(f"readonly snapshot written: {path}")


if __name__ == "__main__":
    main()
