import base64
from collections import deque
import mimetypes
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from app.processor.import_process.agent.node_base import NodeBase
from app.processor.import_process.exceptions import ImageProcessingError
from app.utils.rate_limit_utils import apply_api_rate_limit

# 支持普通内联图片；带空格的本地路径需使用 <path> 写法。
IMAGE_PATTERN = re.compile(r'!\[([^\]]*)\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\s*\)')


def describe_image(path, context, model):
    from app.lm.lm_utils import get_llm_client
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    response = get_llm_client(model=model).invoke([{"role": "user", "content": [
        {"type": "text", "text": "请用中文准确描述图片的内容、文字和表格，不要编造。上下文：" + context},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
    ]}])
    if not isinstance(response.content, str) or not response.content.strip():
        raise ValueError("图片描述为空")
    return response.content.strip()


def upload_image(path, object_name, config):
    from minio import Minio
    client = Minio(config.minio_endpoint, access_key=config.minio_access_key,
                   secret_key=config.minio_secret_key, secure=config.minio_secure)
    if not client.bucket_exists(config.minio_bucket):
        client.make_bucket(config.minio_bucket)
    client.fput_object(config.minio_bucket, object_name, str(path),
                       content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    # 持久化对象地址，避免将会过期的签名 URL 写入知识库。
    return f"s3://{config.minio_bucket}/{object_name}"


class NodeMdImg(NodeBase):
    """读取 Markdown，将本地图片上传并补充视觉模型描述；远程图片保留。"""
    name = "node_md_img"

    def process(self, state):
        try:
            source = Path(state["md_path"])
            content = source.read_text(encoding="utf-8-sig")
            cache = {}
            times = deque()

            def replace(match):
                reference = match.group(2).strip("<>")
                if urlsplit(reference).scheme.lower() in {"http", "https", "data"}:
                    return match.group(0)
                path = (source.parent / unquote(reference)).resolve()
                if not path.is_file() or path.suffix.lower() not in self.config.image_extensions:
                    raise ValueError(f"图片不存在或格式不支持：{reference}")
                if path not in cache:
                    context = content[max(0, match.start() - self.config.img_content_length):match.start()]
                    description = ""
                    if self.config.use_llm:
                        apply_api_rate_limit(times, self.config.requests_per_minute)
                        description = describe_image(path, context, self.config.vl_model)
                    object_name = f"{state['document_id']}/images/{len(cache)}{path.suffix.lower()}"
                    url = upload_image(path, object_name, self.config)
                    cache[path] = (url, description)
                url, description = cache[path]
                image = f"![{match.group(1)}]({url})"
                return image + (f"\n\n图片说明：{description}\n" if description else "")

            processed = IMAGE_PATTERN.sub(replace, content)
            if not processed.strip():
                raise ValueError("Markdown 内容为空")
            target = Path(state["local_dir"]) / "processed.md"
            target.write_text(processed, encoding="utf-8")
            state.update(md_content=processed, md_path=str(target))
            return state
        except Exception as exc:
            raise ImageProcessingError("Markdown 图片处理失败", node_name=self.name, cause=exc) from exc
