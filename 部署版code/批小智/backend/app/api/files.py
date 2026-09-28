"""批次文件上传、页面记录、失败重试和通用任务查询。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.batches import batch_validation, get_owned_batch, get_owned_class
from app.api.deps import get_current_user
from app.api.errors import ApiError, success_payload
from app.core.config import get_settings
from app.core.utils import new_id, utc_now, verify_password
from app.db.session import get_db
from app.models import (
    Anomaly,
    AuditLog,
    AnswerPhoto,
    AnswerVersion,
    AssignmentBatch,
    AssignmentGroup,
    BatchQuestion,
    GradingRun,
    GradingPointResult,
    GradingResult,
    GradingTask,
    OcrBlock,
    OcrRun,
    PageGrouping,
    QuestionLayout,
    QuestionAnswer,
    ReportExport,
    ReviewRecord,
    SourceFile,
    SourcePage,
    Task,
    User,
)
from app.schemas import DeleteReviewedBatchRequest, FileRole, PurgeBatchRequest, PurgeFileRequest

router = APIRouter(tags=["files", "tasks"])
settings = get_settings()
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
UPLOAD_CHUNK_SIZE = 1024 * 1024
RETRY_MAX = 3


@dataclass(frozen=True)
class FileInspection:
    extension: str
    mime_type: str
    size_bytes: int
    sha256: str
    page_count: int


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "upload").name.strip()
    if not name or name in {".", ".."}:
        return "upload"
    return name[:255]


def _extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension == ".jpeg":
        return extension
    return extension


def _storage_root() -> Path:
    root = Path(settings.storage_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _storage_key(batch_id: str, file_id: str, extension: str) -> str:
    return f"batches/{batch_id}/files/{file_id}/original{extension}"


def _path_for_key(storage_key: str) -> Path:
    root = _storage_root().resolve()
    candidate = (root / storage_key).resolve()
    if root != candidate and root not in candidate.parents:
        raise ApiError(400, "INVALID_STORAGE_KEY", "文件存储路径校验失败，请重新上传。")
    return candidate


def _copy_upload_to_temp(upload: UploadFile, temp_path: Path) -> tuple[int, str, bytes]:
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    head = bytearray()
    try:
        with temp_path.open("wb") as target:
            while True:
                chunk = upload.file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_file_size:
                    raise ApiError(413, "FILE_TOO_LARGE", f"文件超过 {settings.max_file_size} 字节限制，请压缩后重试。")
                digest.update(chunk)
                if len(head) < 16:
                    head.extend(chunk[: 16 - len(head)])
                target.write(chunk)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    if size == 0:
        temp_path.unlink(missing_ok=True)
        raise ApiError(422, "FILE_EMPTY", "上传文件为空，请选择有效的 JPG、PNG 或 PDF 文件。")
    return size, digest.hexdigest(), bytes(head)


def _inspect_file(temp_path: Path, filename: str, size: int, sha256: str, head: bytes) -> FileInspection:
    extension = _extension(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "仅支持 JPG、PNG 和 PDF 文件，请更换文件后重试。")

    if head.startswith(b"%PDF-"):
        actual_mime = "application/pdf"
        if extension != ".pdf":
            raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "文件扩展名与实际 PDF 内容不一致，请重新命名或重新导出。")
        try:
            reader = PdfReader(str(temp_path), strict=False)
            page_count = len(reader.pages)
        except Exception as exc:
            raise ApiError(422, "FILE_CORRUPTED", "PDF 文件无法读取，未生成页面记录，请重新导出后上传。") from exc
        if page_count < 1:
            raise ApiError(422, "FILE_CORRUPTED", "PDF 文件没有可读取页面，请重新导出后上传。")
        return FileInspection(extension, actual_mime, size, sha256, page_count)

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        actual_mime = "image/png"
    elif head.startswith(b"\xff\xd8\xff"):
        actual_mime = "image/jpeg"
    else:
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "文件头不是受支持的 JPG、PNG 或 PDF，请检查文件内容。")

    allowed_image_extensions = {".png"} if actual_mime == "image/png" else {".jpg", ".jpeg"}
    if extension not in allowed_image_extensions:
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "文件扩展名与实际图片内容不一致，请重新上传。")
    try:
        with Image.open(temp_path) as image:
            image.verify()
        with Image.open(temp_path) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ApiError(422, "FILE_CORRUPTED", "图片无法解码，未生成页面记录，请重新导出后上传。") from exc
    return FileInspection(extension, actual_mime, size, sha256, 1)


def _file_payload(source_file: SourceFile, task: Task | None = None) -> dict:
    result = {
        "file_id": source_file.id,
        "status": source_file.status,
        "original_name": source_file.original_name,
        "mime_type": source_file.mime_type,
        "extension": source_file.extension,
        "file_role": source_file.file_role,
        "size_bytes": source_file.size_bytes,
        "sha256": source_file.sha256,
        "failure_code": source_file.failure_code,
        "page_count": source_file.page_count,
        "created_at": source_file.created_at,
    }
    if task:
        result["task_id"] = task.id
    return result


def _task_payload(task: Task, source_file: SourceFile | None = None) -> dict:
    try:
        error_items = json.loads(task.error_items_json) if task.error_items_json else []
    except json.JSONDecodeError:
        error_items = []
    try:
        options = json.loads(task.options_json) if task.options_json else {}
    except json.JSONDecodeError:
        options = {}
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "batch_id": task.batch_id,
        "resource_id": task.resource_id,
        "status": task.status,
        "stage": task.stage,
        "progress_current": task.progress_current,
        "progress_total": task.progress_total,
        "success_count": 1 if source_file and source_file.status == "success" else 0,
        "failed_count": 1 if source_file and source_file.status == "failed" else 0,
        "retry_count": task.retry_count,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "error_items": error_items,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "options": options,
    }


def _refresh_batch_status(db: Session, batch: AssignmentBatch) -> None:
    success_count = db.scalar(select(func.count(SourceFile.id)).where(SourceFile.batch_id == batch.id, SourceFile.status == "success")) or 0
    failed_count = db.scalar(select(func.count(SourceFile.id)).where(SourceFile.batch_id == batch.id, SourceFile.status == "failed")) or 0
    if success_count and failed_count:
        batch.status = "partial_failure"
    elif success_count:
        batch.status = "pending_ocr"
    elif failed_count:
        batch.status = "partial_failure"
    else:
        batch.status = "draft"
    batch.updated_at = utc_now()
    batch.version += 1


def _active_file_count(db: Session, batch_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(SourceFile.id)).where(
                SourceFile.batch_id == batch_id,
                SourceFile.status.in_(["pending", "validating", "success"]),
            )
        )
        or 0
    )


def _find_idempotent_task(db: Session, batch_id: str, key: str | None) -> tuple[Task, SourceFile] | None:
    if not key:
        return None
    task = db.scalar(
        select(Task).where(Task.batch_id == batch_id, Task.task_type == "upload", Task.idempotency_key == key)
    )
    if not task or not task.resource_id:
        return None
    source_file = db.get(SourceFile, task.resource_id)
    return (task, source_file) if source_file else None


def _create_task(task_id: str, batch_id: str, resource_id: str, idempotency_key: str | None, now: str) -> Task:
    return Task(
        id=task_id,
        task_type="upload",
        batch_id=batch_id,
        assignment_group_id=None,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        status="running",
        stage="saving",
        progress_current=0,
        progress_total=1,
        retry_count=0,
        max_retries=RETRY_MAX,
        error_code=None,
        error_message=None,
        error_items_json="[]",
        created_at=now,
        started_at=now,
        finished_at=None,
        worker_id=None,
        heartbeat_at=now,
    )


def _failed_upload(
    db: Session,
    batch: AssignmentBatch,
    file_id: str,
    task: Task,
    filename: str,
    extension: str,
    size: int,
    sha256: str,
    failure_code: str,
    failure_message: str,
    file_role: FileRole,
    now: str,
) -> tuple[SourceFile, Task]:
    source_file = SourceFile(
        id=file_id,
        batch_id=batch.id,
        original_name=filename,
        storage_key=_storage_key(batch.id, file_id, extension or ".bin"),
        mime_type="application/octet-stream",
        extension=extension or ".bin",
        size_bytes=max(size, 1),
        sha256=sha256,
        file_role=file_role,
        status="failed",
        failure_code=failure_code,
        page_count=None,
        created_at=now,
    )
    task.status = "failed"
    task.stage = "validating"
    task.progress_current = 0
    task.error_code = failure_code
    task.error_message = failure_message
    task.error_items_json = json.dumps([{"file_id": file_id, "code": failure_code}], ensure_ascii=False)
    task.finished_at = now
    task.heartbeat_at = now
    db.add(source_file)
    db.add(task)
    return source_file, task


def _ingest_upload(
    db: Session,
    batch: AssignmentBatch,
    upload: UploadFile,
    idempotency_key: str | None,
    file_role: FileRole,
) -> tuple[SourceFile, Task]:
    filename = _safe_filename(upload.filename)
    extension = _extension(filename)
    file_id = new_id("file")
    task_id = new_id("task")
    now = utc_now()
    task = _create_task(task_id, batch.id, file_id, idempotency_key, now)
    task.options_json = json.dumps({"file_role": file_role}, ensure_ascii=False)
    temp_path = _storage_root() / ".tmp" / f"{file_id}.upload"
    final_path: Path | None = None
    try:
        size, sha256, head = _copy_upload_to_temp(upload, temp_path)
        existing = db.scalar(
            select(SourceFile).where(
                SourceFile.batch_id == batch.id,
                SourceFile.file_role == file_role,
                SourceFile.sha256 == sha256,
                SourceFile.status.in_(["pending", "validating", "success"]),
            )
        )
        if existing:
            temp_path.unlink(missing_ok=True)
            raise ApiError(409, "DUPLICATE_FILE", "该批次已存在同用途、相同内容的文件，请勿重复上传。")
        try:
            inspection = _inspect_file(temp_path, filename, size, sha256, head)
        except ApiError as exc:
            temp_path.unlink(missing_ok=True)
            source_file, failed_task = _failed_upload(
                db, batch, file_id, task, filename, extension, size, sha256, exc.code, exc.message, file_role, now
            )
            _refresh_batch_status(db, batch)
            db.commit()
            return source_file, failed_task

        storage_key = _storage_key(batch.id, file_id, inspection.extension)
        final_path = _path_for_key(storage_key)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final_path)

        source_file = SourceFile(
            id=file_id,
            batch_id=batch.id,
            original_name=filename,
            storage_key=storage_key,
            mime_type=inspection.mime_type,
            extension=inspection.extension,
            size_bytes=inspection.size_bytes,
            sha256=inspection.sha256,
            file_role=file_role,
            status="success",
            failure_code=None,
            page_count=inspection.page_count,
            created_at=now,
        )
        db.add(source_file)
        db.flush()
        pages = [
            SourcePage(
                id=new_id("page"),
                source_file_id=source_file.id,
                batch_id=batch.id,
                page_index=index,
                original_storage_key=storage_key,
                processed_storage_key=None,
                transform_json=None,
                status="pending",
                created_at=now,
                updated_at=now,
            )
            for index in range(1, inspection.page_count + 1)
        ]
        task.status = "succeeded"
        task.stage = "saving"
        task.progress_current = 1
        task.finished_at = utc_now()
        task.heartbeat_at = task.finished_at
        db.add(task)
        db.add_all(pages)
        _refresh_batch_status(db, batch)
        db.commit()
        return source_file, task
    except ApiError:
        if final_path and final_path.exists():
            final_path.unlink(missing_ok=True)
        temp_path.unlink(missing_ok=True)
        raise
    except (OSError, IntegrityError) as exc:
        db.rollback()
        if final_path and final_path.exists():
            final_path.unlink(missing_ok=True)
        temp_path.unlink(missing_ok=True)
        raise ApiError(500, "INTERNAL_ERROR", "文件保存未完成，未提交文件记录，请稍后重试。") from exc


def _ensure_uploadable_batch(db: Session, user: User, batch_id: str) -> AssignmentBatch:
    batch = get_owned_batch(db, user.id, batch_id)
    if batch.status in {"archived", "grading", "reviewed"}:
        raise ApiError(409, "BATCH_UPLOAD_LOCKED", "当前批次已进入不可追加上传的阶段，请新建批次后重试。")
    room = get_owned_class(db, user.id, batch.class_id, require_active=False)
    questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id)))
    validation = batch_validation(db, batch, questions, room)
    if not validation["valid_for_upload"]:
        raise ApiError(422, "BATCH_NOT_READY_FOR_UPLOAD", "批次配置尚未满足上传条件，请先修正 errors 中的问题。", validation["errors"])
    if _active_file_count(db, batch.id) >= settings.max_file_count:
        raise ApiError(413, "FILE_COUNT_EXCEEDED", f"单个批次最多上传 {settings.max_file_count} 个文件。")
    return batch


def _upload_file_impl(
    batch_id: str,
    request: Request,
    file: UploadFile,
    idempotency_key: str | None,
    user: User,
    db: Session,
    file_role: FileRole,
):
    batch = _ensure_uploadable_batch(db, user, batch_id)
    existing = _find_idempotent_task(db, batch.id, idempotency_key)
    if existing:
        task, source_file = existing
        existing_options = json.loads(task.options_json) if task.options_json else {}
        existing_role = existing_options.get("file_role", "student_work") if isinstance(existing_options, dict) else "student_work"
        if existing_role != file_role:
            raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "相同幂等键已用于其他文件类型，请更换幂等键后重试。")
        return success_payload(request, _file_payload(source_file, task))
    batch.status = "uploading"
    batch.updated_at = utc_now()
    db.commit()
    try:
        source_file, task = _ingest_upload(db, batch, file, idempotency_key, file_role)
    except ApiError:
        _refresh_batch_status(db, batch)
        db.commit()
        raise
    return success_payload(request, _file_payload(source_file, task))


@router.post("/api/v1/batches/{batch_id}/files", status_code=202)
def upload_file(
    batch_id: str,
    request: Request,
    file: UploadFile = File(...),
    file_role: FileRole = Query(default="student_work"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _upload_file_impl(batch_id, request, file, idempotency_key, user, db, file_role)


@router.post("/api/v1/batches/{batch_id}/question-paper", status_code=202)
def upload_question_paper(
    batch_id: str,
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _upload_file_impl(batch_id, request, file, idempotency_key, user, db, "question_paper")


@router.get("/api/v1/batches/{batch_id}/files")
def list_files(
    batch_id: str,
    request: Request,
    status: str | None = Query(default=None),
    file_role: FileRole | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    query = select(SourceFile).where(SourceFile.batch_id == batch.id).order_by(SourceFile.created_at.desc())
    if status:
        query = query.where(SourceFile.status == status)
    if file_role:
        query = query.where(SourceFile.file_role == file_role)
    files = list(db.scalars(query))
    task_by_resource = {
        task.resource_id: task
        for task in db.scalars(select(Task).where(Task.batch_id == batch.id, Task.task_type == "upload"))
        if task.resource_id
    }
    data = [_file_payload(item, task_by_resource.get(item.id)) for item in files]
    return success_payload(request, data, meta={"page": 1, "page_size": len(data), "total": len(data)})


@router.delete("/api/v1/files/{file_id}")
def delete_file(file_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除尚未进入 OCR/批改链路的学生作业原文件。"""

    row = db.execute(
        select(SourceFile, AssignmentBatch)
        .join(AssignmentBatch, SourceFile.batch_id == AssignmentBatch.id)
        .where(SourceFile.id == file_id, AssignmentBatch.owner_user_id == user.id)
    ).first()
    if not row:
        raise ApiError(404, "FILE_NOT_FOUND", "文件不存在或当前账号无权访问。")
    source_file, batch = row
    if source_file.file_role != "student_work":
        raise ApiError(409, "FILE_DELETE_NOT_ALLOWED", "试卷文件不能从学生作业列表删除，请在 OCR 校对页维护。")
    if batch.status not in {"draft", "uploading", "pending_ocr", "partial_failure"}:
        raise ApiError(409, "FILE_DELETE_LOCKED", "当前批次已进入 OCR、批改或复核流程，不能删除作业文件。")
    if source_file.status not in {"success", "failed"}:
        raise ApiError(409, "FILE_DELETE_NOT_READY", "文件仍在保存处理中，请稍后再试。")

    pages = list(db.scalars(select(SourcePage).where(SourcePage.source_file_id == source_file.id)))
    page_ids = [page.id for page in pages]
    if page_ids:
        dependent_checks = (
            (PageGrouping, PageGrouping.source_page_id),
            (OcrRun, OcrRun.source_page_id),
            (OcrBlock, OcrBlock.source_page_id),
            (AnswerVersion, AnswerVersion.source_page_id),
            (AnswerPhoto, AnswerPhoto.source_page_id),
            (Anomaly, Anomaly.source_page_id),
        )
        if any(db.scalar(select(model.id).where(column.in_(page_ids)).limit(1)) for model, column in dependent_checks):
            raise ApiError(409, "FILE_DELETE_LOCKED", "文件已经进入 OCR 或批改链路，不能删除；请先处理对应批次。")

    storage_path = _path_for_key(source_file.storage_key)
    try:
        db.execute(delete(Task).where(Task.task_type == "upload", Task.resource_id == source_file.id))
        if pages:
            db.execute(delete(SourcePage).where(SourcePage.source_file_id == source_file.id))
        db.delete(source_file)
        _refresh_batch_status(db, batch)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "FILE_DELETE_CONFLICT", "文件已有新的业务关联，暂时不能删除。") from exc

    try:
        storage_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("source file content cleanup failed after database deletion: %s", storage_path)
    return success_payload(request, {"deleted": True, "file_id": file_id, "batch_id": batch.id})


