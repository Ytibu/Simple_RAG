"""查询后台任务与可重放的 SSE 事件，按任务隔离同一会话的多轮请求。"""
from dataclasses import dataclass, field
from threading import Condition, Lock, Thread
from time import monotonic
from uuid import uuid4

from app.core.logger import logger
from app.services import queries


@dataclass
class QueryTask:
    session_id: str
    task_id: str = field(default_factory=lambda: uuid4().hex)
    events: list = field(default_factory=list)
    condition: Condition = field(default_factory=Condition)
    finished: bool = False
    updated: float = field(default_factory=monotonic)
    done: list = field(default_factory=list)
    running: list = field(default_factory=list)

    def emit(self, event, data):
        with self.condition:
            if event == "progress":
                node = data["node"]
                if data["status"] == "running":
                    self.running.append(node)
                else:
                    self.running.remove(node)
                    self.done.append(node)
                data = {**data, "done_list": self.done.copy(), "running_list": self.running.copy()}
            self.events.append((event, data))
            self.finished = event in {"final", "error"}
            self.updated = monotonic()
            self.condition.notify_all()

    def read(self, cursor):
        with self.condition:
            if cursor >= len(self.events) and not self.finished:
                self.condition.wait(timeout=10)
            return self.events[cursor:].copy(), self.finished


_tasks = {}
_tasks_lock = Lock()


def submit(question, session_id=None, **options):
    session_id = session_id or uuid4().hex
    with _tasks_lock:
        expired = [key for key, task in _tasks.items() if task.finished and monotonic() - task.updated > 900]
        for key in expired:
            del _tasks[key]
        if any(task.session_id == session_id and not task.finished for task in _tasks.values()):
            raise ValueError("当前会话仍有问题正在处理")
        if len(_tasks) >= 128:
            # 已完成任务可释放，正在处理的任务始终保留。
            oldest = next((key for key, task in _tasks.items() if task.finished), None)
            if oldest is None:
                raise RuntimeError("查询任务已满，请稍后重试")
            del _tasks[oldest]
        task = QueryTask(session_id)
        _tasks[task.task_id] = task

    def run():
        try:
            result = queries.query(question, session_id, **options, event_sink=task.emit, task_id=task.task_id)
            task.emit("final", result)
        except Exception as exc:
            logger.error("查询任务 {} 失败，异常类型 {}", task.task_id, type(exc).__name__)
            task.emit("error", {"error": "查询失败，请检查模型、存储服务及服务端日志。"})

    Thread(target=run, daemon=True, name="query-" + task.task_id).start()
    return task


def get_task(task_id):
    with _tasks_lock:
        return _tasks.get(task_id)


def latest_task(session_id):
    with _tasks_lock:
        return next((task for task in reversed(list(_tasks.values())) if task.session_id == session_id), None)
