from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.imports import router as import_router
from app.api.queries import router as query_router
from app.web.routes import router as web_router

app = FastAPI(title="知识库")
app.include_router(import_router)
app.include_router(query_router)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