@router.post("/api/v1/files/{file_id}/purge")
def purge_file(
    file_id: str,
    payload: PurgeFileRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """撤销 OCR 并彻底删除尚未进入批改链路的作业或试卷文件。"""

    row = db.execute(
        select(SourceFile, AssignmentBatch)
        .join(AssignmentBatch, SourceFile.batch_id == AssignmentBatch.id)
        .where(SourceFile.id == file_id, AssignmentBatch.owner_user_id == user.id)
    ).first()
    if not row:
        raise ApiError(404, "FILE_NOT_FOUND", "文件不存在或当前账号无权访问。")
    source_file, batch = row
    if source_file.file_role not in {"student_work", "question_paper"}:
        raise ApiError(409, "FILE_PURGE_NOT_ALLOWED", "当前文件类型不支持从此入口彻底删除。")
    if payload.confirm_name != source_file.original_name:
        raise ApiError(422, "FILE_PURGE_CONFIRMATION_INVALID", "确认文件名与原文件名不一致，未执行删除。")
    if batch.status in {"grading", "pending_review", "reviewed", "archived"}:
        raise ApiError(409, "FILE_PURGE_LOCKED", "当前批次已经进入批改或复核阶段，不能彻底删除文件。")
    if source_file.status not in {"success", "failed"}:
        raise ApiError(409, "FILE_PURGE_NOT_READY", "文件仍在上传或保存处理中，请稍后再试。")

    pages = list(db.scalars(select(SourcePage).where(SourcePage.source_file_id == source_file.id)))
    page_ids = {page.id for page in pages}
    if not page_ids:
        storage_keys = {source_file.storage_key}
        db.execute(delete(Task).where(Task.task_type == "upload", Task.resource_id == source_file.id))
        db.delete(source_file)
        _refresh_batch_status(db, batch)
        db.commit()
        for storage_key in storage_keys:
            try:
                _path_for_key(storage_key).unlink(missing_ok=True)
            except OSError:
                logger.warning("source file content cleanup failed after purge: %s", storage_key)
        return success_payload(request, {"purged": True, "file_id": file_id, "batch_id": batch.id})

    if source_file.file_role == "student_work":
        if db.scalar(select(PageGrouping.id).where(PageGrouping.source_page_id.in_(page_ids)).limit(1)):
            raise ApiError(409, "FILE_PURGE_STUDENT_LINKED", "文件已经绑定学生或页序，不能彻底删除；请先解除归属。")
        if db.scalar(select(AnswerVersion.id).where(AnswerVersion.source_page_id.in_(page_ids)).limit(1)):
            raise ApiError(409, "FILE_PURGE_ANSWER_SAVED", "文件已经保存学生答案，不能彻底删除；请保留批改历史。")
    elif db.scalar(select(OcrBlock.id).where(OcrBlock.source_page_id.in_(page_ids), OcrBlock.question_id.is_not(None)).limit(1)):
        raise ApiError(409, "FILE_PURGE_QUESTION_LINKED", "试卷 OCR 文本块已经绑定题干，不能删除；请保留题干来源和校对记录。")

    ocr_runs = list(db.scalars(select(OcrRun).where(OcrRun.source_page_id.in_(page_ids))))
    ocr_run_ids = {run.id for run in ocr_runs}
    ocr_tasks = list(db.scalars(select(Task).where(Task.batch_id == batch.id, Task.task_type == "ocr")))
    related_ocr_tasks: list[Task] = []
    for task in ocr_tasks:
        try:
            options = json.loads(task.options_json) if task.options_json else {}
        except json.JSONDecodeError:
            options = {}
        option_run_ids = set(options.get("ocr_run_ids", [])) if isinstance(options, dict) else set()
        if task.resource_id in ocr_run_ids:
            option_run_ids.add(task.resource_id)
        if option_run_ids & ocr_run_ids:
            related_ocr_tasks.append(task)
            if task.status in {"queued", "running"} or any(run.status in {"queued", "running"} for run in ocr_runs):
                raise ApiError(409, "FILE_PURGE_RUNNING", "OCR 任务仍在排队或运行中，请等待任务结束后再彻底删除。")

    answer_photos = list(db.scalars(select(AnswerPhoto).where(AnswerPhoto.source_page_id.in_(page_ids))))
    answer_photo_ids = {photo.id for photo in answer_photos}
    storage_keys = {source_file.storage_key}
    storage_keys.update(page.processed_storage_key for page in pages if page.processed_storage_key)
    storage_keys.update(run.raw_output_storage_key for run in ocr_runs if run.raw_output_storage_key)
    for photo in answer_photos:
        storage_keys.add(photo.original_storage_key)
        if photo.processed_storage_key:
            storage_keys.add(photo.processed_storage_key)
        if photo.raw_output_storage_key:
            storage_keys.add(photo.raw_output_storage_key)

    try:
        if source_file.file_role == "question_paper":
            layout = db.scalar(select(QuestionLayout).where(QuestionLayout.batch_id == batch.id))
            if layout is not None:
                db.execute(delete(ReviewRecord).where(ReviewRecord.entity_id == layout.id))
                db.execute(delete(QuestionLayout).where(QuestionLayout.id == layout.id))
        db.execute(delete(OcrBlock).where(OcrBlock.source_page_id.in_(page_ids)))
        db.execute(delete(Anomaly).where(Anomaly.source_page_id.in_(page_ids)))
        if answer_photo_ids:
            db.execute(delete(Task).where(Task.task_type == "answer_photo_ocr", Task.resource_id.in_(answer_photo_ids)))
            db.execute(delete(AnswerPhoto).where(AnswerPhoto.id.in_(answer_photo_ids)))
        db.execute(delete(OcrRun).where(OcrRun.id.in_(ocr_run_ids)))
        db.execute(delete(Task).where(Task.task_type == "upload", Task.resource_id == source_file.id))
        for task in related_ocr_tasks:
            try:
                options = json.loads(task.options_json) if task.options_json else {}
            except json.JSONDecodeError:
                options = {}
            if not isinstance(options, dict):
                options = {}
            remaining_run_ids = [run_id for run_id in options.get("ocr_run_ids", []) if run_id not in ocr_run_ids]
            if remaining_run_ids:
                options["ocr_run_ids"] = remaining_run_ids
                task.options_json = json.dumps(options, ensure_ascii=False)
                task.resource_id = None if task.resource_id in ocr_run_ids else task.resource_id
                task.progress_total = len(remaining_run_ids)
                task.progress_current = min(task.progress_current, task.progress_total)
                try:
                    errors = json.loads(task.error_items_json) if task.error_items_json else []
                except json.JSONDecodeError:
                    errors = []
                if isinstance(errors, list):
                    task.error_items_json = json.dumps(
                        [item for item in errors if not isinstance(item, dict) or item.get("ocr_run_id") not in ocr_run_ids],
                        ensure_ascii=False,
                    )
            else:
                db.delete(task)
        db.execute(delete(SourcePage).where(SourcePage.id.in_(page_ids)))
        db.delete(source_file)
        remaining_page_statuses = list(db.scalars(select(SourcePage.status).where(SourcePage.batch_id == batch.id)))
        if any(status in {"ocr_ready", "needs_review", "confirmed"} for status in remaining_page_statuses):
            batch.status = "pending_correction"
            batch.updated_at = utc_now()
            batch.version += 1
        else:
            _refresh_batch_status(db, batch)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "FILE_PURGE_CONFLICT", "文件仍有未处理的业务关联，未执行彻底删除。") from exc

    for storage_key in storage_keys:
        try:
            _path_for_key(storage_key).unlink(missing_ok=True)
        except OSError:
            logger.warning("storage cleanup failed after file purge: %s", storage_key)
    return success_payload(request, {"purged": True, "file_id": file_id, "batch_id": batch.id})


