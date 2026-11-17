import json
import math
from pathlib import Path

from app.processor.import_process.agent.node_base import NodeBase
from app.processor.import_process.exceptions import EmbeddingError


def encode_texts(texts):
    from app.lm.embedding_utils import generate_embeddings
    return generate_embeddings(texts)


class NodeBgeEmbedding(NodeBase):
    """分批生成切片及主体名称的 BGE-M3 稠密、稀疏向量。"""
    name = "node_bge_embedding"

    def process(self, state):
        try:
            size = self.config.embedding_batch_size
            if size <= 0 or not state.get("chunks") or not state.get("item_name"):
                raise ValueError("批量大小、切片或主体名称无效")
            texts = [f"{state['item_name']}\n{c['content']}" for c in state["chunks"]]
            texts.append(state["item_name"])
            vectors = []
            for start in range(0, len(texts), size):
                batch = texts[start:start + size]
                result = encode_texts(batch)
                if len(result["dense"]) != len(batch) or len(result["sparse"]) != len(batch):
                    raise ValueError("模型返回的向量数量与输入不一致")
                for dense, sparse in zip(result["dense"], result["sparse"]):
                    if len(dense) != self.config.embedding_dim or not all(math.isfinite(v) for v in dense):
                        raise ValueError("稠密向量维度错误或包含非有限值")
                    sparse = {int(k): float(v) for k, v in sparse.items()}
                    if any(k < 0 or not math.isfinite(v) for k, v in sparse.items()):
                        raise ValueError("稀疏向量无效")
                    vectors.append({"dense_vector": dense, "sparse_vector": sparse})
            records = [{**chunk, **vector, "item_name": state["item_name"],
                        "document_id": state["document_id"]}
                       for chunk, vector in zip(state["chunks"], vectors[:-1])]
            Path(state["embeddings_path"]).write_text(
                json.dumps(records, ensure_ascii=False), encoding="utf-8")
            state.update(chunks=records, embeddings_content=records, item_embedding=vectors[-1])
            return state
        except Exception as exc:
            raise EmbeddingError("BGE 向量生成失败", node_name=self.name, cause=exc) from exc
