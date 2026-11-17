"""从实际引用资料提取图片，MinIO 对象通过同源接口获取临时授权。"""
import re
from urllib.parse import quote, urlsplit

from app.processor.import_process.agent.nodes.node_md_img import IMAGE_PATTERN


def extract_images(sources):
    images = []
    for source in sources:
        content = source.get("content", "")
        references = [m.group(2).strip("<>") for m in IMAGE_PATTERN.finditer(content)]
        references += re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', content, re.I)
        for reference in references:
            try:
                parsed = urlsplit(reference)
            except ValueError:
                continue
            if parsed.username or parsed.password:
                continue
            if parsed.scheme == "s3" and parsed.netloc and parsed.path:
                reference = "/api/query-images?uri=" + quote(reference, safe="")
            elif parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if reference not in images:
                images.append(reference)
    return images[:20]
