from abc import ABC, abstractmethod

from app.core.logger import logger
from app.processor.import_process.state import ImportGraphState
from app.utils.task_utils import add_running_task, add_done_task
from app.processor.import_process.config import get_config


class NodeBase(ABC):
    """
    导入流程节点基类
    所有节点类都应继承此基类，实现 process 方法
    使用示例:
        class NodeXx(BaseNode):
            name = "node_xx"
            def process(self, state):
                # 实现具体逻辑
                return state

        # 作为 LangGraph 节点使用
        node_xx = NodeXx()
        workflow.add_node("node_xx", node_xx)
    """

    #节点名称，子类应覆盖
    name: str = "base_node"


    def __init__(self, config=None):
        """
        强制子类设置 name
        """
        if self.name == "base_node":
            raise ValueError(f"子类 {self.__class__.__name__} 必须覆盖 name 类属性")
        self.config = config or get_config()


    def __call__(self, state: ImportGraphState) -> ImportGraphState:
        """记录进度，失败时清理运行标记并保留原始异常。"""
        from uuid import uuid4
        from app.utils.task_utils import update_task_status, set_task_result, remove_running_task
        task_id = state.get("task_id") or uuid4().hex
        state["task_id"] = task_id
        update_task_status(task_id, "processing")
        add_running_task(task_id, self.name)
        logger.info("节点 {} 开始，任务 {}", self.name, task_id)
        try:
            result = self.process(state)
            add_done_task(task_id, self.name)
            if self.name == "node_import_milvus":
                update_task_status(task_id, "completed")
                set_task_result(task_id, "imported_count", str(result["imported_count"]))
            return result
        except Exception as exc:
            remove_running_task(task_id, self.name)
            update_task_status(task_id, "failed")
            set_task_result(task_id, "error", str(exc))
            logger.error("节点 {} 失败，任务 {}：{}", self.name, task_id, exc)
            raise

    @abstractmethod
    def process(self, state: ImportGraphState) -> ImportGraphState:
        """执行节点业务，返回更新后的状态。"""
        raise NotImplementedError
