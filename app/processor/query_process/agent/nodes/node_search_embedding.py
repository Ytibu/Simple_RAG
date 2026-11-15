from app.processor.query_process.agent.node_base import NodeBase
from app.processor.query_process.retrieval import search


class NodeSearchEmbedding(NodeBase):
    name = "node_search_embedding"

    def process(self, state):
        return {"embedding_chunks": search(self.config, state["rewritten_query"], state["item_names"])}
