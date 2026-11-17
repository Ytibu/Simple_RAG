"""查询流程测试：真实 LangGraph 编排，替换模型与数据库边界。"""
import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.processor.query_process.agent.kb_query_workflow import KBQueryWorkflow
from app.processor.query_process.agent.state import create_default_state
from app.processor.query_process.config import QueryConfig
from app.processor.query_process.agent.nodes.node_rrf import NodeRrf
from app.processor.query_process.agent.nodes.node_rerank import NodeRerank
from app.processor.query_process.agent.nodes.node_answer_output import NodeAnswerOutput

NODES = "app.processor.query_process.agent.nodes."
DOC = {"chunk_id": 101, "document_id": "doc1", "item_name": "LA2608", "title": "恢复出厂设置",
       "parent_title": "", "content": "长按 RESET 按钮恢复出厂设置。", "metadata": {}}


class QueryWorkflowTests(unittest.TestCase):
    def graph_patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch(NODES + "node_item_name_confirm.search",
                                  return_value=[{"item_name": "LA2608", "score": 0.95}]))
        item = stack.enter_context(patch(NODES + "node_item_name_confirm.ask_model",
                                  return_value={"status": "selected", "item_names": ["LA2608"]}))
        stack.enter_context(patch(NODES + "node_search_embedding.search", return_value=[DOC]))
        stack.enter_context(patch(NODES + "node_search_embedding_hyde.search", return_value=[DOC]))
        stack.enter_context(patch(NODES + "node_search_embedding_hyde.ask_model", return_value="假设手册内容"))
        stack.enter_context(patch(NODES + "node_rerank.ask_model", return_value={"scores": [0.9]}))
        answer = stack.enter_context(patch(NODES + "node_answer_output.ask_model", return_value="长按 RESET。[1]"))
        return stack, item, answer

    def test_parallel_graph_fuses_before_answer(self):
        stack, _, answer = self.graph_patches()
        with stack:
            result = KBQueryWorkflow(QueryConfig(reranker="llm")).run(
                create_default_state(original_query="LA2608如何重置？"))
        self.assertEqual(result["answer"], "长按 RESET。[1]")
        self.assertEqual(len(result["rrf_chunks"]), 1)
        self.assertAlmostEqual(result["rrf_chunks"][0]["rrf_score"], 2 / 61)
        self.assertEqual(result["sources"][0]["chunk_id"], 101)
        self.assertEqual(result["reranker_used"], "llm")
        self.assertEqual(answer.call_count, 1)
        self.assertNotIn("假设手册内容", result["prompt"])

    def test_clarification_short_circuits_retrieval(self):
        stack, item, answer = self.graph_patches()
        with stack:
            item.return_value = {"status": "clarify", "item_names": []}
            result = KBQueryWorkflow().run(create_default_state(original_query="如何使用？"))
        self.assertIn("请明确", result["answer"])
        self.assertFalse(result["sources"])
        answer.assert_not_called()

    def test_missing_item_short_circuits(self):
        stack, item, answer = self.graph_patches()
        with stack, patch(NODES + "node_item_name_confirm.search", return_value=[]):
            item.return_value = {"status": "missing", "item_names": []}
            result = KBQueryWorkflow().run(create_default_state(original_query="其他型号？"))
        self.assertIn("未找到", result["answer"])
        answer.assert_not_called()

    def test_unknown_product_is_not_returned_as_confirmed(self):
        stack, item, _ = self.graph_patches()
        with stack, patch(NODES + "node_item_name_confirm.search", return_value=[{"item_name": "LA2608", "score": 0.2}]):
            item.return_value = {"status": "selected", "item_names": ["invented"]}
            result = KBQueryWorkflow().run(create_default_state(original_query="使用？"))
        self.assertEqual(result["item_names"], [])
        self.assertIn("未找到", result["answer"])

    def test_history_rewrites_followup(self):
        stack, item, _ = self.graph_patches()
        with stack:
            item.return_value = {"rewritten_query": "LA2608如何重置？", "item_names": ["LA2608"]}
            result = KBQueryWorkflow(QueryConfig(reranker="llm")).run(create_default_state(
                original_query="它如何重置？", history=[{"question": "LA2608说明", "answer": "说明"}]))
        self.assertEqual(result["rewritten_query"], "LA2608如何重置？")

    def test_stream_and_reuse_clear_old_answer(self):
        stack, _, _ = self.graph_patches()
        with stack:
            updates = list(KBQueryWorkflow(QueryConfig(reranker="llm")).run(
                create_default_state(original_query="LA2608？", answer="旧答案"), stream=True))
        self.assertEqual(sum("node_rrf" in u for u in updates), 1)
        self.assertEqual(updates[-1]["node_answer_output"]["answer"], "长按 RESET。[1]")

    def test_empty_query(self):
        with self.assertRaises(ValueError):
            KBQueryWorkflow().run(create_default_state(original_query="  "))

    def test_empty_knowledge_base(self):
        with patch(NODES + "node_item_name_confirm.search", return_value=[]), patch(
                NODES + "node_item_name_confirm.ask_model", return_value={"item_names": ["LA2608"]}):
            result = KBQueryWorkflow().run(create_default_state(original_query="LA2608？"))
        self.assertIn("先导入", result["answer"])

    def test_rrf_deduplicates_within_branch(self):
        result = NodeRrf().process({"embedding_chunks": [DOC, DOC], "hyde_embedding_chunks": [DOC]})
        self.assertEqual(len(result["rrf_chunks"]), 1)
        self.assertAlmostEqual(result["rrf_chunks"][0]["rrf_score"], 2 / 61)

    def test_no_sources_does_not_generate(self):
        with patch(NODES + "node_answer_output.ask_model") as model:
            result = NodeAnswerOutput().process({"reranked_docs": []})
        self.assertIn("没有找到", result["answer"])
        model.assert_not_called()

    def test_unknown_citation_is_rejected(self):
        with patch(NODES + "node_answer_output.ask_model", return_value="答案[99]"):
            with self.assertRaises(ValueError):
                NodeAnswerOutput().process({"reranked_docs": [DOC], "rewritten_query": "重置？"})

    def test_context_limit_sources_match_prompt(self):
        with patch(NODES + "node_answer_output.ask_model", return_value="答案[1]"):
            result = NodeAnswerOutput(QueryConfig(context_chars=3)).process({
                "reranked_docs": [DOC, {**DOC, "chunk_id": 102}], "rewritten_query": "重置？"})
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(len(result["sources"][0]["content"]), 3)

    def test_bad_rerank_scores(self):
        for scores in ([], [float("nan")], [2]):
            with self.subTest(scores=scores), patch(NODES + "node_rerank.ask_model", return_value={"scores": scores}):
                with self.assertRaises(ValueError):
                    NodeRerank(QueryConfig(reranker="llm")).process({"rrf_chunks": [DOC], "rewritten_query": "重置？"})

    def test_local_rerank(self):
        with patch(NODES + "node_rerank.Path") as path, patch(
                "app.lm.reranker_utils.get_reranker_model") as model:
            path.return_value.is_dir.return_value = True
            model.return_value.compute_score.return_value = [0.9, 0.1]
            result = NodeRerank(QueryConfig(reranker="local", top_k=1)).process({
                "rrf_chunks": [DOC, {**DOC, "chunk_id": 102}], "rewritten_query": "重置？"})
        self.assertEqual(result["reranker_used"], "local")
        self.assertEqual([d["chunk_id"] for d in result["reranked_docs"]], [101])

    def test_disabled_web_does_not_call_remote(self):
        from app.processor.query_process.agent.nodes.node_web_search_mcp import NodeWebSearchMcp
        with patch("app.clients.web_search.search_web") as remote:
            self.assertEqual(NodeWebSearchMcp().process({}), {"web_search_docs": []})
        remote.assert_not_called()

    def test_retrieval_uses_actual_primary_key_and_escaped_filter(self):
        from app.processor.query_process.retrieval import search
        client = MagicMock()
        client.hybrid_search.return_value = [[{"chunk_id": 101, "distance": 0.1, "entity": DOC}]]
        with patch("pymilvus.MilvusClient", return_value=client), patch(
                "app.processor.query_process.retrieval.encode_query", return_value=([0.1], {1: 0.2})), patch(
                "app.clients.milvus_utils.create_hybrid_search_requests", return_value=[]) as requests:
            result = search(QueryConfig(), "问题", ['型号" OR 1==1'])
        self.assertEqual(result[0]["chunk_id"], 101)
        self.assertIn('\\"', requests.call_args.kwargs["expr"])
        client.close.assert_called_once()

    def test_retrieval_failure_propagates_and_closes(self):
        from app.processor.query_process.retrieval import search
        client = MagicMock()
        client.hybrid_search.side_effect = RuntimeError("DB failed")
        with patch("pymilvus.MilvusClient", return_value=client), patch(
                "app.processor.query_process.retrieval.encode_query", return_value=([0.1], {1: 0.2})):
            with self.assertRaises(RuntimeError):
                search(QueryConfig(), "问题")
        client.close.assert_called_once()


class QueryApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_query_validation(self):
        for body in ({"question": " "}, {"question": "a", "top_k": 13},
                     {"question": "a", "session_id": "../x"}):
            self.assertEqual(self.client.post("/api/queries", json=body).status_code, 422)

    def test_query_success(self):
        with patch("app.services.queries.query", return_value={"answer": "答案", "sources": []}) as service:
            response = self.client.post("/api/queries", json={"question": "如何使用？"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "答案")
        self.assertFalse(service.call_args.kwargs["use_web"])

    def test_query_failure_hides_credentials(self):
        with patch("app.services.queries.query", side_effect=RuntimeError("secret-key")):
            response = self.client.post("/api/queries", json={"question": "如何使用？"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-key", response.text)

    def test_history_and_delete(self):
        with patch("app.clients.query_history.get_history", return_value=[{"question": "a"}]):
            self.assertEqual(self.client.get("/api/queries/session/history").json()["history"], [{"question": "a"}])
        with patch("app.clients.query_history.clear_history", return_value=1):
            self.assertEqual(self.client.delete("/api/queries/session/history").json()["deleted_count"], 1)

    def test_page(self):
        response = self.client.get("/query")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="query-form"', response.text)
        self.assertEqual(self.client.get("/").text, response.text)
        self.assertEqual(self.client.get("/static/workspace.css").status_code, 200)
        self.assertEqual(self.client.get("/static/query.js").status_code, 200)


class QueryHistoryTests(unittest.TestCase):
    def test_latest_turns_are_returned_in_chronological_order(self):
        from app.clients.query_history import get_history
        collection = MagicMock()
        collection.find.return_value.sort.return_value.limit.return_value = [
            {"question": "new", "answer": "b", "item_names": []},
            {"question": "old", "answer": "a", "item_names": []}]
        with patch("app.clients.query_history.get_collection", return_value=collection):
            result = get_history("session")
        self.assertEqual([t["question"] for t in result], ["old", "new"])
        self.assertEqual(collection.find.return_value.sort.call_args.args[0][0], ("created_at", -1))

    def test_failed_workflow_does_not_save_turn(self):
        from app.services.queries import query
        with patch("app.clients.query_history.get_history", return_value=[]), patch(
                "app.services.queries.KBQueryWorkflow") as workflow, patch(
                "app.clients.query_history.save_turn") as save, patch(
                "app.clients.query_history.begin_turn"), patch("app.clients.query_history.fail_turn") as fail:
            workflow.return_value.run.side_effect = RuntimeError("model failed")
            with self.assertRaises(RuntimeError):
                query("问题")
        save.assert_not_called()
        fail.assert_called_once()


if __name__ == "__main__":
    unittest.main()
