"""复用现有百炼 MCP SSE 服务，无需增加 MCP SDK 依赖。"""
import json
import hashlib
import os
from queue import Queue, Empty
from threading import Thread
from time import monotonic
from urllib.parse import urljoin, urlparse

import requests


def normalize_results(result):
    """将百炼 pages 拆成独立文档，保留网页链接和有效摘要。"""
    docs = []
    blocks = result.get("content", [])
    if result.get("structuredContent"):
        blocks = [{"type": "text", "text": json.dumps(result["structuredContent"])}] + blocks
    for block in blocks:
        if block.get("type") == "tool_call":
            docs.extend(normalize_results(block))
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text", "").strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        pages = payload.get("pages", []) if isinstance(payload, dict) else None
        if pages is None:
            if text:
                docs.append({"chunk_id": hashlib.sha256(text.encode()).hexdigest(), "document_id": "web",
                             "item_name": "", "title": "联网搜索结果", "content": text[:16000],
                             "metadata": {"source": "web", "provider": "MCP"}})
            continue
        for page in pages:
            snippet = str(page.get("snippet") or "").strip()
            url = str(page.get("url") or "").strip()
            if not snippet or urlparse(url).scheme not in {"http", "https"}:
                continue
            docs.append({"chunk_id": hashlib.sha256(url.encode()).hexdigest(), "document_id": "web",
                         "item_name": "", "title": str(page.get("title") or url).strip(), "url": url,
                         "content": snippet[:16000], "metadata": {"source": "web", "url": url, "provider": "MCP"}})
    return list({(doc["document_id"], doc["chunk_id"]): doc for doc in docs}.values())


def call_search(send, query):
    tools = send("tools/list", {}, 2).get("tools", [])
    tool = next((t for t in tools if t["name"] == "bailian_web_search"), None)
    tool = tool or next((t for t in tools if "search" in t["name"].lower()), None)
    if not tool:
        raise ValueError("MCP 服务未提供搜索工具")
    properties = tool.get("inputSchema", {}).get("properties", {})
    key = next((key for key in ("query", "search_query", "q") if key in properties), None)
    if not key:
        raise ValueError("搜索工具缺少已支持的问题参数")
    arguments = {key: query}
    if "count" in properties:
        arguments["count"] = 5
    result = send("tools/call", {"name": tool["name"], "arguments": arguments}, 3)
    if result.get("isError"):
        raise RuntimeError("MCP 搜索工具执行失败")
    return normalize_results(result)


def search_http(query, url, token):
    """Streamable HTTP 支持 JSON 或 SSE 响应，并传递服务器会话标识。"""
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
    deadline = monotonic() + 60
    with requests.Session() as session:
        def send(method, params=None, request_id=None):
            body = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                body["params"] = params
            if request_id is not None:
                body["id"] = request_id
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("MCP 搜索超时")
            with session.post(url, headers=headers, json=body, stream=True,
                              timeout=(10, remaining), allow_redirects=False) as response:
                response.raise_for_status()
                if response.headers.get("Mcp-Session-Id"):
                    headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
                if request_id is None:
                    return None
                if "text/event-stream" in response.headers.get("Content-Type", ""):
                    response.encoding = "utf-8"
                    data = []
                    payload = None
                    for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                        if monotonic() >= deadline:
                            raise TimeoutError("MCP 搜索超时")
                        if line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif not line and data:
                            message = json.loads("\n".join(data))
                            data = []
                            if message.get("id") == request_id:
                                payload = message
                                break
                    if payload is None:
                        raise RuntimeError("MCP 未返回对应请求结果")
                else:
                    payload = response.json()
                if payload.get("id") != request_id or "error" in payload:
                    raise RuntimeError("MCP 服务返回调用错误")
                return payload["result"]

        try:
            initialized = send("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "knowledge-base", "version": "0.1.0"}}, 1)
            headers["MCP-Protocol-Version"] = initialized.get("protocolVersion", "2025-03-26")
            send("notifications/initialized")
            return call_search(send, query)
        finally:
            if headers.get("Mcp-Session-Id"):
                try:
                    session.delete(url, headers=headers, timeout=5, allow_redirects=False).close()
                except requests.RequestException:
                    pass


def search_web(query):
    url = os.getenv("MCP_DASHSCOPE_BASE_URL", "")
    token = os.getenv("OPENAI_API_KEY", "")
    if not url or not token:
        raise ValueError("联网搜索需要 MCP_DASHSCOPE_BASE_URL 与 OPENAI_API_KEY")
    if not urlparse(url).path.rstrip("/").endswith("/sse"):
        return search_http(query, url, token)
    messages = Queue(maxsize=100)
    headers = {"Authorization": "Bearer " + token}
    deadline = monotonic() + 60
    with requests.Session() as session:
        response = session.get(url, headers={**headers, "Accept": "text/event-stream"},
                               stream=True, timeout=(10, 60), allow_redirects=False)
        response.raise_for_status()
        response.encoding = "utf-8"

        def receive():
            event, data = "", []
            try:
                for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        messages.put_nowait((event, "\n".join(data)))
                        event, data = "", []
            except Exception:
                pass
            finally:
                try:
                    messages.put_nowait(("closed", ""))
                except Exception:
                    pass

        thread = Thread(target=receive, daemon=True)
        thread.start()

        def next_message():
            try:
                return messages.get(timeout=max(0.01, deadline - monotonic()))
            except Empty as exc:
                raise TimeoutError("MCP 搜索超时") from exc

        try:
            while True:
                event, data = next_message()
                if event == "closed":
                    # 部分旧 /sse 地址返回空流，转到同服务的 Streamable HTTP 地址。
                    return search_http(query, url.rsplit("/", 1)[0] + "/mcp", token)
                if event == "endpoint":
                    endpoint = urljoin(url, data)
                    if (urlparse(endpoint).scheme, urlparse(endpoint).netloc) != (urlparse(url).scheme, urlparse(url).netloc):
                        raise ValueError("MCP 消息地址必须与配置服务同源")
                    break

            def send(method, params=None, request_id=None):
                body = {"jsonrpc": "2.0", "method": method}
                if params is not None:
                    body["params"] = params
                if request_id is not None:
                    body["id"] = request_id
                posted = session.post(endpoint, headers=headers, json=body, timeout=15, allow_redirects=False)
                posted.raise_for_status()
                if request_id is None:
                    return None
                while True:
                    event, data = next_message()
                    if event == "closed":
                        raise RuntimeError("MCP 连接已关闭")
                    if event != "message":
                        continue
                    payload = json.loads(data)
                    if payload.get("id") == request_id:
                        if "error" in payload:
                            raise RuntimeError("MCP 服务返回调用错误")
                        return payload["result"]

            send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                 "clientInfo": {"name": "knowledge-base", "version": "0.1.0"}}, 1)
            send("notifications/initialized")
            return call_search(send, query)
        finally:
            response.close()
