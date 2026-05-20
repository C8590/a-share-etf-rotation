from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from .qmt_adapter import QmtAdapter
from .qmt_mapping import map_qmt_order_update, map_qmt_position, map_qmt_trade_update


DEFAULT_CONFIG = Path("config/qmt_execution.example.yaml")
LOCAL_CONFIG = Path("config/qmt_execution.local.yaml")
DEFAULT_SNAPSHOT = Path("runtime/qmt_execution/qmt_readonly_snapshot.json")
SNAPSHOT_ROOT = Path("runtime/qmt_execution")


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def resolve_config_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    return LOCAL_CONFIG if LOCAL_CONFIG.exists() else DEFAULT_CONFIG


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


def run_smoke(
    config_path: Path | None = None,
    snapshot_path: Path | None = None,
    adapter_factory: Callable[[dict[str, Any]], Any] = build_adapter,
) -> Path:
    resolved_config_path = resolve_config_path(config_path)
    config = load_config(resolved_config_path)
    if not bool(config.get("enabled", False)):
        raise RuntimeError(f"QMT readonly smoke is disabled by config: {resolved_config_path}")
    if not bool(config.get("read_only", True)):
        raise RuntimeError("readonly smoke requires read_only=true")
    if bool(config.get("qmt_submit_enabled", False)):
        raise RuntimeError("readonly smoke requires qmt_submit_enabled=false")
    if bool(config.get("allow_place_order", False)):
        raise RuntimeError("readonly smoke requires allow_place_order=false")
    if bool(config.get("allow_cancel_order", False)):
        raise RuntimeError("readonly smoke requires allow_cancel_order=false")

    out_path = _resolve_snapshot_path(snapshot_path or Path(str(config.get("snapshot_path", DEFAULT_SNAPSHOT))))
    adapter = adapter_factory(config)

    snapshot: dict[str, Any] = {
        "collect_time": datetime.now().isoformat(timespec="seconds"),
        "broker_name": config.get("broker_name", ""),
        "trading_env": config.get("trading_env", "SIM"),
        "account_type": config.get("account_type", "STOCK"),
        "read_only": True,
        "qmt_submit_enabled": False,
        "account_summary_sample": None,
        "positions_sample": [],
        "orders_sample": [],
        "trades_sample": [],
        "mapping_result": {
            "positions_mapped": 0,
            "orders_mapped": 0,
            "trades_mapped": 0,
        },
        "errors": [],
    }

    try:
        adapter.connect()
        subscribe = getattr(adapter, "subscribe_updates", None)
        if callable(subscribe):
            subscribe(lambda event_type, payload: None)
        account = adapter.get_account()
        positions = adapter.get_positions()
        orders = adapter.get_orders()
        trades = adapter.get_trades()
        mapped_positions = [map_qmt_position(item) for item in positions]
        mapped_orders = [map_qmt_order_update(item) for item in orders]
        mapped_trades = [map_qmt_trade_update(item) for item in trades]
        snapshot["account_summary_sample"] = _serialize(account)
        snapshot["positions_sample"] = [_serialize(item) for item in mapped_positions]
        snapshot["orders_sample"] = [_serialize(item) for item in mapped_orders]
        snapshot["trades_sample"] = [_serialize(item) for item in mapped_trades]
        snapshot["mapping_result"] = {
            "positions_mapped": len(mapped_positions),
            "orders_mapped": len(mapped_orders),
            "trades_mapped": len(mapped_trades),
        }
    except Exception as exc:  # noqa: BLE001
        snapshot["errors"].append(str(exc))
        raise
    finally:
        adapter.disconnect()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _resolve_snapshot_path(path: Path) -> Path:
    root = SNAPSHOT_ROOT.resolve()
    candidate = path.resolve()
    if root != candidate and root not in candidate.parents:
        raise RuntimeError(f"readonly snapshot must be written under {SNAPSHOT_ROOT}")
    return path


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
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--snapshot", type=Path, default=None)
    args = parser.parse_args()

    path = run_smoke(args.config, args.snapshot)
    print(f"readonly snapshot written: {path}")


if __name__ == "__main__":
    main()
