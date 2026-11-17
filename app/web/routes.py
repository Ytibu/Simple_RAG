from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)


@router.get("/")
@router.get("/query")
@router.get("/chat.html")
def query_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "pages" / "query.html", media_type="text/html")


@router.get("/import")
@router.get("/import.html")
def import_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "pages" / "import.html", media_type="text/html")
