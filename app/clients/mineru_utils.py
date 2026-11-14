"""MinerU v4 云端解析：本地文件上传、任务轮询、结果下载与安全解压。"""
import json
from pathlib import Path
import shutil
import stat
import time
from urllib.parse import urlsplit
from uuid import uuid4
import zipfile

import requests
from app.core.logger import logger

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024


def _https_url(url):
    """解析url是否为有效的HTTPS URL"""
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('MinerU 上传及结果地址必须为有效的 HTTPS URL')
    return url


def _request(session, method, url, timeout, **kwargs):
    """不在异常中输出可能带签名的 URL 或请求凭据。"""
    try:
        response = session.request(method, url, timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError(f'MinerU 网络请求失败：{type(exc).__name__}') from None
    if not 200 <= response.status_code < 300:
        response.close()
        raise RuntimeError(f'MinerU HTTP 请求失败：{response.status_code}')
    return response


def _api_data(response, token):
    try:
        body = response.json()
        if body.get('code') != 0:
            message = str(body.get('msg', '未知错误')).replace(token, '[REDACTED]')[:300]
            raise RuntimeError(f"MinerU API 错误 {body.get('code')}：{message}")
        return body['data']
    finally:
        response.close()


def extract_result(archive_path, output_dir):
    """校验归档所有路径后解压，返回 full.md；图片保留相对目录结构。"""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if sum(info.file_size for info in infos) > MAX_EXTRACTED_BYTES:
            raise ValueError('MinerU 解压结果超过大小限制')
        targets = []
        for info in infos:
            name = info.filename.replace('\\', '/')
            target = (root / name).resolve()
            if not target.is_relative_to(root) or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('MinerU 压缩包包含不安全路径或符号链接')
            targets.append((info, target))
        markdown = [target for info, target in targets if not info.is_dir() and target.name == 'full.md']
        if len(markdown) != 1:
            raise ValueError('MinerU 结果必须包含唯一的 full.md')
        for info, target in targets:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open('wb') as destination:
                    shutil.copyfileobj(source, destination)
    return markdown[0]


def convert_pdf(pdf_path, output_dir, config):
    """按官方 v4 云 API 解析单个本地 PDF。"""
    if not config.base_url or not config.api_token:
        raise ValueError('PDF 解析必须配置 MINERU_BASE_URL 和 MINERU_API_TOKEN')
    if min(config.request_timeout, config.task_timeout, config.poll_interval) <= 0:
        raise ValueError('MinerU 请求超时、任务超时和轮询间隔必须为正数')
    base = config.base_url.rstrip('/')
    _https_url(base)
    source = Path(pdf_path)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    headers = {'Authorization': f'Bearer {config.api_token}'}
    deadline = time.monotonic() + config.task_timeout

    def timeout():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('MinerU 云端解析超过任务总超时')
        return min(config.request_timeout, remaining)

    # Session 不设置全局 Authorization，防止 token 被发送到签名上传或下载域名。
    with requests.Session() as session:
        payload = {'files': [{'name': source.name, 'data_id': uuid4().hex}],
                   'model_version': config.model_version}
        data = _api_data(_request(session, 'POST', base + '/file-urls/batch', timeout(),
                                  headers=headers, json=payload), config.api_token)
        batch_id = data['batch_id']
        urls = data['file_urls']
        if len(urls) != 1:
            raise ValueError('MinerU 必须返回一个文件上传地址')
        marker = directory / 'mineru_task.json'
        marker.write_text(json.dumps({'batch_id': batch_id, 'state': 'uploading'}), encoding='utf-8')
        with source.open('rb') as file:
            _request(session, 'PUT', _https_url(urls[0]), timeout(), data=file).close()
        previous = None
        while True:
            data = _api_data(_request(session, 'GET', base + f'/extract-results/batch/{batch_id}',
                                      timeout(), headers=headers), config.api_token)
            results = data.get('extract_result', [])
            if len(results) != 1:
                raise ValueError('MinerU 返回了非预期的文件结果数量')
            result = results[0]
            state = result['state']
            if state != previous:
                logger.info('MinerU 云端解析状态：{}', state)
                marker.write_text(json.dumps({'batch_id': batch_id, 'state': state}), encoding='utf-8')
                previous = state
            if state == 'done':
                zip_url = _https_url(result['full_zip_url'])
                break
            if state == 'failed':
                message = str(result.get('err_msg', '未知错误')).replace(config.api_token, '[REDACTED]')[:300]
                raise RuntimeError(f'MinerU 解析任务失败：{message}')
            if state not in {'waiting-file', 'pending', 'running', 'converting'}:
                raise RuntimeError(f'MinerU 返回未知任务状态：{state}')
            time.sleep(min(config.poll_interval, timeout()))
        archive_path = directory / 'mineru_result.zip'
        response = _request(session, 'GET', zip_url, timeout(), stream=True)
        try:
            total = 0
            with archive_path.open('wb') as archive:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    timeout()
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError('MinerU 结果压缩包超过大小限制')
                    archive.write(chunk)
        finally:
            response.close()
    return extract_result(archive_path, directory / 'mineru')
