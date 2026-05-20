from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .event_store import RiskEventStore
from .learning_adapter import get_learning_risk_context
from .scorer import calculate_next_day_risk, write_risk_outputs


def add_risk_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("risk", help="管理本地 risk_warning / P0 风险预警")
    risk_subparsers = parser.add_subparsers(dest="risk_command", required=True)

    add_parser = risk_subparsers.add_parser("add", help="手动录入一个风险事件")
    add_parser.add_argument("--file", default=None, help="从 YAML 文件读取 events 列表或单个事件")
    add_parser.add_argument("--event-date", default=None)
    add_parser.add_argument("--event-type", default="other")
    add_parser.add_argument("--title", default="")
    add_parser.add_argument("--description", default="")
    add_parser.add_argument("--source", default="manual")
    add_parser.add_argument("--risk-level", default="R1")
    add_parser.add_argument("--affected-assets", default="")
    add_parser.add_argument("--affected-sectors", default="")
    add_parser.add_argument("--expected-duration", default="unknown")
    add_parser.add_argument("--status", default="watch")
    add_parser.add_argument("--expire-date", default="")
    add_parser.add_argument("--manual-confirmed", action="store_true")
    add_parser.add_argument("--explain", default="")

    list_parser = risk_subparsers.add_parser("list", help="列出本地风险事件")
    list_parser.add_argument("--date", default=None, help="只显示该日期仍生效的事件")

    score_parser = risk_subparsers.add_parser("score", help="计算 next-day risk score 并写出风控文件")
    score_parser.add_argument("--date", default=None, help="风险日期 YYYY-MM-DD，默认今天")

    expire_parser = risk_subparsers.add_parser("expire", help="将已超过失效日期的事件标记为 expired")
    expire_parser.add_argument("--date", default=None, help="检查日期 YYYY-MM-DD，默认今天")


def handle_risk_command(args: argparse.Namespace) -> Any:
    store = RiskEventStore()
    command = str(args.risk_command)
    if command == "add":
        events = _events_from_args(args)
        saved = [store.add_event(event).to_dict() for event in events]
        print(f"已录入 {len(saved)} 个风险事件。")
        return saved
    if command == "list":
        rows = [event.to_dict() for event in (store.active_events(args.date) if args.date else store.load_events())]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return rows
    if command == "score":
        gate = calculate_next_day_risk(args.date, event_store=store)
        write_risk_outputs(gate)
        get_learning_risk_context(gate.risk_date, gate=gate)
        print(json.dumps(gate.to_dict(), ensure_ascii=False, indent=2))
        return gate
    if command == "expire":
        changed = store.expire_events(args.date or date.today().isoformat())
        print(f"已标记过期事件 {changed} 个。")
        return changed
    raise ValueError(f"未知 risk 命令: {command}")


def _events_from_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.file:
        raw = yaml.safe_load(Path(args.file).read_text(encoding="utf-8")) or {}
        events = raw.get("events", raw) if isinstance(raw, dict) else raw
        return [dict(item) for item in events] if isinstance(events, list) else [dict(events)]
    if not args.event_date:
        raise ValueError("risk add 需要 --event-date，或使用 --file 指向 YAML 事件文件")
    return [
        {
            "event_date": args.event_date,
            "event_type": args.event_type,
            "title": args.title,
            "description": args.description,
            "source": args.source,
            "risk_level": args.risk_level,
            "affected_assets": args.affected_assets,
            "affected_sectors": args.affected_sectors,
            "expected_duration": args.expected_duration,
            "status": args.status,
            "expire_date": args.expire_date,
            "manual_confirmed": bool(args.manual_confirmed),
            "explain": args.explain,
        }
    ]
