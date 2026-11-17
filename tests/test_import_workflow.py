"""导入流程离线测试：真实图编排，替换模型、PDF 引擎及存储边界。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.conf.config import ImportConfig
from app.main import app
from app.processor.import_process import create_default_state
from app.processor.import_process.agent.kb_import_workflow import KBImportWorkflow
from app.processor.import_process.agent.nodes.node_entry import NodeEntry
from app.processor.import_process.agent.nodes.node_document_split import NodeDocumentSplit
from app.processor.import_process.agent.nodes.node_md_img import NodeMdImg
from app.processor.import_process.agent.nodes.node_bge_embedding import NodeBgeEmbedding
from app.processor.import_process.agent.nodes.node_import_milvus import NodeImportMilvus
from app.processor.import_process.exceptions import FileProcessingError, EmbeddingError, MilvusError, ImageProcessingError
from app.utils.task_utils import get_task_status, get_running_task_list

NODES = 'app.processor.import_process.agent.nodes.'


def fake_encode(texts):
    return {'dense': [[0.2, 0.4, 0.8] for _ in texts],
            'sparse': [{4: 0.7} for _ in texts]}


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = ImportConfig(output_root=str(self.root / 'output'), embedding_dim=3, use_llm=True,
                                   max_content_length=50, embedding_batch_size=2,
                                   chunks_collection='chunks', item_name_collection='items')

    def source(self, content='# 万用表\n\n使用方法。' * 20, suffix='.md'):
        path = self.root / ('manual' + suffix)
        path.write_text(content, encoding='utf-8')
        return path

    def initial_state(self, **overrides):
        overrides.setdefault('local_dir', str(self.root / 'runs' / uuid4().hex))
        return create_default_state(**overrides)

    def test_entry_uses_exact_state_directory(self):
        directory = self.root / 'specified' / 'output'
        directory.mkdir(parents=True)
        marker = directory / 'keep.txt'
        marker.write_text('existing', encoding='utf-8')
        state = create_default_state(local_file_path=str(self.source()), local_dir=str(directory))
        result = NodeEntry(self.config)(state)
        self.assertEqual(Path(result['local_dir']), directory.resolve())
        self.assertEqual(Path(result['split_path']), directory.resolve() / 'chunks.json')
        self.assertEqual(Path(result['embeddings_path']), directory.resolve() / 'embeddings.json')
        self.assertEqual(marker.read_text(encoding='utf-8'), 'existing')
        self.assertFalse((self.root / 'output').exists())

    def test_entry_requires_state_directory(self):
        state = create_default_state(local_file_path=str(self.source()))
        with self.assertRaisesRegex(FileProcessingError, 'local_dir'):
            NodeEntry(self.config)(state)
        self.assertFalse((self.root / 'output').exists())

    def entry(self, path):
        return NodeEntry(self.config)(self.initial_state(local_file_path=str(path)))

    def test_markdown_complete_workflow(self):
        client = MagicMock()
        client.has_collection.return_value = False
        with patch(NODES + 'node_item_name_recognition.recognize_item', return_value='{"item_name":"万用表"}'), \
             patch(NODES + 'node_bge_embedding.encode_texts', side_effect=fake_encode) as encode, \
             patch(NODES + 'node_import_milvus.get_client', return_value=client):
            result = KBImportWorkflow(self.config).run(self.initial_state(local_file_path=str(self.source())))
        self.assertGreater(result['imported_count'], 1)
        self.assertEqual(result['imported_count'], len(result['chunks']))
        self.assertEqual(result['item_name'], '万用表')
        self.assertEqual(get_task_status(result['task_id']), 'completed')
        self.assertEqual(get_running_task_list(result['task_id']), [])
        self.assertEqual(len(json.loads(Path(result['embeddings_path']).read_text(encoding='utf-8'))), result['imported_count'])
        self.assertTrue(all(len(c['content']) <= 50 for c in result['chunks']))
        self.assertTrue(all(0 <= c['chunk_id'] < 2**63 for c in result['chunks']))
        self.assertGreater(encode.call_count, 1)
        encoded_texts = [text for call in encode.call_args_list for text in call.args[0]]
        self.assertEqual(encoded_texts[:-1], ['万用表\n' + c['content'] for c in result['chunks']])
        self.assertEqual(encoded_texts[-1], '万用表')
        self.assertTrue(all(c['item_name'] == '万用表' and 'dense_vector' in c for c in result['chunks']))
        saved_chunks = json.loads(Path(result['split_path']).read_text(encoding='utf-8'))
        self.assertTrue(all(c['item_name'] == '万用表' for c in saved_chunks))
        self.assertEqual(client.create_collection.call_count, 2)
        self.assertEqual(client.load_collection.call_count, 2)
        client.close.assert_called_once()

    def test_no_llm_import_uploads_images_without_api_calls(self):
        self.config.use_llm = False
        source = self.source('# 手册\n![插图](image.png)\n连接方式说明。')
        (self.root / 'image.png').write_bytes(b'image')
        client = MagicMock()
        client.has_collection.return_value = False
        with patch(NODES + 'node_md_img.describe_image') as describe, \
             patch(NODES + 'node_md_img.upload_image', return_value='s3://bucket/image') as upload, \
             patch(NODES + 'node_item_name_recognition.recognize_item') as recognize, \
             patch(NODES + 'node_bge_embedding.encode_texts', side_effect=fake_encode), \
             patch(NODES + 'node_import_milvus.get_client', return_value=client):
            result = KBImportWorkflow(self.config).run(self.initial_state(local_file_path=str(source)))
        describe.assert_not_called()
        recognize.assert_not_called()
        upload.assert_called_once()
        self.assertEqual(result['item_name'], source.stem)
        self.assertGreater(result['imported_count'], 0)
        self.assertNotIn('图片说明：', result['md_content'])

    def test_upload_page_runs_import_and_reports_progress(self):
        self.config.use_llm = False
        milvus = MagicMock()
        milvus.has_collection.return_value = False
        with TestClient(app) as client, \
             patch('app.services.imports.get_config', return_value=self.config), \
             patch('app.processor.import_process.agent.node_base.get_config', return_value=self.config), \
             patch.dict('os.environ', {'MINIO_IMPORT_ARCHIVE_BUCKET': ''}), \
             patch(NODES + 'node_bge_embedding.encode_texts', side_effect=fake_encode), \
             patch(NODES + 'node_import_milvus.get_client', return_value=milvus), \
             patch(NODES + 'node_md_img.upload_image') as upload_image:
            self.assertEqual(client.get('/import').status_code, 200)
            self.assertEqual(client.get('/static/import.js').status_code, 200)
            self.assertEqual(client.get('/api/imports/missing').status_code, 404)
            invalid = client.post('/api/imports', files={'files': ('bad.txt', b'text')})
            self.assertEqual(invalid.status_code, 400)
            (self.root / 'outside.png').write_bytes(b'image')
            response = client.post('/api/imports', files=[
                ('files', ('manual.md', b'# Device\n\nUsage instructions.')),
                ('files', ('outside.md', b'![image](../../../outside.png)')),
            ])
            self.assertEqual(response.status_code, 202)
            good_id, bad_id = response.json()['task_ids']
            good = client.get(f'/api/imports/{good_id}').json()
            bad = client.get(f'/api/imports/{bad_id}').json()

        self.assertEqual(good['status'], 'completed')
        self.assertEqual(good['imported_count'], 1)
        self.assertIn('导入向量库', good['done_list'])
        self.assertEqual(good['running_list'], [])
        self.assertEqual(bad['status'], 'failed')
        self.assertIsNone(bad['imported_count'])
        self.assertEqual(bad['running_list'], [])
        self.assertTrue(bad['error'])
        upload_image.assert_not_called()

    def test_pdf_branch_stream(self):
        source = self.source('fake pdf bytes', '.PDF')
        client = MagicMock()
        client.has_collection.return_value = False
        markdown = self.root / 'cloud.md'
        markdown.write_text('# 设备\n\n说明。', encoding='utf-8')
        with patch(NODES + 'node_pdf_to_md.convert_pdf', return_value=markdown) as convert, \
             patch(NODES + 'node_item_name_recognition.recognize_item', return_value='{"item_name":"设备"}'), \
             patch(NODES + 'node_bge_embedding.encode_texts', side_effect=fake_encode), \
             patch(NODES + 'node_import_milvus.get_client', return_value=client):
            updates = list(KBImportWorkflow(self.config).run(self.initial_state(local_file_path=str(source)), stream=True))
        self.assertEqual(len(updates), 7)
        convert.assert_called_once()
        self.assertEqual(updates[-1]['node_import_milvus']['imported_count'], 1)

    def test_entry_missing_unsupported_and_isolated_outputs(self):
        with self.assertRaises(FileProcessingError):
            self.entry(self.root / 'missing.md')
        with self.assertRaises(FileProcessingError):
            self.entry(self.source('bad', '.txt'))
        first = self.entry(self.source())
        second = self.entry(self.source())
        self.assertNotEqual(first['local_dir'], second['local_dir'])
        self.assertTrue(first['is_md_read_enabled'])
        self.assertFalse(first['is_pdf_read_enabled'])

    def test_images_relative_repeated_remote_and_source_preserved(self):
        source_text = '# 手册\n![图](<image one.png>)\n![同图](<image one.png>)\n![远程](https://example.com/a.png)'
        source = self.source(source_text)
        (self.root / 'image one.png').write_bytes(b'fake image')
        state = self.entry(source)
        with patch(NODES + 'node_md_img.describe_image', return_value='红色按钮') as describe, \
             patch(NODES + 'node_md_img.upload_image', return_value='https://storage/image') as upload:
            result = NodeMdImg(self.config)(state)
        self.assertEqual(result['md_content'].count('图片说明：红色按钮'), 2)
        self.assertIn('https://example.com/a.png', result['md_content'])
        self.assertEqual(source.read_text(encoding='utf-8'), source_text)
        describe.assert_called_once()
        self.assertIn('文件标题：manual', describe.call_args.args[1])
        self.assertIn('上文：# 手册', describe.call_args.args[1])
        self.assertIn('下文：', describe.call_args.args[1])
        self.assertIn('![同图]', describe.call_args.args[1])
        upload.assert_called_once()

    def test_missing_image_fails_and_clears_running(self):
        state = self.entry(self.source('![不存在](missing.png)'))
        with self.assertRaises(ImageProcessingError):
            NodeMdImg(self.config)(state)
        self.assertEqual(get_task_status(state['task_id']), 'failed')
        self.assertEqual(get_running_task_list(state['task_id']), [])

    def test_headings_code_and_split_bound(self):
        state = self.entry(self.source('# 父标题\n## 子标题\n' + '正文。' * 60 + '\n```python\n# 代码不是标题\n```'))
        state['md_content'] = Path(state['md_path']).read_text(encoding='utf-8')
        result = NodeDocumentSplit(self.config)(state)
        self.assertTrue(all(len(c['content']) <= 50 for c in result['chunks']))
        child = [c for c in result['chunks'] if c['title'] == '子标题']
        self.assertTrue(child)
        self.assertTrue(all(c['parent_title'] == '父标题' for c in child))
        self.assertFalse(any(c['title'] == '代码不是标题' for c in result['chunks']))

    def test_wrong_embedding_dimension_fails_before_storage(self):
        state = self.entry(self.source())
        state.update(chunks=[{'content': '内容'}], item_name='设备')
        with patch(NODES + 'node_bge_embedding.encode_texts', return_value={'dense': [[1], [1]], 'sparse': [{1: 1}, {1: 1}]}):
            with self.assertRaises(EmbeddingError):
                NodeBgeEmbedding(self.config)(state)
        self.assertEqual(state['imported_count'], 0)

    def test_split_merges_short_siblings_within_length_limit(self):
        self.config.max_content_length = 80
        self.config.min_content_length = 40
        content = '# 第一章\n## 甲\n短内容。\n## 乙\n另一段内容。\n# 第二章\n## 丙\n不同章节。'
        state = self.entry(self.source(content))
        state['md_content'] = content
        result = NodeDocumentSplit(self.config)(state)
        merged = next(c for c in result['chunks'] if '## 甲' in c['content'])
        self.assertIn('## 乙', merged['content'])
        self.assertNotIn('不同章节', merged['content'])
        self.assertTrue(all(len(c['content']) <= 80 for c in result['chunks']))
        self.assertTrue(all(c['parent_title'] for c in result['chunks']))
        self.assertTrue(all(c['metadata']['file_title'] == 'manual' for c in result['chunks']))

    def test_mixed_fences_do_not_expose_code_as_headings(self):
        content = '# 正文\n````python\n~~~\n# 代码标题\n```\n## 仍在代码中\n````\n## 真实标题\n内容。'
        state = self.entry(self.source(content))
        state['md_content'] = content
        self.config.min_content_length = 0
        result = NodeDocumentSplit(self.config)(state)
        titles = {c['title'] for c in result['chunks']}
        self.assertEqual(titles, {'正文', '真实标题'})

    def test_untitled_document_retains_all_content(self):
        content = '没有标题的正文。' * 30
        state = self.entry(self.source(content))
        state['md_content'] = content
        result = NodeDocumentSplit(self.config)(state)
        self.assertTrue(all(c['title'] == 'manual' for c in result['chunks']))
        self.assertTrue(all(len(c['content']) <= 50 for c in result['chunks']))

    def test_storage_error_does_not_report_success(self):
        state = self.entry(self.source())
        state.update(embeddings_content=[{'chunk_id': 1}], item_embedding={})
        client = MagicMock()
        client.upsert.side_effect = RuntimeError('offline')
        with patch(NODES + 'node_import_milvus.get_client', return_value=client), \
             patch('app.clients.milvus_utils.ensure_import_collection'):
            with self.assertRaises(MilvusError):
                NodeImportMilvus(self.config)(state)
        self.assertEqual(state['imported_count'], 0)
        self.assertEqual(get_task_status(state['task_id']), 'failed')
        client.close.assert_called_once()

    def test_real_sdk_schema_construction(self):
        from pymilvus import MilvusClient
        from app.clients.milvus_utils import ensure_import_collection
        client = MagicMock()
        client.has_collection.return_value = False
        client.create_schema.side_effect = MilvusClient.create_schema
        client.prepare_index_params.side_effect = MilvusClient.prepare_index_params
        ensure_import_collection(client, 'chunks', 1024)
        schema = client.create_collection.call_args.kwargs['schema']
        fields = {f.name: f for f in schema.fields}
        self.assertEqual(fields['dense_vector'].params['dim'], 1024)
        self.assertTrue(fields['chunk_id'].is_primary)
        self.assertIn('metadata', fields)
        self.assertFalse(schema.auto_id)

    def test_failed_recognition_uses_title_and_completes_import(self):
        for result in ['not json', '{"item_name":""}', RuntimeError('unavailable')]:
            with self.subTest(result=result):
                client = MagicMock()
                client.has_collection.return_value = False
                with patch(NODES + 'node_item_name_recognition.recognize_item') as recognize, \
                     patch(NODES + 'node_bge_embedding.encode_texts', side_effect=fake_encode), \
                     patch(NODES + 'node_import_milvus.get_client', return_value=client):
                    if isinstance(result, Exception):
                        recognize.side_effect = result
                    else:
                        recognize.return_value = result
                    state = KBImportWorkflow(self.config).run(
                        self.initial_state(local_file_path=str(self.source())))
                self.assertEqual(state['item_name'], 'manual')
                self.assertTrue(all(c['item_name'] == 'manual' for c in state['chunks']))
                self.assertGreater(state['imported_count'], 0)

    def test_bad_pdf_stops_before_following_nodes(self):
        from app.processor.import_process.exceptions import PdfConversionError
        with patch(NODES + 'node_pdf_to_md.convert_pdf', side_effect=ValueError('bad PDF')), \
             patch(NODES + 'node_md_img.describe_image') as describe:
            with self.assertRaises(PdfConversionError):
                KBImportWorkflow(self.config).run(self.initial_state(local_file_path=str(self.source('bad', '.pdf'))))
        describe.assert_not_called()

    def test_incompatible_schema_rejected(self):
        from app.clients.milvus_utils import ensure_import_collection
        client = MagicMock()
        client.has_collection.return_value = True
        client.describe_collection.return_value = {'fields': []}
        with self.assertRaises(ValueError):
            ensure_import_collection(client, 'old', 1024)
        client.create_collection.assert_not_called()


if __name__ == '__main__':
    unittest.main()
