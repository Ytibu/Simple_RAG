from app.core.logger import logger
from app.import_process.agent.node_base import NodeBase
from app.import_process.agent.state import ImportGraphState


class NodeEntry(NodeBase):
    """
    节点: 入口节点
    作为图的 Entry Point，负责接收外部输入并决定流程走向。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_entry"

    def process(self, state: ImportGraphState) -> ImportGraphState:
        """
        节点逻辑
        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """

        # TODO
        logger.info(f"【{self.name}】节点逻辑")

        # 模拟简单的路由逻辑
        if "local_file_path" in state:
            path = state["local_file_path"]
            if path.endswith(".pdf"):
                state["is_pdf_read_enabled"] = True
            elif path.endswith(".md"):
                state["is_md_read_enabled"] = True

        return state


if __name__ == '__main__':

    from app.import_process.agent.state import create_default_state

    # 单元测试：覆盖不支持类型、MD、PDF三种场景
    logger.info("===== 开始node_entry节点单元测试 =====")

    # 测试1: 不支持的TXT文件
    test_state1 = create_default_state(
        task_id="test_entry_task_001",
        local_file_path="联想海豚用户手册.txt"
    )
    node_entry1 = NodeEntry()
    node_entry1(test_state1)

    # 测试2: MD文件
    test_state2 = create_default_state(
        task_id="test_entry_task_002",
        local_file_path="小米用户手册.md"
    )
    node_entry2 = NodeEntry()
    node_entry2(test_state2)

    # 测试3: PDF文件
    test_state3 = create_default_state(
        task_id="test_entry_task_003",
        local_file_path="万用表的使用.pdf"
    )
    node_entry3 = NodeEntry()
    node_entry3(test_state3)

    logger.info("===== 结束node_entry节点单元测试 =====")