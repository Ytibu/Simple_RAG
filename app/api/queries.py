from typing import Annotated
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

from app.core.logger import logger
from app.clients import query_history
from app.services import queries

router = APIRouter(prefix="/api/queries", tags=["知识库查询"])
SessionId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    top_k: int = Field(default=5, ge=1, le=12)
    use_hyde: bool = True
    use_web: bool = False

    @field_validator("question")
    @classmethod
    def valid_question(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


@router.post("")
def query(request: QueryRequest):
    try:
        return queries.query(**request.model_dump())
    except Exception as exc:
        logger.error("知识库查询失败，异常类型：{}", type(exc).__name__)
        raise HTTPException(status_code=503, detail="查询失败，请检查模型、Milvus 和 MongoDB 服务配置及服务端日志。") from exc


@router.get("/{session_id}/history")
def history(session_id: SessionId):
    try:
        return {"session_id": session_id, "history": query_history.get_history(session_id, limit=20)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="读取会话历史失败") from exc


@router.delete("/{session_id}/history")
def delete_history(session_id: SessionId):
    try:
        return {"deleted_count": query_history.clear_history(session_id)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="清除会话历史失败") from exc
