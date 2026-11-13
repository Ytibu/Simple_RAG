"""使用一个真实资源文件验收导入，保存中间状态，并按文档 ID 回读验证存储。"""
import argparse
import json
from pathlib import Path

from app.processor.import_process import create_default_state
from app.processor.import_process.agent.kb_import_workflow import KBImportWorkflow
from app.processor.import_process.config import get_config


def main():
    parser = argparse.ArgumentParser(description='真实文件导入验收')
    parser.add_argument('--file', type=Path, help='默认选 resource 中最小的 PDF')
    parser.add_argument('--bge-model-path', type=Path, help='已有本地 BGE 模型目录')
    parser.add_argument('--resume', type=Path, help='从已保存状态继续未完成的节点')
    parser.add_argument('--no-llm', action='store_true', help='禁用图片描述及主体识别模型调用')
    args = parser.parse_args()
    if args.bge_model_path:
        from app.conf.embedding_config import embedding_config
        path = args.bge_model_path.resolve()
        if not (path / 'config.json').is_file():
            raise ValueError('指定目录不是有效的本地 BGE 模型目录')
        embedding_config.bge_m3_path = str(path)
    config = get_config()
    if args.no_llm:
        config.use_llm = False
    workflow = KBImportWorkflow(config)
    state = None
    try:
        if args.resume:
            state = json.loads(args.resume.read_text(encoding='utf-8'))
            for record in state['embeddings_content']:
                record['sparse_vector'] = {int(k): v for k, v in record['sparse_vector'].items()}
            if state.get('item_embedding'):
                state['item_embedding']['sparse_vector'] = {
                    int(k): v for k, v in state['item_embedding']['sparse_vector'].items()}
            order = ["node_entry", "node_pdf_to_md", "node_md_img", "node_document_split",
                     "node_item_name_recognition", "node_bge_embedding", "node_import_milvus"]
            last = state.pop("_last_completed_node", None)
            if last is None:
                last = "node_bge_embedding" if state.get("embeddings_content") else (
                    "node_pdf_to_md" if state.get("md_content") else "node_entry")
            for node in order[order.index(last) + 1:]:
                state = getattr(workflow, node)(state)
                print('DONE', node, 'chunks', len(state.get('chunks', [])), flush=True)
                Path(state['local_dir'], 'state.json').write_text(
                    json.dumps({**state, '_last_completed_node': node}, ensure_ascii=False), encoding='utf-8')
        else:
            source = args.file or min(Path('resource').rglob('*.pdf'), key=lambda p: p.stat().st_size)
            print('SOURCE', source, flush=True)
            for update in workflow.run(create_default_state(local_file_path=str(source)), stream=True):
                for node, state in update.items():
                    print('DONE', node, 'chunks', len(state.get('chunks', [])), flush=True)
                    Path(state['local_dir'], 'state.json').write_text(
                        json.dumps({**state, '_last_completed_node': node}, ensure_ascii=False), encoding='utf-8')
        from pymilvus import MilvusClient
        from minio import Minio
        client = MilvusClient(uri=config.milvus_url)
        try:
            expr = 'document_id == ' + json.dumps(state['document_id'])
            chunks = client.query(collection_name=config.chunks_collection, filter=expr,
                                  output_fields=['count(*)'], consistency_level='Strong')[0]['count(*)']
            items = client.query(collection_name=config.item_name_collection, filter=expr,
                                 output_fields=['count(*)'], consistency_level='Strong')[0]['count(*)']
            sample = client.get(collection_name=config.chunks_collection,
                                ids=[state['chunks'][0]['chunk_id']],
                                output_fields=['document_id', 'content', 'dense_vector', 'sparse_vector'],
                                consistency_level='Strong')
            assert chunks == len(state['chunks']) and items == 1 and sample
            assert len(sample[0]['dense_vector']) == config.embedding_dim
            assert sample[0]['content'] == state['chunks'][0]['content']
        finally:
            client.close()
        image_count = state['md_content'].count('s3://')
        objects = []
        if image_count:
            from urllib.parse import urlsplit
            from app.processor.import_process.agent.nodes.node_md_img import IMAGE_PATTERN
            storage = Minio(config.minio_endpoint, access_key=config.minio_access_key,
                            secret_key=config.minio_secret_key, secure=config.minio_secure)
            objects = list(storage.list_objects(config.minio_bucket,
                                                prefix=state['document_id'] + '/images/', recursive=True))
            expected_keys = {urlsplit(match.group(2)).path.lstrip('/')
                             for match in IMAGE_PATTERN.finditer(state['md_content'])
                             if urlsplit(match.group(2)).scheme == 's3'}
            assert {obj.object_name for obj in objects} == expected_keys
            assert all(obj.size > 0 for obj in objects)
        summary = {'document_id': state['document_id'], 'source': state['local_file_path'],
                   'item_name': state['item_name'], 'chunk_count': chunks, 'item_count': items,
                   'dimension': config.embedding_dim, 'image_objects': len(objects),
                   'output_dir': state['local_dir'], 'llm_enabled': config.use_llm, 'status': 'verified'}
        Path(state['local_dir'], 'acceptance.json').write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print('VERIFIED', json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception:
        if state and state.get('local_dir'):
            print('CHECKPOINT', str(Path(state['local_dir'], 'state.json')), flush=True)
        raise


if __name__ == '__main__':
    main()
