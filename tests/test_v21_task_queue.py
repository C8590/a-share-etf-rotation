from __future__ import annotations

from api.task_queue import TaskQueue


def test_task_queue_status_lifecycle(tmp_path):
    queue = TaskQueue(task_dir=tmp_path / "tasks", log_path=tmp_path / "logs" / "recent_actions.log")

    task = queue.create_task("demo_action", {"x": 1}, message="任务已创建")
    assert task["status"] == "pending"

    running = queue.update_task(task["task_id"], status="running", progress=40, message="任务运行中")
    assert running["status"] == "running"
    assert running["progress"] == 40

    done = queue.complete_task(task["task_id"], message="任务完成")
    assert done["status"] == "success"
    assert done["progress"] == 100
    assert done["end_time"]

    tasks = queue.list_tasks()
    assert tasks[0]["task_id"] == task["task_id"]
    assert queue.get_task(task["task_id"])["status"] == "success"


def test_task_queue_failed_task_records_chinese_error(tmp_path):
    queue = TaskQueue(task_dir=tmp_path / "tasks", log_path=tmp_path / "logs" / "recent_actions.log")
    task = queue.create_task("bad_action")

    failed = queue.fail_task(task["task_id"], "中文失败原因：行情源不可用")

    assert failed["status"] == "failed"
    assert "中文失败原因" in failed["error"]
    assert queue.failed_tasks()[0]["task_id"] == task["task_id"]
    assert any("中文失败原因" in item.get("error", "") for item in queue.recent_logs())
