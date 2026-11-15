import os
import shutil
from contextlib import ExitStack
from pathlib import Path, PureWindowsPath
from typing import BinaryIO
from uuid import uuid4

from app.conf.config import get_config
from app.core.logger import logger
from app.utils.task_utils import (
    add_done_task,
    get_done_task_list,
    get_running_task_list,
    get_task_result,
    get_task_status,
    set_task_result,
    update_task_status,
)

SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown"}


def stage_files(files: list[tuple[str, BinaryIO]]) -> list[tuple[str, Path]]:
    """先校验整批名称，再暂存文件并登记任务。"""
    for name, _ in files:
        if (
            Path(name).suffix.lower() not in SUPPORTED_SUFFIXES
            or PureWindowsPath(name).is_reserved()
            or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name)
        ):
            raise ValueError("请上传文件名有效的 PDF 或 Markdown 文档")

    root = Path(get_config().output_root).expanduser().resolve()
    staged = []
    with ExitStack() as cleanup:
        for name, source in files:
            task_id = uuid4().hex
            directory = root / task_id
            directory.mkdir(parents=True)
            cleanup.callback(shutil.rmtree, directory, ignore_errors=True)
            source_dir = directory / "source"
            source_dir.mkdir()
            path = source_dir / name
            with path.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            staged.append((task_id, path))
        cleanup.pop_all()

    for task_id, _ in staged:
        add_done_task(task_id, "upload_file")
        update_task_status(task_id, "pending")
    return staged


def run_import(task_id: str, path: Path) -> None:
    """复用导入图，节点进度由现有节点基类更新。"""
    update_task_status(task_id, "processing")
    try:
        archive_bucket = os.getenv("MINIO_IMPORT_ARCHIVE_BUCKET")
        if archive_bucket:
            try:
                from app.clients.minio_utils import get_minio_client

                get_minio_client().fput_object(archive_bucket, f"{task_id}/{path.name}", str(path))
            except Exception as exc:
                logger.warning("任务 {} 原文件归档失败，继续导入：{}", task_id, exc)

        from app.processor.import_process.agent.kb_import_workflow import KBImportWorkflow
        from app.processor.import_process.agent.state import create_default_state

        state = create_default_state(
            task_id=task_id,
            local_file_path=str(path),
            local_dir=str(path.parent.parent),
        )
        result = KBImportWorkflow.create_and_run(state)
        set_task_result(task_id, "imported_count", str(result["imported_count"]))
        update_task_status(task_id, "completed")
    except Exception:
        logger.exception("任务 {} 导入失败", task_id)
        update_task_status(task_id, "failed")


def get_progress(task_id: str) -> dict | None:
    status = get_task_status(task_id)
    if not status:
        return None
    count = get_task_result(task_id, "imported_count")
    if status == "completed" and not count:
        status = "processing"
    return {
        "status": status,
        "done_list": get_done_task_list(task_id),
        "running_list": get_running_task_list(task_id),
        "imported_count": int(count) if status == "completed" and count else None,
        "error": "导入失败，请根据任务编号查看服务日志" if status == "failed" else None,
    }
