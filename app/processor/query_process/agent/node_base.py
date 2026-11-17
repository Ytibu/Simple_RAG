from abc import ABC, abstractmethod
from app.core.logger import logger
from app.processor.query_process.config import QueryConfig


class NodeBase(ABC):
    """节点只返回修改字段，避免并行分支覆盖其他状态。"""
    name = "base_node"

    def __init__(self, config=None):
        self.config = config or QueryConfig()

    def __call__(self, state):
        logger.info("【{}】查询节点开始", self.name)
        if self.config.event_sink:
            self.config.event_sink("progress", {"node": self.name, "status": "running"})
        result = self.process(state)
        if self.config.event_sink:
            self.config.event_sink("progress", {"node": self.name, "status": "completed"})
        logger.info("【{}】查询节点完成", self.name)
        return result

    @abstractmethod
    def process(self, state):
        raise NotImplementedError
