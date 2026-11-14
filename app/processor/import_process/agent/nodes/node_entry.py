from pathlib import Path
from uuid import uuid4

from app.processor.import_process.agent.node_base import NodeBase
from app.processor.import_process.exceptions import FileProcessingError


class NodeEntry(NodeBase):
    """校验输入，并使用调用方在状态中指定的输出目录。"""
    name = "node_entry"

    def process(self, state):
        raw_path = state.get("local_file_path", "")
        if not raw_path:
            raise FileProcessingError("请提供 local_file_path", node_name=self.name)
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".md", ".markdown"}:
            raise FileProcessingError("输入必须是存在的 PDF 或 Markdown 文件", node_name=self.name)
        raw_directory = state.get("local_dir", "")
        if not raw_directory:
            raise FileProcessingError("请提供 local_dir 输出目录", node_name=self.name)
        directory = Path(raw_directory).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        document_id = uuid4().hex
        is_pdf = path.suffix.lower() == ".pdf"
        state.update(document_id=document_id, task_id=state.get("task_id") or document_id,
                     local_file_path=str(path), local_dir=str(directory), file_title=path.stem,
                     is_pdf_read_enabled=is_pdf, is_md_read_enabled=not is_pdf,
                     pdf_path=str(path) if is_pdf else "", md_path="" if is_pdf else str(path),
                     md_content="", chunks=[], item_name=state.get("item_name", ""), embeddings_content=[],
                     item_embedding={}, imported_count=0,
                     split_path=str(directory / "chunks.json"),
                     embeddings_path=str(directory / "embeddings.json"))
        return state