@router.post("/api/v1/batches/{batch_id}/purge")
def purge_batch(
    batch_id: str,
    payload: PurgeBatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """撤销 OCR 并彻底删除一个尚未进入批改链路的错误批次。"""

    batch = get_owned_batch(db, user.id, batch_id)
    if payload.confirm_name != batch.title:
        raise ApiError(422, "BATCH_PURGE_CONFIRMATION_INVALID", "确认批次名称与原批次名称不一致，未执行删除。")
    if batch.status not in {"uploading", "pending_ocr", "ocr_processing", "pending_correction", "partial_failure"}:
        raise ApiError(409, "BATCH_PURGE_STAGE_LOCKED", "只有上传或 OCR 阶段的批次可以撤销；批改、复核或已归档批次必须保留历史。")

    source_files = list(db.scalars(select(SourceFile).where(SourceFile.batch_id == batch.id)))
    pages = list(db.scalars(select(SourcePage).where(SourcePage.batch_id == batch.id)))
    page_ids = {page.id for page in pages}
    question_ids = set(db.scalars(select(BatchQuestion.id).where(BatchQuestion.batch_id == batch.id)))
    assignment_group_ids = set(db.scalars(select(AssignmentGroup.id).where(AssignmentGroup.batch_id == batch.id)))
    grouping_ids = set(
        db.scalars(select(PageGrouping.id).where(PageGrouping.source_page_id.in_(page_ids))).all()
    ) if page_ids else set()
    batch_storage_path = _path_for_key(f"batches/{batch.id}")

    active_tasks = list(
        db.scalars(
            select(Task).where(Task.batch_id == batch.id, Task.status.in_({"queued", "running"}))
        )
    )
    if active_tasks:
        raise ApiError(409, "BATCH_PURGE_RUNNING", "批次仍有 OCR 或上传任务在排队/运行，请等待任务结束后再撤销。")

    answer_version_filter = []
    if page_ids:
        answer_version_filter.append(AnswerVersion.source_page_id.in_(page_ids))
    if assignment_group_ids:
        answer_version_filter.append(AnswerVersion.assignment_group_id.in_(assignment_group_ids))
    if answer_version_filter and db.scalar(select(AnswerVersion.id).where(or_(*answer_version_filter)).limit(1)):
        raise ApiError(409, "BATCH_PURGE_ANSWER_SAVED", "批次已经保存学生答案，不能彻底删除；请保留批改历史。")
    if db.scalar(select(AnswerPhoto.id).where(AnswerPhoto.batch_id == batch.id).limit(1)):
        raise ApiError(409, "BATCH_PURGE_ANSWER_SAVED", "批次已经保存答案照片，不能彻底删除；请保留批改历史。")
    if db.scalar(select(QuestionAnswer.id).join(AssignmentGroup, QuestionAnswer.assignment_group_id == AssignmentGroup.id).where(AssignmentGroup.batch_id == batch.id).limit(1)):
        raise ApiError(409, "BATCH_PURGE_ANSWER_SAVED", "批次已经形成题目答案记录，不能彻底删除；请保留批改历史。")
    if db.scalar(select(GradingRun.id).where(GradingRun.batch_id == batch.id).limit(1)):
        raise ApiError(409, "BATCH_PURGE_GRADING_EXISTS", "批次已经进入批改链路，不能彻底删除；请保留批改历史。")
    if db.scalar(
        select(GradingTask.id)
        .join(GradingRun, GradingTask.grading_run_id == GradingRun.id)
        .where(GradingRun.batch_id == batch.id)
        .limit(1)
    ):
        raise ApiError(409, "BATCH_PURGE_GRADING_EXISTS", "批次已经进入批改链路，不能彻底删除；请保留批改历史。")
    if db.scalar(select(ReportExport.id).where(ReportExport.batch_id == batch.id).limit(1)):
        raise ApiError(409, "BATCH_PURGE_REPORT_EXISTS", "批次已经产生报告导出记录，不能彻底删除；请保留历史。")

    review_entity_ids = {batch.id, *page_ids, *question_ids, *assignment_group_ids, *grouping_ids}
    layout = db.scalar(select(QuestionLayout).where(QuestionLayout.batch_id == batch.id))
    if layout is not None:
        review_entity_ids.add(layout.id)
    related_reviews = list(db.scalars(select(ReviewRecord).where(ReviewRecord.entity_id.in_(review_entity_ids))))
    blocking_reviews = [item for item in related_reviews if item.entity_type not in {"grouping", "question_prompt", "question_layout"}]
    if blocking_reviews:
        raise ApiError(409, "BATCH_PURGE_REVIEW_EXISTS", "批次已经产生教师复核记录，不能彻底删除；请保留历史。")

    ocr_runs = list(db.scalars(select(OcrRun).where(OcrRun.batch_id == batch.id)))
    storage_keys = {source_file.storage_key for source_file in source_files}
    storage_keys.update(page.original_storage_key for page in pages if page.original_storage_key)
    storage_keys.update(page.processed_storage_key for page in pages if page.processed_storage_key)
    storage_keys.update(run.raw_output_storage_key for run in ocr_runs if run.raw_output_storage_key)

    try:
        if page_ids:
            db.execute(delete(OcrBlock).where(OcrBlock.source_page_id.in_(page_ids)))
            db.execute(delete(PageGrouping).where(PageGrouping.source_page_id.in_(page_ids)))
        db.execute(delete(OcrRun).where(OcrRun.batch_id == batch.id))
        db.execute(delete(Anomaly).where(Anomaly.batch_id == batch.id))
        if related_reviews:
            db.execute(delete(ReviewRecord).where(ReviewRecord.id.in_({item.id for item in related_reviews})))
        db.execute(delete(Task).where(Task.batch_id == batch.id))
        db.execute(delete(AssignmentGroup).where(AssignmentGroup.batch_id == batch.id))
        db.execute(delete(QuestionLayout).where(QuestionLayout.batch_id == batch.id))
        db.execute(delete(SourcePage).where(SourcePage.batch_id == batch.id))
        db.execute(delete(SourceFile).where(SourceFile.batch_id == batch.id))
        db.execute(delete(BatchQuestion).where(BatchQuestion.batch_id == batch.id))
        db.delete(batch)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "BATCH_PURGE_CONFLICT", "批次在删除过程中产生了新的业务关联，未执行彻底删除。") from exc

    for storage_key in storage_keys:
        try:
            _path_for_key(storage_key).unlink(missing_ok=True)
        except OSError:
            logger.warning("storage cleanup failed after batch purge: %s", storage_key)
    try:
        if batch_storage_path.is_dir():
            shutil.rmtree(batch_storage_path)
    except OSError:
        logger.warning("batch storage directory cleanup failed after batch purge: %s", batch_storage_path)

    return success_payload(
        request,
        {
            "purged": True,
            "id": batch_id,
            "deleted_files": len(source_files),
            "deleted_pages": len(pages),
            "deleted_groups": len(assignment_group_ids),
            "deleted_groupings": len(grouping_ids),
        },
    )


