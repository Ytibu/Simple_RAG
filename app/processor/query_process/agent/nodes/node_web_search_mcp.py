from app.processor.query_process.agent.node_base import NodeBase


class NodeWebSearchMcp(NodeBase):
    """仅在请求显式启用时调用联网搜索，不影响默认知识库查询。"""
    name = "node_web_search_mcp"

    def process(self, state):
        if not self.config.use_web:
            return {"web_search_docs": []}
        from app.clients.web_search import search_web
        return {"web_search_docs": search_web(state["rewritten_query"])}
