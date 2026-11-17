import hashlib
import json
from pathlib import Path
import re

from app.core.logger import logger
from app.processor.import_process.agent.node_base import NodeBase
from app.processor.import_process.exceptions import DocumentSplitError


class NodeDocumentSplit(NodeBase):
    """按 Markdown 标题分节，再按段落及字符边界切分超长内容。"""
    name = "node_document_split"

    def process(self, state):
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        limit = self.config.max_content_length
        if limit <= 0 or not state.get("md_content", "").strip():
            raise DocumentSplitError("切片长度必须为正数，文档不能为空", node_name=self.name)
        if not state.get("is_normal_split_enabled", True) or state.get("is_advanced_split_enabled", False):
            raise DocumentSplitError("目前仅支持普通切分", node_name=self.name)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=limit, chunk_overlap=min(limit // 10, 200),
            separators=["\n\n", "\n", "。", "！", "？", ";", " ", ""])
        headings = []
        section = []
        sections = []

        def flush():
            title = headings[-1][1] if headings else state["file_title"]
            parent = " / ".join(h[1] for h in headings[:-1]) or title
            for part, text in enumerate(splitter.split_text("\n".join(section)), start=1):
                sections.append({"content": text, "title": title, "parent_title": parent,
                                 "metadata": {"file_title": state["file_title"], "part": part}})

        fence = None
        for line in state["md_content"].splitlines():
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if marker:
                token, suffix = marker.groups()
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence) and not suffix.strip():
                    fence = None
                section.append(line)
                continue
            heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line) if fence is None else None
            if heading:
                flush()
                section = []
                level = len(heading.group(1))
                headings = [h for h in headings if h[0] < level]
                headings.append((level, heading.group(2)))
            section.append(line)
        flush()
        chunks = []
        for section in sections:
            previous = chunks[-1] if chunks else None
            if (previous and len(previous["content"]) < self.config.min_content_length
                    and previous["parent_title"] == section["parent_title"]
                    and len(previous["content"]) + 2 + len(section["content"]) <= limit):
                previous["content"] += "\n\n" + section["content"]
            else:
                chunks.append(section)
        if not chunks:
            raise DocumentSplitError("未生成有效切片", node_name=self.name)
        for index, chunk in enumerate(chunks):
            digest = hashlib.sha256(f"{state['document_id']}:{index}".encode()).digest()
            chunk["chunk_id"] = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
            chunk["metadata"].update(document_id=state["document_id"], index=index,
                                     source=state["local_file_path"])
        logger.info("文档切分完成，原文 {} 行，初切 {} 块，合并后 {} 块",
                    len(state["md_content"].splitlines()), len(sections), len(chunks))
        Path(state["split_path"]).write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        state["chunks"] = chunks
        return state
