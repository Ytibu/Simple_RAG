from typing import Annotated
import asyncio
import json
from datetime import timedelta
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.core.logger import logger
from app.clients import query_history
from app.services import queries
from app.services import query_tasks

router = APIRouter(tags=["知识库查询"])
SessionId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000, validation_alias=AliasChoices("question", "query"))
    session_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    top_k: int = Field(default=5, ge=1, le=12)
    use_hyde: bool = True
    use_web: bool = False
    is_stream: bool = False

    @field_validator("question")
    @classmethod
    def valid_question(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


def submit_task(request):
    try:
        task = query_tasks.submit(**request.model_dump(exclude={"is_stream"}))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="查询任务已满，请稍后重试") from exc
    return JSONResponse({"session_id": task.session_id, "task_id": task.task_id,
                         "stream_url": f"/api/queries/tasks/{task.task_id}/events"}, status_code=202)


@router.post("/api/queries/submit", status_code=202)
def submit(request: QueryRequest):
    return submit_task(request)


@router.post("/query", include_in_schema=False)
@router.post("/api/queries")
def query(request: QueryRequest):
    if request.is_stream:
        return submit_task(request)
    try:
        return queries.query(**request.model_dump(exclude={"is_stream"}))
    except Exception as exc:
        logger.error("知识库查询失败，异常类型：{}", type(exc).__name__)
        raise HTTPException(status_code=503, detail="查询失败，请检查模型、Milvus 和 MongoDB 服务配置及服务端日志。") from exc


@router.get("/history/{session_id}", include_in_schema=False)
@router.get("/api/queries/{session_id}/history")
def history(session_id: SessionId, limit: int = Query(default=20, ge=1, le=100)):
    try:
        turns = query_history.get_history(session_id, limit=limit)
        items = [message for turn in turns for message in (
            {"role": "user", "text": turn["question"], "item_names": turn.get("item_names", [])},
            {"role": "assistant", "text": turn.get("answer", ""), "sources": turn.get("sources", []),
             "image_urls": turn.get("image_urls", []), "status": turn.get("status", "completed")})]
        return {"session_id": session_id, "history": turns, "items": items}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="读取会话历史失败") from exc


@router.delete("/history/{session_id}", include_in_schema=False)
@router.delete("/api/queries/{session_id}/history")
def delete_history(session_id: SessionId):
    task = query_tasks.latest_task(session_id)
    if task and not task.finished:
        raise HTTPException(status_code=409, detail="请等待当前查询完成后清空历史")
    try:
        return {"deleted_count": query_history.clear_history(session_id)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="清除会话历史失败") from exc


def event_response(task, request):
    if task is None:
        raise HTTPException(status_code=404, detail="查询任务不存在或已过期")
    try:
        cursor = int(request.headers.get("last-event-id", "0"))
        if not 0 <= cursor <= len(task.events):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="事件游标无效") from exc

    async def events():
        nonlocal cursor
        yield "event: ready\ndata: " + json.dumps({"task_id": task.task_id, "session_id": task.session_id}) + "\n\n"
        while not await request.is_disconnected():
            batch, finished = await asyncio.to_thread(task.read, cursor)
            for event, data in batch:
                cursor += 1
                yield f"id: {cursor}\nevent: {event}\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"
            if finished:
                break
            if not batch:
                yield ": heartbeat\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/queries/tasks/{task_id}/events")
def stream_task(task_id: str, request: Request):
    return event_response(query_tasks.get_task(task_id), request)


@router.get("/stream/{session_id}", include_in_schema=False)
def stream_session(session_id: SessionId, request: Request):
    return event_response(query_tasks.latest_task(session_id), request)


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/api/query-images")
def query_image(uri: str):
    from minio import Minio
    from app.conf.config import get_config
    config = get_config()
    try:
        parsed = urlsplit(uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="图片对象地址无效") from exc
    key = parsed.path.lstrip("/")
    if parsed.scheme != "s3" or parsed.netloc != config.minio_bucket or not key or parsed.query or parsed.fragment:
        raise HTTPException(status_code=400, detail="图片对象地址无效")
    try:
        client = Minio(config.minio_endpoint, access_key=config.minio_access_key,
                       secret_key=config.minio_secret_key, secure=config.minio_secure)
        return RedirectResponse(client.presigned_get_object(config.minio_bucket, key,
                                                            expires=timedelta(minutes=10)))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="图片访问暂时不可用") from exc
