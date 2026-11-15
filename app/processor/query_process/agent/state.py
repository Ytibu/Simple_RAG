"""查询图状态；并行召回分别写入独立字段。"""
import copy
from typing import TypedDict


class QueryGraphState(TypedDict, total=False):
    task_id: str
    session_id: str
    original_query: str
    embedding_chunks: list
    hyde_embedding_chunks: list
    web_search_docs: list
    rrf_chunks: list
    reranked_docs: list
    prompt: str
    answer: str
    sources: list
    item_names: list[str]
    item_candidates: list[str]
    rewritten_query: str
    history: list
    reranker_used: str
    is_stream: bool


graph_default_state = {
    "task_id": "", "session_id": "", "original_query": "",
    "embedding_chunks": [], "hyde_embedding_chunks": [], "web_search_docs": [],
    "rrf_chunks": [], "reranked_docs": [], "prompt": "", "answer": "",
    "sources": [], "item_names": [], "item_candidates": [], "rewritten_query": "",
    "history": [], "reranker_used": "", "is_stream": False,
}


def create_default_state(**overrides):
    state = copy.deepcopy(graph_default_state)
    state.update(overrides)
    return state


def get_default_state():
    return create_default_state()
