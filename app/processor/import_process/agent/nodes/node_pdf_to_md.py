from app.clients.mineru_utils import convert_pdf
from app.conf.mineru_config import MineruConfig
from app.processor.import_process.agent.node_base import NodeBase
from app.processor.import_process.exceptions import PdfConversionError


class NodePdfToMd(NodeBase):
    """将 PDF 统一交给 MinerU 云 API，读取并保留结果中的图片相对路径。"""

    name = "node_pdf_to_md"

    def process(self, state):
        try:
            config = MineruConfig(base_url=self.config.mineru_base_url,
                                  api_token=self.config.mineru_api_token)
            target = convert_pdf(state["pdf_path"], state["local_dir"], config)
            content = target.read_text(encoding="utf-8-sig")
            if not content.strip():
                raise ValueError("MinerU 云端未返回有效的 Markdown 内容")
            state.update(md_path=str(target), md_content=content)
            return state
        except Exception as exc:
            raise PdfConversionError("MinerU 云端 PDF 解析失败",
                                     node_name=self.name, cause=exc) from exc
