import json
from app.processor.query_process.agent.node_base import NodeBase
from app.processor.query_process.retrieval import ask_model, search


class NodeItemNameConfirm(NodeBase):
    """结合历史提取产品和独立问题，再按库中匹配分数确认或追问。"""
    name = "node_item_name_confirm"

    def process(self, state):
        query = state["original_query"].strip()
        if not query:
            raise ValueError("问题不能为空")
        history = [{key: turn.get(key) for key in ("question", "answer", "item_names", "item_candidates")}
                   for turn in state.get("history", [])[-10:]]
        extracted = ask_model(self.config,
            '结合历史提取当前问题的产品名称并改写为独立问题，支持多个产品。'
            '用户回复候选型号时，补全之前待回答的问题。不要回答问题。'
            '返回JSON {"item_names":[],"rewritten_query":"完整问题"}。\n'
            + json.dumps({"history": history, "question": query}, ensure_ascii=False),
            json_mode=True)
        names = extracted.get("item_names", [])
        rewritten = extracted.get("rewritten_query") or query
        if (not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() for n in names)
                or not isinstance(rewritten, str) or not rewritten.strip()):
            raise ValueError("产品提取或问题改写结果无效")
        confirmed, candidates = [], []
        unresolved = False
        for name in list(dict.fromkeys(names)) or [rewritten]:
            matches = search(self.config, name, items=True)
            # 同一产品可能关联多份手册，按最高匹配分去重。
            scores = {}
            for match in matches:
                product = match["item_name"]
                scores[product] = max(scores.get(product, 0), match.get("score", 0))
            ordered = sorted(scores, key=scores.get, reverse=True)
            high = [product for product in ordered if scores[product] > 0.85]
            options = {product for turn in history for product in (turn.get("item_candidates") or [])}
            explicit = name in options and query == name and name in scores
            if explicit:
                confirmed.append(name)
            elif names and high:
                confirmed.append(name if name in high else high[0])
            else:
                unresolved = True
                candidates.extend(product for product in ordered[:5] if scores[product] >= 0.6)
        update = {"rewritten_query": rewritten.strip(), "item_names": list(dict.fromkeys(confirmed)),
                  "item_candidates": list(dict.fromkeys(candidates))}
        if candidates or (confirmed and unresolved):
            # 多产品问题须全部明确，保留候选供下一轮确认。
            update["item_candidates"] = list(dict.fromkeys(confirmed + candidates))
            update["item_names"] = []
            update["answer"] = "请明确要查询的产品：" + "、".join(update["item_candidates"])
        elif not confirmed:
            update["answer"] = "知识库未找到相关型号，请核对产品名称或先导入对应文件。"
        return update
