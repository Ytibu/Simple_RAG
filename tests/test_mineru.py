"""MinerU 云 API 协议及 PDF 节点测试，不请求外部服务。"""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from app.conf.mineru_config import MineruConfig
from app.clients.mineru_utils import convert_pdf, extract_result
from app.processor.import_process import ImportConfig, create_default_state
from app.processor.import_process.agent.nodes.node_pdf_to_md import NodePdfToMd
from app.processor.import_process.exceptions import PdfConversionError


class MineruTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'manual.pdf'
        self.source.write_bytes(b'%PDF-test')
        self.output = self.root / 'out'
        self.config = MineruConfig(base_url='https://mineru.test/api/v4/',
                                   api_token='test-secret', poll_interval=0.01)

    def archive(self, entries=None):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            for name, value in (entries or {'full.md': '# 手册\n![图](images/a.png)', 'images/a.png': b'image'}).items():
                archive.writestr(name, value)
        return data.getvalue()

    def response(self, data=None, status=200, content=None):
        result = MagicMock()
        result.status_code = status
        result.json.return_value = {'code': 0, 'data': data}
        result.iter_content.return_value = [content] if content else []
        return result

    def remote(self, results, download=True):
        responses = [self.response({'batch_id': 'batch-one', 'file_urls': ['https://upload.test/signed']}), self.response()]
        responses += [self.response({'extract_result': [r]}) for r in results]
        if download:
            responses.append(self.response(content=self.archive()))
        session = MagicMock()
        session.__enter__.return_value = session
        session.request.side_effect = responses
        return session

    def test_upload_poll_download_preserves_images_and_limits_auth(self):
        session = self.remote([{'state': 'pending'}, {'state': 'running'},
                               {'state': 'done', 'full_zip_url': 'https://download.test/signed'}])
        with patch('app.clients.mineru_utils.requests.Session', return_value=session), \
             patch('app.clients.mineru_utils.time.sleep'):
            md = convert_pdf(self.source, self.output, self.config)
        self.assertEqual(md, (self.output / 'mineru/full.md').resolve())
        self.assertIn('images/a.png', md.read_text(encoding='utf-8'))
        self.assertEqual((md.parent / 'images/a.png').read_bytes(), b'image')
        calls = session.request.call_args_list
        self.assertEqual(calls[0].args, ('POST', 'https://mineru.test/api/v4/file-urls/batch'))
        self.assertEqual(calls[0].kwargs['json']['files'][0]['name'], 'manual.pdf')
        self.assertIn('Authorization', calls[0].kwargs['headers'])
        self.assertNotIn('headers', calls[1].kwargs)  # PUT 不带 token
        self.assertNotIn('headers', calls[-1].kwargs) # 下载不带 token
        self.assertEqual(len(calls), 6)

    def test_missing_token_prevents_request(self):
        self.config.api_token = ''
        with patch('app.clients.mineru_utils.requests.Session') as session:
            with self.assertRaisesRegex(ValueError, 'MINERU_API_TOKEN'):
                convert_pdf(self.source, self.output, self.config)
        session.assert_not_called()

    def test_failed_task_stops_without_download(self):
        session = self.remote([{'state': 'failed', 'err_msg': 'parse failed'}], download=False)
        with patch('app.clients.mineru_utils.requests.Session', return_value=session):
            with self.assertRaisesRegex(RuntimeError, 'parse failed'):
                convert_pdf(self.source, self.output, self.config)
        self.assertEqual(session.request.call_count, 3)
        self.assertFalse((self.output / 'mineru_result.zip').exists())

    def test_http_error_does_not_expose_url_or_token(self):
        session = self.remote([])
        session.request.side_effect = [self.response(status=401)]
        with patch('app.clients.mineru_utils.requests.Session', return_value=session):
            with self.assertRaisesRegex(RuntimeError, '401') as error:
                convert_pdf(self.source, self.output, self.config)
        self.assertNotIn('test-secret', str(error.exception))
        self.assertNotIn('https', str(error.exception))

    def test_api_error_is_redacted(self):
        session = self.remote([])
        response = self.response()
        response.json.return_value = {'code': 'A0202', 'msg': 'invalid test-secret'}
        session.request.side_effect = [response]
        with patch('app.clients.mineru_utils.requests.Session', return_value=session):
            with self.assertRaisesRegex(RuntimeError, 'A0202') as error:
                convert_pdf(self.source, self.output, self.config)
        self.assertNotIn('test-secret', str(error.exception))

    def test_total_timeout_stops(self):
        self.config.task_timeout = 1
        with patch('app.clients.mineru_utils.time.monotonic', side_effect=[0, 2]), \
             patch('app.clients.mineru_utils.requests.Session') as session:
            with self.assertRaises(TimeoutError):
                convert_pdf(self.source, self.output, self.config)
        session.return_value.__enter__.return_value.request.assert_not_called()

    def test_archive_traversal_rejected_before_writing(self):
        path = self.root / 'bad.zip'
        path.write_bytes(self.archive({'full.md': '# ok', '../escape.txt': 'bad'}))
        with self.assertRaisesRegex(ValueError, '不安全'):
            extract_result(path, self.output)
        self.assertFalse((self.root / 'escape.txt').exists())
        self.assertFalse((self.output / 'full.md').exists())

    def test_archive_missing_markdown_rejected(self):
        path = self.root / 'bad.zip'
        path.write_bytes(self.archive({'images/a.png': b'image'}))
        with self.assertRaisesRegex(ValueError, 'full.md'):
            extract_result(path, self.output)

    def test_node_keeps_cloud_markdown_parent(self):
        self.output.mkdir()
        md = self.output / 'full.md'
        md.write_text('# 云端内容', encoding='utf-8')
        config = ImportConfig(mineru_base_url=self.config.base_url,
                              mineru_api_token=self.config.api_token)
        state = create_default_state(pdf_path=str(self.source), local_dir=str(self.output))
        with patch('app.processor.import_process.agent.nodes.node_pdf_to_md.convert_pdf', return_value=md) as remote:
            result = NodePdfToMd(config).process(state)
        self.assertEqual(result['md_path'], str(md))
        self.assertEqual(result['md_content'], '# 云端内容')
        remote.assert_called_once()
        self.assertEqual(remote.call_args.args[2].api_token, self.config.api_token)

    def test_cloud_failure_stops_node(self):
        state = create_default_state(pdf_path=str(self.source), local_dir=str(self.output))
        with patch('app.processor.import_process.agent.nodes.node_pdf_to_md.convert_pdf', side_effect=RuntimeError('failed')) as remote:
            with self.assertRaisesRegex(PdfConversionError, '云端'):
                NodePdfToMd(ImportConfig()).process(state)
        remote.assert_called_once()
        self.assertEqual(state['md_content'], '')

    def test_empty_cloud_result_rejected(self):
        self.output.mkdir()
        md = self.output / 'full.md'
        md.write_text('  ', encoding='utf-8')
        state = create_default_state(pdf_path=str(self.source), local_dir=str(self.output))
        with patch('app.processor.import_process.agent.nodes.node_pdf_to_md.convert_pdf', return_value=md):
            with self.assertRaises(PdfConversionError):
                NodePdfToMd(ImportConfig()).process(state)
        self.assertEqual(state['md_path'], '')


if __name__ == '__main__':
    unittest.main()
