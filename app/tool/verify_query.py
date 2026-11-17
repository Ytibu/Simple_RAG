"""真实查询验收：HTTP 路由、本地向量/重排、LLM、MongoDB 连续两轮。"""
import argparse
import json
from pathlib import Path
from uuid import uuid4
from time import monotonic

from fastapi.testclient import TestClient
from app.main import app
from app.clients import query_history


def execute(client, base, question, session_id, streaming, use_web):
    started = monotonic()
    response = client.post(base + ("/api/queries/submit" if streaming else "/api/queries"),
                           json={"question": question, "session_id": session_id, "use_web": use_web})
    if response.status_code != (202 if streaming else 200):
        raise RuntimeError(f"查询接口失败：HTTP {response.status_code}，请检查服务日志")
    data = response.json()
    if streaming:
        url = base + data['stream_url']
        connection = client.get(url, stream=True, timeout=(10, 600)) if base else client.stream('GET', url)
        deltas, final, counts = [], None, {}
        first_event = None
        with connection as stream:
            if stream.status_code != 200:
                raise RuntimeError('SSE 连接失败')
            event = ''
            for line in stream.iter_lines():
                line = line.decode('utf-8') if isinstance(line, bytes) else line
                if line.startswith('event:'):
                    event = line[6:].strip()
                elif line.startswith('data:'):
                    payload = json.loads(line[5:])
                    first_event = first_event if first_event is not None else monotonic() - started
                    counts[event] = counts.get(event, 0) + 1
                    if event == 'delta':
                        deltas.append(payload['text'])
                    elif event == 'final':
                        final = payload
                    elif event == 'error':
                        raise RuntimeError(payload['error'])
        if not final or ''.join(deltas) != final['answer'] or not counts.get('progress'):
            raise RuntimeError('SSE 事件或答案增量不完整')
        data = {**final, 'event_counts': counts, 'first_event_seconds': round(first_event, 3)}
    return {**data, 'elapsed_seconds': round(monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default="H3C LA2608通过什么网络连接无线控制器，支持哪些厂商的无线控制器？")
    parser.add_argument("--followup", default="它在配置前需要满足哪些基本要求？")
    parser.add_argument("--output", default="temp-files/query-verification.json")
    parser.add_argument("--stream", action="store_true", help="验证任务提交、SSE 进度及模型增量")
    parser.add_argument("--web", action="store_true", help="启用真实 MCP 搜索")
    parser.add_argument("--base-url", help="测试已启动的 HTTP 服务，默认在进程内调用路由")
    parser.add_argument("--confirm-product", help="出现澄清时明确选择的候选产品名称")
    args = parser.parse_args()
    session_id = "verify_" + uuid4().hex
    if args.base_url:
        import requests
        client = requests.Session()
        base = args.base_url.rstrip('/')
    else:
        client = TestClient(app)
        base = ''
    results, clarifications = [], []
    for question in (args.question, args.followup):
        data = execute(client, base, question, session_id, args.stream, args.web)
        if data.get('item_candidates') and not data.get('item_names') and args.confirm_product:
            if args.confirm_product not in data['item_candidates']:
                raise RuntimeError('指定确认产品不在实际候选中')
            clarifications.append({'question': question, **data})
            data = execute(client, base, args.confirm_product, session_id, args.stream, args.web)
        if not data["answer"] or not data["sources"]:
            raise RuntimeError("回答或引用为空，未通过真实检索验收")
        if data["reranker_used"] != "local":
            raise RuntimeError("本次验收要求使用本地重排模型")
        results.append({"question": question, **data})
        print('ROUND', len(results), 'sources', len(data['sources']), 'events', data.get('event_counts', {}), flush=True)
    history = client.get(base + f"/api/queries/{session_id}/history").json()["history"]
    if len(history) != 2 + len(clarifications):
        raise RuntimeError("MongoDB 会话回读数量不符")
    # 确认引用主键确实来自 Milvus，而非模型生成的内容。
    from pymilvus import MilvusClient
    from app.processor.query_process.config import QueryConfig
    config = QueryConfig()
    milvus = MilvusClient(uri=config.milvus_url, timeout=30)
    try:
        local_sources = [s for result in results for s in result['sources'] if s.get('document_id') != 'web']
        ids = list({s['chunk_id'] for s in local_sources})
        if not ids:
            raise RuntimeError('没有知识库来源，无法验证本地检索')
        records = milvus.get(collection_name=config.chunks_collection, ids=ids, output_fields=["content"])
        if len(records) != len(ids):
            raise RuntimeError("引用切片的 Milvus 回读数量不符")
        by_id = {r["chunk_id"]: r["content"] for r in records}
        if any(s["content"] not in by_id[s["chunk_id"]] for s in local_sources):
            raise RuntimeError("引用内容与 Milvus 不符")
    finally:
        milvus.close()
    images_checked = 0
    for image in dict.fromkeys(url for result in results for url in result.get('image_urls', [])):
        response = client.get(base + image if image.startswith('/') else image)
        if response.status_code != 200 or not response.content:
            raise RuntimeError('引用图片无法通过 HTTP 访问')
        images_checked += 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"status": "verified", "session_id": session_id, "results": results,
                                 "history_count": len(history), "source_readback": True,
                                 "clarifications": clarifications, "images_checked": images_checked},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "verified", "session_id": session_id, "rounds": len(results),
                      "sources": [len(r["sources"]) for r in results], "reranker": "local",
                      "history_count": len(history), "images_checked": images_checked,
                      "output": str(output)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
