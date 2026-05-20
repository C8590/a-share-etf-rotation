from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .action_schema import now_iso


TASK_STATUSES = {"pending", "running", "success", "failed", "cancelled"}


class TaskQueue:
    def __init__(
        self,
        task_dir: str | Path = Path("output") / "tasks",
        log_path: str | Path = Path("output") / "logs" / "recent_actions.log",
    ) -> None:
        self.task_dir = Path(task_dir)
        self.index_path = self.task_dir / "task_index.json"
        self.log_path = Path(log_path)
        self._lock = threading.RLock()

    def create_task(
        self,
        action_name: str,
        parameters: dict[str, Any] | None = None,
        *,
        created_by: str = "v21_action_api",
        message: str = "任务已创建，等待后台执行。",
    ) -> dict[str, Any]:
        task_id = f"task_{uuid4().hex[:12]}"
        record = {
            "task_id": task_id,
            "action_name": action_name,
            "status": "pending",
            "progress": 0,
            "message": message,
            "start_time": now_iso(),
            "end_time": "",
            "error": "",
            "result_file": "",
            "created_by": created_by,
            "parameters": parameters or {},
        }
        with self._lock:
            self._write_task(record)
            index = self._read_index()
            index = [item for item in index if item.get("task_id") != task_id]
            index.insert(0, self._index_item(record))
            self._write_index(index)
            self.append_log(action_name, "pending", message, task_id=task_id)
        return record

    def enqueue(
        self,
        action_name: str,
        parameters: dict[str, Any] | None = None,
        *,
        runner: Callable[[dict[str, Any], "TaskQueue"], dict[str, Any] | None] | None = None,
        created_by: str = "v21_action_api",
        message: str = "任务已进入后台队列。",
    ) -> dict[str, Any]:
        record = self.create_task(action_name, parameters, created_by=created_by, message=message)
        thread = threading.Thread(
            target=self._run_task,
            args=(record["task_id"], runner),
            name=f"v21-{record['task_id']}",
            daemon=True,
        )
        thread.start()
        return record

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        error: str | None = None,
        result_file: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            record = self.get_task(task_id)
            if not record:
                raise KeyError(f"task not found: {task_id}")
            if status is not None:
                if status not in TASK_STATUSES:
                    raise ValueError(f"unsupported task status: {status}")
                record["status"] = status
                if status in {"success", "failed", "cancelled"}:
                    record["end_time"] = now_iso()
            if progress is not None:
                record["progress"] = max(0, min(100, int(progress)))
            if message is not None:
                record["message"] = str(message)
            if error is not None:
                record["error"] = str(error)
            if result_file is not None:
                record["result_file"] = str(result_file)
            self._write_task(record)
            index = self._read_index()
            index = [self._index_item(record) if item.get("task_id") == task_id else item for item in index]
            self._write_index(index)
            self.append_log(record["action_name"], record["status"], record["message"], task_id=task_id, error=record.get("error", ""))
            return record

    def complete_task(self, task_id: str, message: str = "任务已完成。", result_file: str = "") -> dict[str, Any]:
        return self.update_task(task_id, status="success", progress=100, message=message, result_file=result_file)

    def fail_task(self, task_id: str, error: str, message: str = "任务执行失败。") -> dict[str, Any]:
        return self.update_task(task_id, status="failed", message=message, error=error)

    def list_tasks(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            index = self._read_index()
        return index[:limit] if limit else index

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            path = self.task_dir / f"{task_id}.json"
            if not path.exists():
                return {}
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}

    def failed_tasks(self, limit: int | None = None) -> list[dict[str, Any]]:
        tasks = [item for item in self.list_tasks() if item.get("status") == "failed"]
        return tasks[:limit] if limit else tasks

    def append_log(self, action_name: str, status: str, message: str, *, task_id: str = "", error: str = "") -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": now_iso(),
            "task_id": task_id,
            "action_name": action_name,
            "status": status,
            "message": message,
            "error": error,
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def recent_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").splitlines()[-limit:]
        logs: list[dict[str, Any]] = []
        for line in lines:
            try:
                logs.append(json.loads(line))
            except json.JSONDecodeError:
                logs.append({"timestamp": "", "message": line})
        return logs

    def _run_task(
        self,
        task_id: str,
        runner: Callable[[dict[str, Any], "TaskQueue"], dict[str, Any] | None] | None,
    ) -> None:
        try:
            record = self.update_task(task_id, status="running", progress=5, message="任务正在后台执行。")
            result = runner(record, self) if runner else None
            result_file = str((result or {}).get("result_file") or "")
            message = str((result or {}).get("message") or "任务已完成。")
            self.complete_task(task_id, message=message, result_file=result_file)
        except Exception as exc:  # noqa: BLE001
            self.fail_task(task_id, error=f"任务失败：{exc}", message="任务执行失败，请查看失败日志。")

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return payload if isinstance(payload, list) else []

    def _write_index(self, index: list[dict[str, Any]]) -> None:
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self.index_path, index)

    def _write_task(self, record: dict[str, Any]) -> None:
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self.task_dir / f"{record['task_id']}.json", record)

    @staticmethod
    def _atomic_write_json(path: Path, payload: Any) -> None:
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    @staticmethod
    def _index_item(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": record.get("task_id", ""),
            "action_name": record.get("action_name", ""),
            "status": record.get("status", ""),
            "progress": record.get("progress", 0),
            "message": record.get("message", ""),
            "start_time": record.get("start_time", ""),
            "end_time": record.get("end_time", ""),
            "error": record.get("error", ""),
            "result_file": record.get("result_file", ""),
            "created_by": record.get("created_by", ""),
            "parameters": record.get("parameters", {}),
        }


DEFAULT_QUEUE = TaskQueue()


def get_default_queue() -> TaskQueue:
    return DEFAULT_QUEUE
