from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class ExecutionLogRecord:
    order_intent_id: str
    submit_time: str
    code: str
    action: str
    quantity: int
    limit_price: Optional[float]
    status: str
    broker_order_id: Optional[str]
    filled_quantity: int
    avg_price: Optional[float]
    error_message: Optional[str]
    risk_check_result: Dict[str, Any]
    manual_confirmed: bool


class ExecutionLogger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: ExecutionLogRecord) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    @staticmethod
    def now() -> str:
        return datetime.now().isoformat(timespec="seconds")
