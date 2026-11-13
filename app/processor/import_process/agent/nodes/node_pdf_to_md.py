from pathlib import Path

from app.processor.import_process.agent.node_base import NodeBase
from app.processor.import_process.exceptions import PdfConversionError


def convert_pdf(pdf_path, output_dir):
    """使用当前 magic-pdf 0.6 的本地解析接口，模型按 MinerU 自身配置加载。"""
    from magic_pdf.pipe.UNIPipe import UNIPipe
    from magic_pdf.rw.DiskReaderWriter import DiskReaderWriter
    from magic_pdf.libs.MakeContentConfig import DropMode
    import magic_pdf.model as model_config
    # magic-pdf 0.6 默认禁用内置模型；本地解析必须显式开启。
    model_config.__use_inside_model__ = True
    images = Path(output_dir) / "images"
    images.mkdir(parents=True, exist_ok=True)
    pipe = UNIPipe(Path(pdf_path).read_bytes(), {"_pdf_type": "", "model_list": []},
                   DiskReaderWriter(str(images)))
    pipe.pipe_classify()
    pipe.pipe_analyze()
    pipe.pipe_parse()
    return pipe.pipe_mk_markdown("images", drop_mode=DropMode.NONE)


class NodePdfToMd(NodeBase):
    """将 PDF 转为 Markdown，同时保留解析产生的本地图片。"""
    name = "node_pdf_to_md"

    def process(self, state):
        try:
            content = convert_pdf(state["pdf_path"], state["local_dir"])
            if not content or not content.strip():
                raise ValueError("PDF 未解析出有效内容")
            target = Path(state["local_dir"]) / "document.md"
            target.write_text(content, encoding="utf-8")
            state.update(md_path=str(target), md_content=content)
            return state
        except (Exception, SystemExit) as exc:
            raise PdfConversionError("PDF 转换失败，请检查 MinerU 模型及运行依赖",
                                     node_name=self.name, cause=exc) from exc
