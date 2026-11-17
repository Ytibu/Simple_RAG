"""MCP SSE 边界测试，不访问公网。"""
import json
import unittest
from unittest.mock import MagicMock, patch

from app.clients.web_search import search_web, search_http, normalize_results


class WebSearchTests(unittest.TestCase):
    def test_empty_legacy_stream_uses_same_service_http_endpoint(self):
        session = MagicMock()
        session.get.return_value.iter_lines.return_value = iter([])
        with patch.dict('os.environ', {'MCP_DASHSCOPE_BASE_URL': 'https://mcp.example/sse', 'OPENAI_API_KEY': 'test'}), patch(
                'app.clients.web_search.requests.Session') as factory, patch(
                'app.clients.web_search.search_http', return_value=[]) as http:
            factory.return_value.__enter__.return_value = session
            self.assertEqual(search_web('问题'), [])
        http.assert_called_once_with('问题', 'https://mcp.example/mcp', 'test')

    def test_streamable_http_accepts_sse_tool_result(self):
        payloads = [{'id': 1, 'result': {}}, {}, {'id': 2, 'result': {'tools': [
            {'name': 'web_search', 'inputSchema': {'properties': {'query': {}}}}]}}]
        responses = []
        for payload in payloads:
            response = MagicMock()
            response.__enter__.return_value = response
            response.headers = {'Content-Type': 'application/json'}
            response.json.return_value = payload
            responses.append(response)
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.headers = {'Content-Type': 'text/event-stream'}
        stream.iter_lines.return_value = iter(['event: message', 'data: ' + json.dumps({
            'id': 3, 'result': {'content': [{'type': 'text', 'text': '搜索结果'}]}}), ''])
        responses.append(stream)
        session = MagicMock()
        session.post.side_effect = responses
        with patch('app.clients.web_search.requests.Session') as factory:
            factory.return_value.__enter__.return_value = session
            docs = search_http('问题', 'https://mcp.example/mcp', 'test')
        self.assertEqual(docs[0]['content'], '搜索结果')

    def test_pages_are_normalized_and_invalid_results_filtered(self):
        result = {'content': [{'type': 'text', 'text': json.dumps({'pages': [
            {'title': ' 标题 ', 'url': 'https://example.com/doc', 'snippet': ' 内容 '},
            {'url': 'https://example.com/empty', 'snippet': ''},
            {'url': 'javascript:bad', 'snippet': '内容'}]})}]}
        docs = normalize_results(result)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]['content'], '内容')
        self.assertEqual(docs[0]['title'], '标题')
        self.assertEqual(docs[0]['url'], 'https://example.com/doc')

    def test_streamable_http_session_and_json_response(self):
        session = MagicMock()
        replies = [
            {'id': 1, 'result': {'protocolVersion': '2025-03-26'}}, {},
            {'id': 2, 'result': {'tools': [{'name': 'bailian_web_search', 'inputSchema': {
                'properties': {'query': {}, 'count': {}}}}]}},
            {'id': 3, 'result': {'content': [{'type': 'text', 'text': json.dumps({'pages': [
                {'title': '资料', 'url': 'https://example.com', 'snippet': '网页摘要'}]})}]}}]
        responses = []
        for payload in replies:
            response = MagicMock()
            response.__enter__.return_value = response
            response.headers = {'Content-Type': 'application/json', 'Mcp-Session-Id': 'session-test'}
            response.json.return_value = payload
            responses.append(response)
        session.post.side_effect = responses
        with patch.dict('os.environ', {'MCP_DASHSCOPE_BASE_URL': 'https://mcp.example/mcp', 'OPENAI_API_KEY': 'test'}), patch(
                'app.clients.web_search.requests.Session') as factory:
            factory.return_value.__enter__.return_value = session
            docs = search_web('问题')
        self.assertEqual(docs[0]['content'], '网页摘要')
        self.assertEqual(session.post.call_args.kwargs['json']['params']['arguments'], {'query': '问题', 'count': 5})
        self.assertEqual(session.post.call_args.kwargs['headers']['Mcp-Session-Id'], 'session-test')
        session.delete.assert_called_once()

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
