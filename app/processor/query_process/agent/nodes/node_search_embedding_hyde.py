from app.processor.query_process.agent.node_base import NodeBase
from app.processor.query_process.retrieval import ask_model, search


class NodeSearchEmbeddingHyde(NodeBase):
    name = "node_search_embedding_hyde"

    def process(self, state):
        if not self.config.use_hyde:
            return {"hyde_embedding_chunks": []}
        hypothesis = ask_model(self.config, "为检索生成一段不超过200字的假设性手册说明，保留型号和术语。"
                               "仅用于检索，不作为答案依据。\n" + state["rewritten_query"])
        text = state["rewritten_query"] + "\n" + hypothesis
        return {"hyde_embedding_chunks": search(self.config, text, state["item_names"])}
