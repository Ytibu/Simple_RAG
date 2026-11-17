"""按会话读取最近对话；客户端懒加载，导入模块不连接数据库。"""
import os
from functools import lru_cache
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()


@lru_cache(maxsize=1)
def get_collection():
    url, database = os.getenv("MONGO_URL"), os.getenv("MONGO_DB_NAME")
    if not url or not database:
        raise ValueError("请配置 MONGO_URL 与 MONGO_DB_NAME")
    client = MongoClient(url, serverSelectionTimeoutMS=5000)
    collection = client[database]["query_turns"]
    collection.create_index([("session_id", 1), ("created_at", -1)])
    return collection


def get_history(session_id, limit=5):
    turns = list(get_collection().find({"session_id": session_id}, {"_id": 0})
                 .sort([("created_at", -1), ("_id", -1)]).limit(limit))
    return [{"question": t["question"], "answer": t.get("answer", ""), "item_names": t.get("item_names", []),
             "task_id": t.get("task_id", ""), "sources": t.get("sources", []),
             "image_urls": t.get("image_urls", []), "item_candidates": t.get("item_candidates", []),
             "rewritten_query": t.get("rewritten_query", ""), "status": t.get("status", "completed")}
            for t in reversed(turns)]


def begin_turn(session_id, state):
    get_collection().insert_one({
        "session_id": session_id, "task_id": state["task_id"], "question": state["original_query"],
        "answer": "", "item_names": [], "sources": [], "status": "processing",
        "created_at": datetime.now(timezone.utc)})


def save_turn(session_id, state):
    get_collection().update_one({"session_id": session_id, "task_id": state["task_id"]}, {"$set": {
        "session_id": session_id, "task_id": state["task_id"],
        "question": state["original_query"], "answer": state["answer"],
        "item_names": state["item_names"], "sources": state["sources"],
        "item_candidates": state.get("item_candidates", []), "image_urls": state.get("image_urls", []),
        "rewritten_query": state.get("rewritten_query", ""), "status": "completed"},
        "$setOnInsert": {"created_at": datetime.now(timezone.utc)}}, upsert=True)


def fail_turn(session_id, task_id):
    get_collection().update_one({"session_id": session_id, "task_id": task_id}, {"$set": {
        "status": "failed", "answer": "本次查询失败，请重新发送问题。"}})


def confirm_history(session_id, history, item_names):
    """仅回填本会话中与确认产品有关的澄清轮次。"""
    ids = [turn["task_id"] for turn in history if turn.get("task_id") and not turn.get("item_names")
           and set(turn.get("item_candidates", [])) & set(item_names)]
    if ids:
        get_collection().update_many({"session_id": session_id, "task_id": {"$in": ids}},
                                     {"$set": {"item_names": item_names}})


def clear_history(session_id):
    return get_collection().delete_many({"session_id": session_id}).deleted_count
