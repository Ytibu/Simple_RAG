from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.logger import logger
from app.services import imports

router = APIRouter(tags=["文件导入"])


class ImportSubmission(BaseModel):
    code: int = 200
    message: str
    task_ids: list[str]


class ImportProgress(BaseModel):
    code: int = 200
    task_id: str
    status: str
    done_list: list[str]
    running_list: list[str]
    imported_count: int | None = None
    error: str | None = None


@router.post("/upload", include_in_schema=False)
@router.post("/api/imports", status_code=202)
def upload_files(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)) -> ImportSubmission:
    try:
        staged = imports.stage_files([(file.filename or "", file.file) for file in files])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.error("保存上传文件失败：{}", exc)
        raise HTTPException(status_code=500, detail="保存上传文件失败") from exc

    for task_id, path in staged:
        background_tasks.add_task(imports.run_import, task_id, path)
    return ImportSubmission(
        message=f"已接收 {len(staged)} 个文件",
        task_ids=[task_id for task_id, _ in staged],
    )


@router.get("/status/{task_id}", include_in_schema=False)
@router.get("/api/imports/{task_id}")
def get_import_progress(task_id: str) -> ImportProgress:
    progress = imports.get_progress(task_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return ImportProgress(task_id=task_id, **progress)
