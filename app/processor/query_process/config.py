"""查询配置；复用已入库的模型与集合。"""
from dataclasses import dataclass, field
from typing import Callable
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class QueryConfig:
    recall_k: int = 12
    top_k: int = 5
    context_chars: int = 18000
    rrf_k: int = 60
    rrf_weights: tuple[float, float] = (1.0, 1.0)
    rerank_min_k: int = 3
    rerank_gap_abs: float = 0.5
    rerank_gap_ratio: float = 0.25
    event_sink: Callable | None = field(default=None, repr=False)
    use_hyde: bool = True
    use_web: bool = False
    reranker: str = field(default_factory=lambda: os.getenv("QUERY_RERANKER", "auto"))
    model: str = field(default_factory=lambda: os.getenv("LLM_DEFAULT_MODEL", "qwen-flash"))
    milvus_url: str = field(default_factory=lambda: os.getenv("MILVUS_URL", ""))
    chunks_collection: str = field(default_factory=lambda: os.getenv("CHUNKS_COLLECTION", "kb_chunks"))
    item_collection: str = field(default_factory=lambda: os.getenv("ITEM_NAME_COLLECTION", "kb_item_names"))

    def __post_init__(self):
        if not 1 <= self.top_k <= self.recall_k <= 100:
            raise ValueError("查询数量需满足 1 <= top_k <= recall_k <= 100")
        if self.rrf_k <= 0 or self.context_chars <= 0:
            raise ValueError("RRF 参数与上下文长度必须为正数")
        if self.reranker not in {"auto", "local", "llm"}:
            raise ValueError("QUERY_RERANKER 仅支持 auto、local、llm")
        if self.rerank_min_k < 1 or not 0 < self.rerank_gap_abs <= 1 or not 0 < self.rerank_gap_ratio <= 1:
            raise ValueError("动态重排数量和断崖阈值无效")
        if len(self.rrf_weights) != 2 or any(w <= 0 for w in self.rrf_weights):
            raise ValueError("两路 RRF 权重必须为正数")
