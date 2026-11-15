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
    return [{"question": t["question"], "answer": t["answer"], "item_names": t["item_names"]}
            for t in reversed(turns)]


def save_turn(session_id, state):
    get_collection().insert_one({
        "session_id": session_id, "task_id": state["task_id"],
        "question": state["original_query"], "answer": state["answer"],
        "item_names": state["item_names"], "sources": state["sources"],
        "created_at": datetime.now(timezone.utc)})


def clear_history(session_id):
    return get_collection().delete_many({"session_id": session_id}).deleted_count
