"""MCP SSE 边界测试，不访问公网。"""
import json
import unittest
from unittest.mock import MagicMock, patch

from app.clients.web_search import search_web


class WebSearchTests(unittest.TestCase):
    def response(self, endpoint="/messages?session=test"):
        response = MagicMock()
        events = [("endpoint", endpoint), ("message", json.dumps({"id": 1, "result": {}})),
                  ("message", json.dumps({"id": 2, "result": {"tools": [
                      {"name": "web_search", "inputSchema": {"properties": {"query": {"type": "string"}}}}]}})),
                  ("message", json.dumps({"id": 3, "result": {"content": [{"type": "text", "text": "真实搜索结果"}]}}))]
        response.iter_lines.return_value = iter(
            line for event, data in events for line in [f"event: {event}", f"data: {data}", ""])
        return response

    def test_sse_handshake_and_search(self):
        session = MagicMock()
        response = self.response()
        session.get.return_value = response
        with patch.dict("os.environ", {"MCP_DASHSCOPE_BASE_URL": "https://mcp.example/sse", "OPENAI_API_KEY": "test"}), patch(
                "app.clients.web_search.requests.Session") as factory:
            factory.return_value.__enter__.return_value = session
            results = search_web("问题")
        self.assertEqual(results[0]["content"], "真实搜索结果")
        calls = [c.kwargs["json"] for c in session.post.call_args_list]
        self.assertEqual([c["method"] for c in calls],
                         ["initialize", "notifications/initialized", "tools/list", "tools/call"])
        self.assertEqual(calls[-1]["params"]["arguments"], {"query": "问题"})
        response.close.assert_called_once()

    def test_rejects_cross_origin_message_endpoint(self):
        session = MagicMock()
        session.get.return_value = self.response("https://other.example/messages")
        with patch.dict("os.environ", {"MCP_DASHSCOPE_BASE_URL": "https://mcp.example/sse", "OPENAI_API_KEY": "test"}), patch(
                "app.clients.web_search.requests.Session") as factory:
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(ValueError):
                search_web("问题")
        session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
