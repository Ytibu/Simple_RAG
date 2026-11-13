import sys

from app.core.logger import logger
from app.processor.import_process.state import ImportGraphState


def node_1(state: ImportGraphState) -> ImportGraphState:


    node_name = sys._getframe().f_code.co_name

    logger.info("记录开始日志：" + node_name)
    logger.info("添加节点状态跟踪" + node_name)

    try:


        # TODO 节点核心处理逻辑

        logger.info("添加节点状态跟踪" + node_name)

        logger.info("记录结束日志" + node_name)

        return state

    except Exception as e:

        # 统一处理所有步骤的异常
        error_msg = f"【{node_name}】流程执行失败：{str(e)}"
        logger.exception(error_msg, e)

        raise