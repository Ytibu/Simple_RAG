import json
from app.processor.query_process.agent.node_base import NodeBase
from app.processor.query_process.retrieval import ask_model, search


class NodeItemNameConfirm(NodeBase):
    """结合历史改写问题；主体必须来自已入库的候选名称。"""
    name = "node_item_name_confirm"

    def process(self, state):
        query = state["original_query"].strip()
        if not query:
            raise ValueError("问题不能为空")
        history = state.get("history", [])[-10:]
        rewritten = query
        if history:
            rewritten = ask_model(self.config, "结合历史将最后的问题改写为独立问题，保留型号，勿回答。\n"
                                  + json.dumps(history, ensure_ascii=False) + "\n最后问题：" + query)
        candidates = list(dict.fromkeys(c["item_name"] for c in search(self.config, rewritten, items=True)))
        if not candidates:
            return {"rewritten_query": rewritten, "item_names": [], "answer": "知识库中还没有可查询的文档，请先导入文件。"}
        selected = ask_model(self.config,
            '从候选主体中选择与问题明确对应的主体，可多选。问题无型号且无法唯一确定时返回clarify；'
            '明确询问其他型号返回missing。只有一个候选且问题没有其他型号时可直接选择。'
            '返回JSON：{"status":"selected|clarify|missing","item_names":[]}。\n'
            + json.dumps({"question": rewritten, "candidates": candidates}, ensure_ascii=False), json_mode=True)
        names = selected.get("item_names", [])
        if not isinstance(names, list) or any(name not in candidates for name in names):
            raise ValueError("主体识别返回了不在候选中的名称")
        update = {"rewritten_query": rewritten, "item_names": names, "item_candidates": candidates}
        if selected.get("status") == "selected" and names:
            return update
        update["item_names"] = []
        update["answer"] = ("知识库未找到该型号，请核对产品名称。" if selected.get("status") == "missing"
                            else "请明确要查询的产品：" + "、".join(candidates))
        return update
