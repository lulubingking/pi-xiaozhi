"""批小智 FastAPI 应用入口。"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.auth import router as auth_router
from app.api.batches import router as batches_router
from app.api.classes import router as classes_router
from app.api.errors import ApiError, api_error_handler, get_request_id, http_exception_handler, validation_error_handler
from app.api.files import router as files_router
from app.api.grading import router as grading_router
from app.api.health import router as health_router
from app.api.ocr import router as ocr_router
from app.api.rubrics import router as rubrics_router
from app.api.reports import router as reports_router
from app.api.settings import router as settings_router
from app.core.utils import new_id
from app.core.config import get_settings
from app.core.runtime import ensure_runtime_directories
from app.core.security import apply_security_headers, install_log_redaction
from app.grading_worker import run_worker as run_grading_worker
from app.ocr_worker import run_worker as run_ocr_worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
install_log_redaction()
logger = logging.getLogger("pixiaozhi.api")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """初始化目录并启动与 API 同进程的批改 Worker。"""

    ensure_runtime_directories(settings.database_url, settings.storage_root)
    app.state.settings = settings
    app.state.storage_root = Path(settings.storage_root)
    stop_event = threading.Event()
    grading_worker = threading.Thread(
        target=run_grading_worker,
        kwargs={"stop_event": stop_event, "poll_interval": 1.0},
        name="grading-worker",
        daemon=True,
    )
    ocr_worker = threading.Thread(
        target=run_ocr_worker,
        kwargs={"stop_event": stop_event, "poll_interval": 1.0},
        name="ocr-worker",
        daemon=True,
    )
    app.state.grading_worker_stop = stop_event
    app.state.grading_worker = grading_worker
    app.state.ocr_worker = ocr_worker
    grading_worker.start()
    ocr_worker.start()
    logger.info(
        "批小智 API started: env=%s version=%s; grading worker and OCR worker started",
        settings.app_env,
        settings.app_version,
    )
    try:
        yield
    finally:
        stop_event.set()
        grading_worker.join(timeout=5)
        ocr_worker.join(timeout=5)
        logger.info("批小智 API stopped; grading worker and OCR worker stopped")


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(classes_router, prefix="/api/v1")
app.include_router(batches_router, prefix="/api/v1")
app.include_router(rubrics_router, prefix="/api/v1")
app.include_router(reports_router)
app.include_router(settings_router, prefix="/api/v1")
app.include_router(grading_router, prefix="/api/v1")
app.include_router(files_router)
app.include_router(ocr_router)
frontend_dir = Path(__file__).resolve().parents[2] / "frontend-dist"
if frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(404, http_exception_handler)
app.add_exception_handler(405, http_exception_handler)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID") or new_id("req")
    response = await call_next(request)
    response.headers["X-Request-ID"] = get_request_id(request)
    return apply_security_headers(response, request.url.path)
