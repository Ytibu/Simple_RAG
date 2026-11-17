from app.processor.query_process.agent.node_base import NodeBase


class NodeRrf(NodeBase):
    """按主键去重并累加各召回分支的倒数排名。"""
    name = "node_rrf"

    def process(self, state):
        merged = {}
        for field, weight in zip(("embedding_chunks", "hyde_embedding_chunks"), self.config.rrf_weights):
            seen = set()
            for rank, doc in enumerate(state.get(field, []), 1):
                key = (doc.get("document_id", ""), str(doc["chunk_id"]))
                if key in seen:
                    continue
                seen.add(key)
                if key not in merged:
                    merged[key] = {**doc, "rrf_score": 0.0}
                merged[key]["rrf_score"] += weight / (self.config.rrf_k + rank)
        return {"rrf_chunks": sorted(merged.values(), key=lambda d: d["rrf_score"], reverse=True)[:self.config.recall_k]}
