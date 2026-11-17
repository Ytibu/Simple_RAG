"""查询服务：从 MongoDB 加载上下文，成功回答后保存整轮对话。"""
from threading import Lock
from uuid import uuid4

from app.clients import query_history
from app.processor.query_process.agent.kb_query_workflow import KBQueryWorkflow
from app.processor.query_process.agent.state import create_default_state
from app.processor.query_process.config import QueryConfig

# 本机 CPU 同时运行两套模型开销较大，串行处理查询；导入服务不受此锁影响。
_query_lock = Lock()


def query(question, session_id=None, top_k=5, use_hyde=True, use_web=False, *, event_sink=None, task_id=None):
    session_id = session_id or uuid4().hex
    with _query_lock:
        history = [turn for turn in query_history.get_history(session_id)
                   if turn.get("status", "completed") == "completed"]
        initial = create_default_state(original_query=question, session_id=session_id,
                                       task_id=task_id or uuid4().hex, history=history)
        query_history.begin_turn(session_id, initial)
        try:
            state = KBQueryWorkflow(QueryConfig(top_k=top_k, use_hyde=use_hyde, use_web=use_web,
                                                event_sink=event_sink)).run(initial)
            query_history.save_turn(session_id, state)
            if state.get("item_names"):
                query_history.confirm_history(session_id, history, state["item_names"])
        except Exception:
            query_history.fail_turn(session_id, initial["task_id"])
            raise
    return {key: state.get(key) for key in
            ("task_id", "session_id", "answer", "sources", "image_urls", "item_names", "item_candidates", "rewritten_query", "reranker_used")}