@router.post("/api/v1/batches/{batch_id}/delete-reviewed")
def delete_reviewed_batch(
    batch_id: str,
    payload: DeleteReviewedBatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """在密码和批次名称二次确认后，物理删除已完成批改链路的批次。"""

    batch = get_owned_batch(db, user.id, batch_id)
    if batch.status not in {"reviewed", "archived"}:
        raise ApiError(409, "BATCH_DELETE_REVIEWED_ONLY", "只有已复核或已归档批次可以从此入口物理删除。")
    if payload.confirm_name != batch.title:
        raise ApiError(422, "BATCH_DELETE_CONFIRMATION_INVALID", "确认批次名称与原批次名称不一致，未执行删除。")
    if not verify_password(payload.password, user.password_hash):
        raise ApiError(401, "BATCH_DELETE_PASSWORD_INVALID", "当前账号密码错误，未执行删除。")

    assignment_group_ids = set(db.scalars(select(AssignmentGroup.id).where(AssignmentGroup.batch_id == batch.id)).all())
    task_scope = [Task.batch_id == batch.id]
    if assignment_group_ids:
        task_scope.append(Task.assignment_group_id.in_(assignment_group_ids))
    active_task = db.scalar(
        select(Task.id).where(or_(*task_scope), Task.status.in_({"queued", "running"})).limit(1)
    )
    active_grading_task = db.scalar(
        select(GradingTask.id)
        .join(GradingRun, GradingTask.grading_run_id == GradingRun.id)
        .where(GradingRun.batch_id == batch.id, GradingTask.status.in_({"queued", "running"}))
        .limit(1)
    )
    active_export = db.scalar(
        select(ReportExport.id).where(ReportExport.batch_id == batch.id, ReportExport.status.in_({"queued", "running"})).limit(1)
    )
    if active_task or active_grading_task or active_export:
        raise ApiError(409, "BATCH_DELETE_RUNNING", "批次仍有任务或报告导出在运行，请等待完成后再删除。")

    source_files = list(db.scalars(select(SourceFile).where(SourceFile.batch_id == batch.id)))
    pages = list(db.scalars(select(SourcePage).where(SourcePage.batch_id == batch.id)))
    page_ids = {page.id for page in pages}
    question_ids = set(db.scalars(select(BatchQuestion.id).where(BatchQuestion.batch_id == batch.id)).all())
    grouping_ids = set(
        db.scalars(select(PageGrouping.id).where(PageGrouping.source_page_id.in_(page_ids))).all()
    ) if page_ids else set()
    ocr_run_ids = set(db.scalars(select(OcrRun.id).where(OcrRun.batch_id == batch.id)).all())
    grading_run_ids = set(db.scalars(select(GradingRun.id).where(GradingRun.batch_id == batch.id)).all())
    grading_task_ids = set(
        db.scalars(select(GradingTask.id).where(GradingTask.grading_run_id.in_(grading_run_ids))).all()
    ) if grading_run_ids else set()
    grading_result_ids = set(
        db.scalars(select(GradingResult.id).where(GradingResult.grading_task_id.in_(grading_task_ids))).all()
    ) if grading_task_ids else set()
    answer_photo_ids = set(db.scalars(select(AnswerPhoto.id).where(AnswerPhoto.batch_id == batch.id)).all())
    report_exports = list(db.scalars(select(ReportExport).where(ReportExport.batch_id == batch.id)))
    layout = db.scalar(select(QuestionLayout).where(QuestionLayout.batch_id == batch.id))

    answer_version_filters = []
    if page_ids:
        answer_version_filters.append(AnswerVersion.source_page_id.in_(page_ids))
    if assignment_group_ids:
        answer_version_filters.append(AnswerVersion.assignment_group_id.in_(assignment_group_ids))
    answer_version_ids = set(
        db.scalars(select(AnswerVersion.id).where(or_(*answer_version_filters))).all()
    ) if answer_version_filters else set()
    anomaly_ids = set(db.scalars(select(Anomaly.id).where(Anomaly.batch_id == batch.id)).all())

    review_entity_ids = {
        batch.id,
        *page_ids,
        *question_ids,
        *assignment_group_ids,
        *grouping_ids,
        *ocr_run_ids,
        *grading_run_ids,
        *grading_task_ids,
        *grading_result_ids,
        *answer_version_ids,
        *answer_photo_ids,
        *anomaly_ids,
        *(item.id for item in source_files),
        *(item.id for item in report_exports),
    }
    if layout is not None:
        review_entity_ids.add(layout.id)

    storage_keys = {source_file.storage_key for source_file in source_files}
    storage_keys.update(page.original_storage_key for page in pages if page.original_storage_key)
    storage_keys.update(page.processed_storage_key for page in pages if page.processed_storage_key)
    storage_keys.update(run.raw_output_storage_key for run in db.scalars(select(OcrRun).where(OcrRun.batch_id == batch.id)) if run.raw_output_storage_key)
    storage_keys.update(export.storage_key for export in report_exports if export.storage_key)
    storage_keys.update(photo.original_storage_key for photo in db.scalars(select(AnswerPhoto).where(AnswerPhoto.batch_id == batch.id)))
    storage_keys.update(photo.processed_storage_key for photo in db.scalars(select(AnswerPhoto).where(AnswerPhoto.batch_id == batch.id)) if photo.processed_storage_key)
    storage_keys.update(photo.raw_output_storage_key for photo in db.scalars(select(AnswerPhoto).where(AnswerPhoto.batch_id == batch.id)) if photo.raw_output_storage_key)
    batch_storage_path = _path_for_key(f"batches/{batch.id}")

    try:
        # Keep the deletion order explicit: SQLite foreign keys are enabled and
        # the schema intentionally does not rely on broad ON DELETE CASCADE.
        if review_entity_ids:
            db.execute(delete(ReviewRecord).where(ReviewRecord.entity_id.in_(review_entity_ids)))
        if grading_result_ids:
            db.execute(delete(GradingPointResult).where(GradingPointResult.grading_result_id.in_(grading_result_ids)))
            db.execute(delete(GradingResult).where(GradingResult.id.in_(grading_result_ids)))
        if assignment_group_ids:
            db.execute(delete(QuestionAnswer).where(QuestionAnswer.assignment_group_id.in_(assignment_group_ids)))
        if answer_version_ids:
            db.execute(delete(AnswerVersion).where(AnswerVersion.id.in_(answer_version_ids)))
        if grading_task_ids:
            db.execute(delete(GradingTask).where(GradingTask.id.in_(grading_task_ids)))
        if grading_run_ids:
            db.execute(delete(GradingRun).where(GradingRun.id.in_(grading_run_ids)))
        if answer_photo_ids:
            db.execute(delete(AnswerPhoto).where(AnswerPhoto.id.in_(answer_photo_ids)))
        db.execute(delete(ReportExport).where(ReportExport.batch_id == batch.id))
        db.execute(delete(Task).where(or_(*task_scope)))
        if page_ids:
            db.execute(delete(OcrBlock).where(OcrBlock.source_page_id.in_(page_ids)))
            db.execute(delete(PageGrouping).where(PageGrouping.source_page_id.in_(page_ids)))
        db.execute(delete(Anomaly).where(Anomaly.batch_id == batch.id))
        db.execute(delete(OcrRun).where(OcrRun.batch_id == batch.id))
        db.execute(delete(QuestionLayout).where(QuestionLayout.batch_id == batch.id))
        db.execute(delete(SourcePage).where(SourcePage.batch_id == batch.id))
        db.execute(delete(SourceFile).where(SourceFile.batch_id == batch.id))
        db.execute(delete(AssignmentGroup).where(AssignmentGroup.batch_id == batch.id))
        db.execute(delete(BatchQuestion).where(BatchQuestion.batch_id == batch.id))
        db.add(
            AuditLog(
                id=new_id("audit"),
                user_id=user.id,
                event_name="batch.physical_deleted",
                entity_type="assignment_batch",
                entity_id=batch.id,
                request_id=getattr(request.state, "request_id", None),
                metadata_json=json.dumps({"title": batch.title, "status": batch.status}, ensure_ascii=False),
                created_at=utc_now(),
            )
        )
        db.delete(batch)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "BATCH_DELETE_CONFLICT", "批次仍有未处理的业务关联，未执行物理删除。") from exc

    for storage_key in storage_keys:
        try:
            _path_for_key(storage_key).unlink(missing_ok=True)
        except OSError:
            logger.warning("storage cleanup failed after reviewed batch deletion: %s", storage_key)
    try:
        if batch_storage_path.is_dir():
            shutil.rmtree(batch_storage_path)
    except OSError:
        logger.warning("batch storage directory cleanup failed after reviewed batch deletion: %s", batch_storage_path)

    return success_payload(
        request,
        {
            "deleted": True,
            "id": batch_id,
            "deleted_files": len(source_files),
            "deleted_pages": len(pages),
            "deleted_groups": len(assignment_group_ids),
            "deleted_exports": len(report_exports),
        },
    )


