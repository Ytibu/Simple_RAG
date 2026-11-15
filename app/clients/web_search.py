"""复用现有百炼 MCP SSE 服务，无需增加 MCP SDK 依赖。"""
import json
import os
from queue import Queue, Empty
from threading import Thread
from time import monotonic
from urllib.parse import urljoin, urlparse

import requests


def search_web(query):
    url = os.getenv("MCP_DASHSCOPE_BASE_URL", "")
    token = os.getenv("OPENAI_API_KEY", "")
    if not url or not token:
        raise ValueError("联网搜索需要 MCP_DASHSCOPE_BASE_URL 与 OPENAI_API_KEY")
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
                    raise RuntimeError("MCP 连接已关闭")
                if event == "endpoint":
                    endpoint = urljoin(url, data)
                    if urlparse(endpoint).netloc != urlparse(url).netloc:
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
            tools = send("tools/list", {}, 2).get("tools", [])
            tool = next((t for t in tools if "search" in t["name"].lower()), None)
            if not tool:
                raise ValueError("MCP 服务未提供搜索工具")
            properties = tool.get("inputSchema", {}).get("properties", {})
            key = next((key for key in ("query", "search_query", "q") if key in properties), None)
            if not key:
                raise ValueError("搜索工具缺少已支持的问题参数")
            result = send("tools/call", {"name": tool["name"], "arguments": {key: query}}, 3)
            if result.get("isError"):
                raise RuntimeError("MCP 搜索工具执行失败")
            text = "\n".join(c["text"] for c in result.get("content", []) if c.get("type") == "text")
            if not text.strip():
                return []
            return [{"chunk_id": "web-1", "document_id": "web", "item_name": "",
                     "title": "MCP 联网搜索结果", "content": text[:16000],
                     "metadata": {"source": "web", "provider": "MCP"}}]
        finally:
            response.close()
