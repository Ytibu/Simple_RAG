from dotenv import load_dotenv

load_dotenv()
from langgraph.constants import END
from langgraph.graph import StateGraph
from typing import Optional, Iterator

from app.core.logger import logger
from app.processor.import_process.agent.nodes.node_bge_embedding import NodeBgeEmbedding
from app.processor.import_process.agent.nodes.node_document_split import NodeDocumentSplit
from app.processor.import_process.agent.nodes.node_entry import NodeEntry
from app.processor.import_process.agent.nodes.node_import_milvus import NodeImportMilvus
from app.processor.import_process.agent.nodes.node_item_name_recognition import NodeItemNameRecognition
from app.processor.import_process.agent.nodes.node_md_img import NodeMdImg
from app.processor.import_process.agent.nodes.node_pdf_to_md import NodePdfToMd
from app.processor.import_process.state import ImportGraphState, create_default_state

# 初始化环境变量（类加载前执行，保证全局生效）


class KBImportWorkflow:
    """
    知识库导入工作流类
    封装LangGraph工作流的构建、编译、执行逻辑，支持自定义配置和多实例运行
    """
    def __init__(self, config=None):
        """初始化工作流：创建状态图、注册节点、定义路由规则"""
        # 1. 初始化LangGraph状态图
        self.config = config
        self.workflow = StateGraph(ImportGraphState)
        # 2. 初始化所有业务节点（实例属性，支持多实例隔离）
        self._init_nodes()
        # 3. 注册节点到工作流
        self._register_nodes()
        # 4. 设置入口和路由规则
        self._setup_routes()
        # 5. 编译工作流（懒加载，首次执行时编译）
        self._compiled_app: Optional[object] = None

    def _init_nodes(self):
        """初始化所有业务节点（私有方法，封装节点创建逻辑）"""
        self.node_entry = NodeEntry(self.config)
        self.node_pdf_to_md = NodePdfToMd(self.config)
        self.node_md_img = NodeMdImg(self.config)
        self.node_document_split = NodeDocumentSplit(self.config)
        self.node_item_name_recognition = NodeItemNameRecognition(self.config)
        self.node_bge_embedding = NodeBgeEmbedding(self.config)
        self.node_import_milvus = NodeImportMilvus(self.config)

    def _register_nodes(self):
        """注册所有节点到工作流（私有方法，统一管理节点注册）"""
        # 节点标识与实例属性名保持一致，便于维护
        self.workflow.add_node("node_entry", self.node_entry)
        self.workflow.add_node("node_pdf_to_md", self.node_pdf_to_md)
        self.workflow.add_node("node_md_img", self.node_md_img)
        self.workflow.add_node("node_document_split", self.node_document_split)
        self.workflow.add_node("node_item_name_recognition", self.node_item_name_recognition)
        self.workflow.add_node("node_bge_embedding", self.node_bge_embedding)
        self.workflow.add_node("node_import_milvus", self.node_import_milvus)

    @staticmethod
    def _route_after_entry(state: ImportGraphState) -> str:
        """入口节点后的条件路由函数（私有方法，封装路由逻辑）"""
        if state.get("is_md_read_enabled"):
            return "node_md_img"
        elif state.get("is_pdf_read_enabled"):
            return "node_pdf_to_md"
        else:
            return END

    def _setup_routes(self):
        """设置工作流路由规则（私有方法，封装边的定义）"""
        # 设置入口节点
        self.workflow.set_entry_point("node_entry")
        # 注册条件路由边
        self.workflow.add_conditional_edges(
            "node_entry",
            self._route_after_entry,
            {
                "node_md_img": "node_md_img",
                "node_pdf_to_md": "node_pdf_to_md",
                END: END
            }
        )
        # 注册静态顺序边
        self.workflow.add_edge("node_pdf_to_md", "node_md_img")
        self.workflow.add_edge("node_md_img", "node_document_split")
        self.workflow.add_edge("node_document_split", "node_item_name_recognition")
        self.workflow.add_edge("node_item_name_recognition", "node_bge_embedding")
        self.workflow.add_edge("node_bge_embedding", "node_import_milvus")
        self.workflow.add_edge("node_import_milvus", END)

    def compile(self):
        """编译工作流（如果已编译则直接获取结果）"""
        if not self._compiled_app:
            self._compiled_app = self.workflow.compile()
        return self._compiled_app

    def run(self, initial_state: ImportGraphState, stream: bool = False) -> ImportGraphState | Iterator[dict]:
        """
        统一执行入口，支持切换invoke/stream
        :param initial_state:  初始状态对象
        :param stream: 是否是流式输出
        :return: 执行完成后的状态对象
        """
        if not self._compiled_app:
            self.compile()
        if stream:
            return self._compiled_app.stream(initial_state)
        else:
            return self._compiled_app.invoke(initial_state)

    @classmethod
    def create_and_run(cls, initial_state: ImportGraphState, stream: bool = False) -> ImportGraphState | Iterator[dict]:
        """
        快捷方法：创建工作流实例并立即执行（兼容原有函数式调用习惯）
        :param initial_state: 初始状态对象
        :param stream: 是否是流式输出
        :return: 执行完成后的状态对象
        """
        workflow = cls()
        return workflow.run(initial_state, stream)


def main():
    """命令行一次只执行一次导入，可选按节点输出进度。"""
    import argparse
    parser = argparse.ArgumentParser(description="导入 PDF 或 Markdown 到知识库")
    parser.add_argument("file", help="本地 PDF 或 Markdown 路径")
    parser.add_argument("--stream", action="store_true", help="输出每个节点的完成信息")
    parser.add_argument("--no-llm", action="store_true", help="不调用大语言模型；图片仅上传，主体采用文件名")
    args = parser.parse_args()
    from app.processor.import_process.config import ImportConfig
    config = ImportConfig.from_env()
    if args.no_llm:
        config.use_llm = False
    workflow = KBImportWorkflow(config)
    state = create_default_state(local_file_path=args.file)
    if args.stream:
        for update in workflow.run(state, stream=True):
            logger.info("完成节点：{}", ", ".join(update))
    else:
        result = workflow.run(state)
        logger.info("导入完成，任务 {}，切片 {} 条", result["task_id"], result["imported_count"])


if __name__ == "__main__":
    main()