@router.get("/api/v1/pages/{page_id}/original")
def get_page_original(page_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回当前账号有权访问的原始页面文件，供前台只读预览使用。"""

    row = db.execute(
        select(SourcePage, SourceFile)
        .join(SourceFile, SourcePage.source_file_id == SourceFile.id)
        .join(AssignmentBatch, SourcePage.batch_id == AssignmentBatch.id)
        .where(SourcePage.id == page_id, AssignmentBatch.owner_user_id == user.id)
    ).first()
    if not row:
        raise ApiError(404, "PAGE_NOT_FOUND", "页面不存在或当前账号无权访问。")
    page, source_file = row
    path = _path_for_key(page.original_storage_key)
    if not path.is_file():
        raise ApiError(404, "PAGE_FILE_NOT_FOUND", "原始页面文件不存在，请检查文件存储状态。")
    # OCR 校对页会把 PDF 放进 iframe、把图片放进 img 进行只读预览。
    # FileResponse 默认使用 attachment，会导致浏览器进入 OCR 页面时自动下载原文件。
    # 原始文件仍然只读且受当前账号权限保护；这里仅把展示方式改为 inline。
    return FileResponse(
        path,
        media_type=source_file.mime_type,
        filename=source_file.original_name,
        content_disposition_type="inline",
    )


@router.post("/api/v1/files/{file_id}/retry", status_code=202)
def retry_file(
    file_id: str,
    request: Request,
    file: UploadFile | None = File(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source_file = db.scalar(
        select(SourceFile)
        .join(AssignmentBatch, SourceFile.batch_id == AssignmentBatch.id)
        .where(SourceFile.id == file_id, AssignmentBatch.owner_user_id == user.id)
    )
    if not source_file:
        raise ApiError(404, "FILE_NOT_FOUND", "文件不存在或当前账号无权访问。")
    if source_file.status != "failed":
        raise ApiError(409, "FILE_NOT_RETRYABLE", "只有失败文件可以重试，成功文件不能覆盖原始记录。")
    if not file:
        raise ApiError(422, "RETRY_FILE_REQUIRED", "重试上传必须重新提供 file 文件字段。")
    batch = _ensure_uploadable_batch(db, user, source_file.batch_id)
    existing = _find_idempotent_task(db, batch.id, idempotency_key)
    if existing:
        task, retried_file = existing
        return success_payload(request, _file_payload(retried_file, task))
    batch.status = "uploading"
    batch.updated_at = utc_now()
    db.commit()
    try:
        retried_file, task = _ingest_upload(db, batch, file, idempotency_key, source_file.file_role)
    except ApiError:
        _refresh_batch_status(db, batch)
        db.commit()
        raise
    return success_payload(request, _file_payload(retried_file, task))


@router.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.scalar(
        select(Task)
        .join(AssignmentBatch, Task.batch_id == AssignmentBatch.id)
        .where(Task.id == task_id, AssignmentBatch.owner_user_id == user.id)
    )
    if not task:
        raise ApiError(404, "TASK_NOT_FOUND", "任务不存在或当前账号无权访问。")
    source_file = db.get(SourceFile, task.resource_id) if task.resource_id else None
    result = _task_payload(task, source_file)
    if task.task_type == "ocr" and task.resource_id:
        ocr_run = db.get(OcrRun, task.resource_id)
        if ocr_run:
            result["ocr_run_id"] = ocr_run.id
            result["ocr_status"] = ocr_run.status
            result["engine_name"] = ocr_run.engine_name
            result["engine_version"] = ocr_run.engine_version
            result["preprocess_version"] = ocr_run.preprocess_version
    if task.task_type == "answer_photo_ocr" and task.resource_id:
        answer_photo = db.get(AnswerPhoto, task.resource_id)
        if answer_photo:
            result["answer_photo"] = {
                "id": answer_photo.id,
                "source_page_id": answer_photo.source_page_id,
                "question_id": answer_photo.question_id,
                "original_name": answer_photo.original_name,
                "status": answer_photo.status,
                "answer_text": answer_photo.answer_text,
                "confidence": str(answer_photo.confidence) if answer_photo.confidence is not None else None,
                "recognition_mode": answer_photo.recognition_mode,
                "error_code": answer_photo.error_code,
                "error_message": answer_photo.error_message,
                "updated_at": answer_photo.updated_at,
            }
    return success_payload(request, result)
