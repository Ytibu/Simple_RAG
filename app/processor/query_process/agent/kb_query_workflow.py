"""知识库查询图：主体确认 → 多路召回 → RRF → 重排 → 生成。"""
import argparse
import json
from uuid import uuid4

from langgraph.graph import END, StateGraph
from app.processor.query_process.config import QueryConfig
from app.processor.query_process.agent.state import QueryGraphState, create_default_state
from app.processor.query_process.agent.nodes.node_item_name_confirm import NodeItemNameConfirm
from app.processor.query_process.agent.nodes.node_search_embedding import NodeSearchEmbedding
from app.processor.query_process.agent.nodes.node_search_embedding_hyde import NodeSearchEmbeddingHyde
from app.processor.query_process.agent.nodes.node_web_search_mcp import NodeWebSearchMcp
from app.processor.query_process.agent.nodes.node_rrf import NodeRrf
from app.processor.query_process.agent.nodes.node_rerank import NodeRerank
from app.processor.query_process.agent.nodes.node_answer_output import NodeAnswerOutput


class KBQueryWorkflow:
    def __init__(self, config=None):
        self.config = config or QueryConfig()
        self.workflow = StateGraph(QueryGraphState)
        for node_class in (NodeItemNameConfirm, NodeSearchEmbedding, NodeSearchEmbeddingHyde,
                           NodeWebSearchMcp, NodeRrf, NodeRerank, NodeAnswerOutput):
            node = node_class(self.config)
            self.workflow.add_node(node.name, node)
        self.workflow.set_entry_point("node_item_name_confirm")
        self.workflow.add_conditional_edges("node_item_name_confirm",
            lambda state: "answer" if state.get("answer") else "search",
            {"answer": "node_answer_output", "search": "node_multi_search"})
        self.workflow.add_node("node_multi_search", lambda state: {})
        branches = ["node_search_embedding", "node_search_embedding_hyde", "node_web_search_mcp"]
        for branch in branches:
            self.workflow.add_edge("node_multi_search", branch)
        # 列表边显式等待全部分支；各分支只写自己的召回字段。
        self.workflow.add_edge(branches, "node_rrf")
        self.workflow.add_edge("node_rrf", "node_rerank")
        self.workflow.add_edge("node_rerank", "node_answer_output")
        self.workflow.add_edge("node_answer_output", END)
        self._compiled_app = None

    def compile(self):
        if self._compiled_app is None:
            self._compiled_app = self.workflow.compile()
        return self._compiled_app

    def run(self, initial_state, stream=False):
        query = initial_state.get("original_query", "").strip()
        if not query or len(query) > 4000:
            raise ValueError("问题长度必须为 1 到 4000 字")
        # 每轮清除旧答案与召回；仅显式历史用于连续对话。
        state = create_default_state(original_query=query,
            session_id=initial_state.get("session_id", ""),
            task_id=initial_state.get("task_id") or uuid4().hex,
            history=initial_state.get("history", []))
        app = self.compile()
        return app.stream(state, stream_mode="updates") if stream else app.invoke(state)

    @classmethod
    def create_and_run(cls, initial_state, stream=False):
        return cls().run(initial_state, stream)


def main():
    parser = argparse.ArgumentParser(description="查询已导入的知识库")
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--no-hyde", action="store_true")
    parser.add_argument("--web", action="store_true", help="额外启用配置的 MCP 联网搜索")
    parser.add_argument("--stream", action="store_true", help="输出节点完成进度")
    args = parser.parse_args()
    workflow = KBQueryWorkflow(QueryConfig(top_k=args.top_k, use_hyde=not args.no_hyde, use_web=args.web))
    result = workflow.run(create_default_state(original_query=args.question), stream=args.stream)
    if args.stream:
        for update in result:
            print(json.dumps(update, ensure_ascii=False, default=str))
    else:
        print(json.dumps({key: result.get(key) for key in ("answer", "item_names", "sources", "reranker_used")},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
