import hashlib
import json
from pathlib import Path
import re

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
        chunks = []

        def flush():
            for text in splitter.split_text("\n".join(section)):
                index = len(chunks)
                chunks.append({"chunk_id": int.from_bytes(hashlib.sha256(f"{state['document_id']}:{index}".encode()).digest()[:8], "big") & ((1 << 63) - 1),
                               "content": text, "title": headings[-1][1] if headings else state["file_title"],
                               "parent_title": " / ".join(h[1] for h in headings[:-1]),
                               "metadata": {"document_id": state["document_id"], "index": index,
                                            "source": state["local_file_path"]}})

        fenced = False
        for line in state["md_content"].splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line) if not fenced else None
            if heading:
                flush()
                section = []
                level = len(heading.group(1))
                headings = [h for h in headings if h[0] < level]
                headings.append((level, heading.group(2)))
            section.append(line)
        flush()
        if not chunks:
            raise DocumentSplitError("未生成有效切片", node_name=self.name)
        Path(state["split_path"]).write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        state["chunks"] = chunks
        return state
