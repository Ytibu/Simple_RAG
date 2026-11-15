"""真实查询验收：HTTP 路由、本地向量/重排、LLM、MongoDB 连续两轮。"""
import argparse
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import app
from app.clients import query_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default="H3C LA2608通过什么网络连接无线控制器，支持哪些厂商的无线控制器？")
    parser.add_argument("--followup", default="它在配置前需要满足哪些基本要求？")
    parser.add_argument("--output", default="temp-files/query-verification.json")
    args = parser.parse_args()
    session_id = "verify_" + uuid4().hex
    client = TestClient(app)
    results = []
    for question in (args.question, args.followup):
        response = client.post("/api/queries", json={"question": question, "session_id": session_id})
        if response.status_code != 200:
            raise RuntimeError(f"查询接口失败：HTTP {response.status_code}，请检查服务日志")
        data = response.json()
        if not data["answer"] or not data["sources"]:
            raise RuntimeError("回答或引用为空，未通过真实检索验收")
        if data["reranker_used"] != "local":
            raise RuntimeError("本次验收要求使用本地重排模型")
        results.append({"question": question, **data})
    history = client.get(f"/api/queries/{session_id}/history").json()["history"]
    if len(history) != 2:
        raise RuntimeError("MongoDB 会话回读数量不符")
    # 确认引用主键确实来自 Milvus，而非模型生成的内容。
    from pymilvus import MilvusClient
    from app.processor.query_process.config import QueryConfig
    config = QueryConfig()
    milvus = MilvusClient(uri=config.milvus_url, timeout=30)
    try:
        ids = list({s["chunk_id"] for result in results for s in result["sources"]})
        records = milvus.get(collection_name=config.chunks_collection, ids=ids, output_fields=["content"])
        if len(records) != len(ids):
            raise RuntimeError("引用切片的 Milvus 回读数量不符")
        by_id = {r["chunk_id"]: r["content"] for r in records}
        if any(s["content"] not in by_id[s["chunk_id"]] for result in results for s in result["sources"]):
            raise RuntimeError("引用内容与 Milvus 不符")
    finally:
        milvus.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"status": "verified", "session_id": session_id, "results": results,
                                 "history_count": len(history), "source_readback": True},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "verified", "session_id": session_id, "rounds": len(results),
                      "sources": [len(r["sources"]) for r in results], "reranker": "local",
                      "history_count": len(history), "output": str(output)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
