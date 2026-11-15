"""查询服务：从 MongoDB 加载上下文，成功回答后保存整轮对话。"""
from threading import Lock
from uuid import uuid4

from app.clients import query_history
from app.processor.query_process.agent.kb_query_workflow import KBQueryWorkflow
from app.processor.query_process.agent.state import create_default_state
from app.processor.query_process.config import QueryConfig

# 本机 CPU 同时运行两套模型开销较大，串行处理查询；导入服务不受此锁影响。
_query_lock = Lock()


def query(question, session_id=None, top_k=5, use_hyde=True, use_web=False):
    session_id = session_id or uuid4().hex
    with _query_lock:
        history = query_history.get_history(session_id)
        state = KBQueryWorkflow(QueryConfig(top_k=top_k, use_hyde=use_hyde, use_web=use_web)).run(
            create_default_state(original_query=question, session_id=session_id, history=history))
        query_history.save_turn(session_id, state)
    return {key: state.get(key) for key in
            ("task_id", "session_id", "answer", "sources", "item_names", "item_candidates", "rewritten_query", "reranker_used")}
