import re
from app.processor.query_process.agent.node_base import NodeBase
from app.processor.query_process.retrieval import ask_model


class NodeAnswerOutput(NodeBase):
    name = "node_answer_output"

    def process(self, state):
        if state.get("answer"):
            return {"answer": state["answer"], "sources": []}
        sources, context = [], []
        remaining = self.config.context_chars
        for doc in state.get("reranked_docs", []):
            content = doc["content"][:remaining]
            if not content.strip():
                continue
            number = len(sources) + 1
            source = {key: doc.get(key) for key in
                      ("chunk_id", "document_id", "item_name", "title", "parent_title", "metadata", "rerank_score")}
            source.update(citation=number, content=content)
            sources.append(source)
            context.append(f"[{number}] {doc.get('title', '')}\n{content}")
            remaining -= len(content)
            if remaining <= 0:
                break
        if not sources:
            return {"answer": "知识库中没有找到相关内容，请补充问题或导入对应文档。", "sources": []}
        prompt = ("仅依据以下检索资料，用中文回答问题，在事实后用[1]等编号引用来源。"
                  "资料不足时明确说明，不使用假设答案补全事实；不得引用未提供的编号。\n问题："
                  + state["rewritten_query"] + "\n检索资料：\n" + "\n\n".join(context))
        answer = ask_model(self.config, prompt)
        if any(not 1 <= int(n) <= len(sources) for n in re.findall(r"\[(\d+)\]", answer)):
            raise ValueError("回答包含未提供的来源编号")
        return {"answer": answer, "sources": sources, "prompt": prompt}
