"""验证流式事件、重连、动态截断和引用图片，外部服务均使用替身。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from app.main import app
from app.processor.query_process.config import QueryConfig
from app.processor.query_process.agent.nodes.node_rerank import NodeRerank
from app.processor.query_process.agent.nodes.node_item_name_confirm import NodeItemNameConfirm
from app.processor.query_process.retrieval import stream_answer
from app.services.query_images import extract_images
from app.services import query_tasks

NODES = "app.processor.query_process.agent.nodes."


class QueryStreamTests(unittest.TestCase):
    def test_submit_replays_progress_delta_and_final(self):
        def query(question, session_id, **options):
            sink = options['event_sink']
            sink('progress', {'node': 'node_answer_output', 'status': 'running'})
            sink('delta', {'text': '回答'})
            sink('progress', {'node': 'node_answer_output', 'status': 'completed'})
            return {'answer': '回答', 'session_id': session_id, 'task_id': options['task_id'], 'sources': []}

        with patch('app.services.queries.query', side_effect=query), TestClient(app) as client:
            accepted = client.post('/query', json={'query': '使用方法', 'is_stream': True})
            self.assertEqual(accepted.status_code, 202)
            task = accepted.json()
            response = client.get(task['stream_url'])
            self.assertIn('event: ready', response.text)
            self.assertIn('event: progress', response.text)
            self.assertIn('"running_list": ["node_answer_output"]', response.text)
            self.assertIn('event: delta', response.text)
            self.assertIn('event: final', response.text)
            resumed = client.get(task['stream_url'], headers={'Last-Event-ID': '2'})
            self.assertNotIn('event: delta', resumed.text)
            self.assertIn('event: final', resumed.text)
            self.assertEqual(client.get(task['stream_url'], headers={'Last-Event-ID': '-1'}).status_code, 400)
            self.assertEqual(client.get('/stream/' + task['session_id']).status_code, 200)

    def test_failed_job_emits_error_without_secret(self):
        with patch('app.services.queries.query', side_effect=RuntimeError('secret-key')), TestClient(app) as client:
            accepted = client.post('/api/queries/submit', json={'question': '问题'})
            response = client.get(accepted.json()['stream_url'])
        self.assertIn('event: error', response.text)
        self.assertNotIn('secret-key', response.text)
        self.assertNotIn('event: final', response.text)

    def test_duplicate_active_session_is_rejected(self):
        task = query_tasks.QueryTask('busy-session')
        with patch.dict(query_tasks._tasks, {task.task_id: task}), TestClient(app) as client:
            response = client.post('/api/queries/submit', json={'question': '问题', 'session_id': task.session_id})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(client.delete('/api/queries/busy-session/history').status_code, 409)

    def test_model_chunks_are_forwarded_before_completion(self):
        events = []
        def chunks(*args):
            yield SimpleNamespace(content='第一段')
            self.assertEqual(events, [('delta', {'text': '第一段'})])
            yield SimpleNamespace(content='第二段')
        model = MagicMock()
        model.bind.return_value.stream.side_effect = chunks
        config = QueryConfig(event_sink=lambda event, data: events.append((event, data)))
        with patch('app.processor.query_process.retrieval.get_llm_client', return_value=model):
            self.assertEqual(stream_answer(config, '问题'), '第一段第二段')

    def test_dynamic_rerank_keeps_web_results_and_cuts_gap(self):
        docs = [{'chunk_id': i, 'content': str(i)} for i in range(4)]
        web = {'chunk_id': 'web', 'document_id': 'web', 'content': '网络资料'}
        with patch(NODES + 'node_rerank.ask_model', return_value={'scores': [.95, .9, .85, .1, .05]}):
            result = NodeRerank(QueryConfig(reranker='llm')).process({
                'rrf_chunks': docs, 'web_search_docs': [web], 'rewritten_query': '问题'})
        self.assertEqual(len(result['reranked_docs']), 3)

    def test_images_come_from_sources_and_use_stable_object_route(self):
        sources = [{'content': '![图](s3://bucket/doc/images/a.png) ![重复](s3://bucket/doc/images/a.png) '
                    '<img src="https://example.com/image.png"> ![无效](javascript:bad)'}]
        images = extract_images(sources)
        self.assertEqual(len(images), 2)
        self.assertTrue(images[0].startswith('/api/query-images?uri='))
        self.assertEqual(images[1], 'https://example.com/image.png')

    def test_ambiguous_product_confirmation_uses_history(self):
        state = {'original_query': '产品甲', 'history': [
            {'question': '如何配置？', 'answer': '请选择', 'item_candidates': ['产品甲', '产品乙']} ]}
        with patch(NODES + 'node_item_name_confirm.ask_model', return_value={
                'item_names': ['产品甲'], 'rewritten_query': '产品甲如何配置？'}), patch(
                NODES + 'node_item_name_confirm.search', return_value=[{'item_name': '产品甲', 'score': .7}]):
            result = NodeItemNameConfirm().process(state)
        self.assertEqual(result['item_names'], ['产品甲'])
        self.assertNotIn('answer', result)

    def test_image_authorization_and_health_routes(self):
        config = SimpleNamespace(minio_bucket='bucket', minio_endpoint='localhost:9000',
                                 minio_access_key='test', minio_secret_key='test', minio_secure=False)
        with patch('app.conf.config.get_config', return_value=config), patch('minio.Minio') as storage, TestClient(app) as client:
            storage.return_value.presigned_get_object.return_value = 'http://localhost:9000/bucket/image?signature=test'
            response = client.get('/api/query-images', params={'uri': 's3://bucket/doc/images/a.png'}, follow_redirects=False)
            self.assertEqual(response.status_code, 307)
            self.assertEqual(client.get('/api/query-images', params={'uri': 's3://other/image'}).status_code, 400)
            self.assertEqual(client.get('/health').json(), {'ok': True})
            self.assertEqual(client.get('/chat.html').status_code, 200)
