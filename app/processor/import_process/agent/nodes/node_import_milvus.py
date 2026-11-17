from app.processor.import_process.agent.node_base import NodeBase
from app.processor.import_process.exceptions import MilvusError


def get_client(config):
    from pymilvus import MilvusClient
    return MilvusClient(uri=config.milvus_url)


class NodeImportMilvus(NodeBase):
    """建立兼容的集合并写入切片与主体名称；不执行检索。"""
    name = "node_import_milvus"

    def process(self, state):
        from app.clients.milvus_utils import ensure_import_collection
        config = self.config
        records = state.get("embeddings_content", [])
        if not records or not config.chunks_collection or not config.item_name_collection:
            raise MilvusError("缺少向量记录或 Milvus 集合配置", node_name=self.name)
        if config.chunks_collection == config.item_name_collection:
            raise MilvusError("切片集合与主体集合必须不同", node_name=self.name)
        client = None
        state["imported_count"] = 0
        try:
            if config.embedding_batch_size <= 0:
                raise ValueError("入库批量大小必须为正数")
            client = get_client(config)
            ensure_import_collection(client, config.chunks_collection, config.embedding_dim)
            ensure_import_collection(client, config.item_name_collection, config.embedding_dim, item=True)
            # upsert 允许同一状态的入库节点重试，不自动删除已有文档。
            for start in range(0, len(records), config.embedding_batch_size):
                client.upsert(collection_name=config.chunks_collection,
                              data=records[start:start + config.embedding_batch_size])
            client.upsert(collection_name=config.item_name_collection, data=[{
                "chunk_id": records[0]["chunk_id"], "document_id": state["document_id"],
                "item_name": state["item_name"], **state["item_embedding"]}])
            client.load_collection(collection_name=config.chunks_collection)
            client.load_collection(collection_name=config.item_name_collection)
            state["imported_count"] = len(records)
            return state
        except Exception as exc:
            raise MilvusError("Milvus 入库失败，可能已部分写入，可用同一状态重试",
                              node_name=self.name, cause=exc) from exc
        finally:
            if client is not None:
                client.close()
