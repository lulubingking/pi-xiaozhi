"""页面查询和 OCR 任务编排接口。

本模块只负责用户隔离、页面/运行记录和持久化任务入队；不在 HTTP 请求中伪造
OCR 文本，也不直接执行 GPU 推理。OCR Worker 应复用这里固化的识别模式、预处理
版本和参数快照。
"""

from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.batches import get_owned_batch
from app.api.deps import get_current_user
from app.api.errors import ApiError, success_payload
from app.core.config import get_settings
from app.core.utils import new_id, utc_now
from app.db.session import get_db
from app.models import (
    AssignmentBatch,
    AssignmentGroup,
    AnswerVersion,
    AnswerPhoto,
    BatchQuestion,
    OcrBlock,
    OcrRun,
    PageGrouping,
    QuestionLayout,
    QuestionAnswer,
    ReviewRecord,
    SourceFile,
    SourcePage,
    Student,
    Task,
    User,
)
from app.ocr_reading_order import sort_ocr_blocks_reading_order
from app.schemas import (
    OcrCorrectionRequest,
    FileRole,
    OcrRunRequest,
    PageConfirmRequest,
    PageGroupingRequest,
    RecognitionMode,
)

router = APIRouter(tags=["pages", "ocr"])
settings = get_settings()

ANSWER_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}
UPLOAD_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class OcrProfile:
    mode: RecognitionMode
    engine_name: str
    engine_version: str
    preprocess_version: str
    parameters: dict[str, Any]


# 识别器、预处理链路和参数来自已确认的 OCR HANDOFF。这里保存的是任务快照，
# 不是推理实现；Worker 必须使用同一份 profile，避免任务记录与实际运行漂移。
OCR_PROFILES: dict[RecognitionMode, OcrProfile] = {
    "printed": OcrProfile(
        mode="printed",
        engine_name="paddleocr",
        engine_version="PP-OCRv5_server_det+PP-OCRv5_server_rec",
        preprocess_version="opencv_pil_page_geometry_v1",
        parameters={
            "det_model": "PP-OCRv5_server_det",
            "rec_model": "PP-OCRv5_server_rec",
            "lang": "ch",
            "language_scope": "zh+en",
        },
    ),
    "chinese_handwriting": OcrProfile(
        mode="chinese_handwriting",
        engine_name="paddleocr",
        engine_version="PP-OCRv5_server_det+PP-OCRv5_server_rec",
        preprocess_version="opencv_red_suppression_lab_clahe_row_sort_v1",
        parameters={
            "lang": "ch",
            "device": "gpu",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "det_box_order": "row_cluster_then_x_sort",
        },
    ),
    "english_handwriting": OcrProfile(
        mode="english_handwriting",
        engine_name="trocr",
        engine_version="microsoft/trocr-large-handwritten",
        preprocess_version="opencv_perspective_line_crop_gray_clahe_rgb_v1",
        parameters={
            "batch_size": 1,
            "line_pipeline": "perspective_correction_remove_lines_detect_merge_split_quality_filter",
            "color_pipeline": "gray_clahe_to_rgb",
        },
    ),
    "math_handwriting": OcrProfile(
        mode="math_handwriting",
        engine_name="texteller",
        engine_version="TexTeller:texteller_model",
        preprocess_version="opencv_formula_candidate_crop_v1",
        parameters={
            "device": "cuda",
            "max_tokens": 256,
            "num_beams": 3,
            "no_repeat_ngram_size": 0,
            "candidate_input": "not_full_page",
            "text_engine": "PP-OCRv5",
            "text_language_scope": "zh+en",
            "output": "merged_text_and_formula_blocks",
        },
    ),
}


def _profile_payload(profile: OcrProfile) -> dict[str, Any]:
    return {
        "recognition_mode": profile.mode,
        "engine_name": profile.engine_name,
        "engine_version": profile.engine_version,
        "preprocess_version": profile.preprocess_version,
        "parameters": profile.parameters,
    }


def _parse_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _question_layout_snapshot(db: Session, batch_id: str) -> dict[str, Any] | None:
    layout = db.scalar(select(QuestionLayout).where(QuestionLayout.batch_id == batch_id, QuestionLayout.status == "confirmed"))
    if layout is None:
        return None
    return {
        "id": layout.id,
        "version": layout.version,
        "status": layout.status,
        "source_page_id": layout.source_page_id,
        "source_pages": _parse_json(layout.source_pages_json, []),
        "regions": _parse_json(layout.regions_json, []),
    }


def _answer_photo_storage_root() -> Path:
    root = Path(settings.storage_root)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[3] / root
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _answer_photo_path(storage_key: str) -> Path:
    root = _answer_photo_storage_root()
    candidate = (root / storage_key).resolve()
    if root != candidate and root not in candidate.parents:
        raise ApiError(400, "INVALID_STORAGE_KEY", "答案照片存储路径校验失败，请重新上传。")
    return candidate


def _safe_photo_name(filename: str | None) -> str:
    name = Path(filename or "answer-photo").name.strip()
    return (name if name and name not in {".", ".."} else "answer-photo")[:255]


def _copy_and_validate_answer_photo(upload: UploadFile, temp_path: Path) -> tuple[str, str, int, str]:
    filename = _safe_photo_name(upload.filename)
    extension = Path(filename).suffix.lower()
    if extension not in ANSWER_PHOTO_EXTENSIONS:
        raise ApiError(415, "UNSUPPORTED_ANSWER_PHOTO", "主观题答案照片仅支持 JPG、JPEG 或 PNG。")
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
                    raise ApiError(413, "FILE_TOO_LARGE", f"答案照片超过 {settings.max_file_size} 字节限制，请压缩后重试。")
                digest.update(chunk)
                if len(head) < 16:
                    head.extend(chunk[: 16 - len(head)])
                target.write(chunk)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    if size == 0:
        temp_path.unlink(missing_ok=True)
        raise ApiError(422, "FILE_EMPTY", "答案照片为空，请选择有效的图片。")
    if extension == ".png" and not bytes(head).startswith(b"\x89PNG\r\n\x1a\n"):
        temp_path.unlink(missing_ok=True)
        raise ApiError(415, "UNSUPPORTED_ANSWER_PHOTO", "PNG 文件头校验失败，请重新选择照片。")
    if extension in {".jpg", ".jpeg"} and not bytes(head).startswith(b"\xff\xd8\xff"):
        temp_path.unlink(missing_ok=True)
        raise ApiError(415, "UNSUPPORTED_ANSWER_PHOTO", "JPEG 文件头校验失败，请重新选择照片。")
    try:
        with Image.open(temp_path) as image:
            image.verify()
        with Image.open(temp_path) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        temp_path.unlink(missing_ok=True)
        raise ApiError(422, "FILE_CORRUPTED", "答案照片无法解码，请重新拍摄或导出后上传。") from exc
    return filename, ("image/png" if extension == ".png" else "image/jpeg"), size, digest.hexdigest()


def _answer_photo_payload(photo: AnswerPhoto, task: Task | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": photo.id,
        "batch_id": photo.batch_id,
        "assignment_group_id": photo.assignment_group_id,
        "source_page_id": photo.source_page_id,
        "question_id": photo.question_id,
        "task_id": photo.task_id,
        "original_name": photo.original_name,
        "mime_type": photo.mime_type,
        "extension": photo.extension,
        "size_bytes": photo.size_bytes,
        "recognition_mode": photo.recognition_mode,
        "status": photo.status,
        "answer_text": photo.answer_text,
        "confidence": str(photo.confidence) if photo.confidence is not None else None,
        "error_code": photo.error_code,
        "error_message": photo.error_message,
        "created_at": photo.created_at,
        "updated_at": photo.updated_at,
    }
    if task is not None:
        result["task"] = {
            "task_id": task.id,
            "status": task.status,
            "stage": task.stage,
            "progress_current": task.progress_current,
            "progress_total": task.progress_total,
            "error_code": task.error_code,
            "error_message": task.error_message,
        }
    return result
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _grouping_payload(grouping: PageGrouping | None, student: Student | None, group: AssignmentGroup | None) -> dict[str, Any] | None:
    if not grouping:
        return None
    return {
        "id": grouping.id,
        "assignment_group_id": grouping.assignment_group_id,
        "student_id": grouping.student_id,
        "student_code": student.student_code if student else None,
        "student_name": student.display_name if student else None,
        "page_sequence": grouping.page_sequence,
        "grouping_status": grouping.grouping_status,
        "grouping_confidence": str(grouping.grouping_confidence) if grouping.grouping_confidence is not None else None,
        "grouping_reason": grouping.grouping_reason,
        "confirmed_by": grouping.confirmed_by,
        "confirmed_at": grouping.confirmed_at,
        "version": grouping.version,
        "assignment_group_status": group.status if group else None,
    }


def _ocr_run_payload(run: OcrRun, *, blocks: list[OcrBlock] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": run.id,
        "batch_id": run.batch_id,
        "source_page_id": run.source_page_id,
        "engine_name": run.engine_name,
        "engine_version": run.engine_version,
        "preprocess_version": run.preprocess_version,
        "status": run.status,
        "raw_output_storage_key": run.raw_output_storage_key,
        "error_code": run.error_code,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
    if blocks is not None:
        result["blocks"] = [
            {
                "id": block.id,
                "block_index": block.block_index,
                "text_raw": block.text_raw,
                "confidence": str(block.confidence) if block.confidence is not None else None,
                "x1": str(block.x1) if block.x1 is not None else None,
                "y1": str(block.y1) if block.y1 is not None else None,
                "x2": str(block.x2) if block.x2 is not None else None,
                "y2": str(block.y2) if block.y2 is not None else None,
                "question_id": block.question_id,
                "created_at": block.created_at,
            }
            for block in blocks
        ]
    return result


def _page_payload(
    db: Session,
    page: SourcePage,
    source_file: SourceFile,
    *,
    detail: bool = False,
) -> dict[str, Any]:
    grouping = db.scalar(select(PageGrouping).where(PageGrouping.source_page_id == page.id))
    student = db.get(Student, grouping.student_id) if grouping and grouping.student_id else None
    group = db.get(AssignmentGroup, grouping.assignment_group_id) if grouping and grouping.assignment_group_id else None
    runs = list(
        db.scalars(
            select(OcrRun)
            .where(OcrRun.source_page_id == page.id)
            .order_by(OcrRun.started_at.desc(), OcrRun.id.desc())
        )
    )
    latest = runs[0] if runs else None
    latest_blocks: list[OcrBlock] = []
    if latest:
        latest_blocks = list(
            db.scalars(select(OcrBlock).where(OcrBlock.ocr_run_id == latest.id).order_by(OcrBlock.block_index))
        )
    result: dict[str, Any] = {
        "id": page.id,
        "source_file_id": source_file.id,
        "file_role": source_file.file_role,
        "original_name": source_file.original_name,
        "page_index": page.page_index,
        "page_count": source_file.page_count,
        "original_storage_key": page.original_storage_key,
        "processed_storage_key": page.processed_storage_key,
        "transform": _parse_json(page.transform_json, None),
        "status": page.status,
        "created_at": page.created_at,
        "updated_at": page.updated_at,
        "grouping": _grouping_payload(grouping, student, group),
        # 详情和列表都返回同一份最新 OCR 文本块，避免前端必须猜测 blocks 位于哪个层级。
        "latest_ocr_run": _ocr_run_payload(latest, blocks=latest_blocks) if latest else None,
    }
    if detail:
        result["ocr_runs"] = [_ocr_run_payload(run, blocks=latest_blocks if run.id == latest.id else None) for run in runs]
        saved_answers: dict[str, dict[str, Any]] = {}
        if grouping and grouping.assignment_group_id:
            answer_rows = list(
                db.scalars(
                    select(QuestionAnswer).where(QuestionAnswer.assignment_group_id == grouping.assignment_group_id)
                )
            )
            for answer_row in answer_rows:
                version = db.get(AnswerVersion, answer_row.current_version_id)
                if version is None:
                    continue
                saved_answers[answer_row.question_id] = {
                    "answer_text": version.answer_text or "",
                    "is_blank_confirmed": bool(version.is_blank_confirmed),
                    "source_type": version.source_type,
                    "answer_photo_id": version.answer_photo_id,
                    "coverage_status": answer_row.coverage_status,
                    "version_no": version.version_no,
                }
        result["saved_answers"] = saved_answers
    return result


def _task_payload(task: Task, profile: OcrProfile, page_count: int) -> dict[str, Any]:
    options = _parse_json(task.options_json, {})
    recorded_mode = options.get("recognition_mode") if isinstance(options, dict) else None
    recorded_file_role = options.get("file_role", "student_work") if isinstance(options, dict) else "student_work"
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "batch_id": task.batch_id,
        "resource_id": task.resource_id,
        "status": task.status,
        "stage": task.stage,
        "progress_current": task.progress_current,
        "progress_total": task.progress_total,
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "page_count": page_count,
        "profile": _profile_payload(profile),
        "recorded_recognition_mode": recorded_mode,
        "file_role": recorded_file_role,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


@router.get("/api/v1/batches/{batch_id}/ocr-task")
def get_latest_ocr_task(
    batch_id: str,
    request: Request,
    file_role: FileRole = Query(default="student_work"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """读取当前批次指定文件类型最近一次 OCR 任务，供页面恢复进度。"""

    batch = get_owned_batch(db, user.id, batch_id)
    task = None
    for candidate in db.scalars(
        select(Task)
        .where(Task.batch_id == batch.id, Task.task_type == "ocr")
        .order_by(Task.created_at.desc())
        .limit(20)
    ):
        options = _parse_json(candidate.options_json, {})
        candidate_file_role = options.get("file_role", "student_work") if isinstance(options, dict) else "student_work"
        if candidate_file_role == file_role:
            task = candidate
            break
    if task is None:
        return success_payload(request, None)
    options = _parse_json(task.options_json, {})
    mode = options.get("recognition_mode", "printed") if isinstance(options, dict) else "printed"
    profile = OCR_PROFILES.get(mode, OCR_PROFILES["printed"])
    return success_payload(request, _task_payload(task, profile, task.progress_total))


def _owned_page(db: Session, user_id: str, page_id: str) -> tuple[SourcePage, SourceFile, AssignmentBatch]:
    row = db.execute(
        select(SourcePage, SourceFile, AssignmentBatch)
        .join(SourceFile, SourcePage.source_file_id == SourceFile.id)
        .join(AssignmentBatch, SourcePage.batch_id == AssignmentBatch.id)
        .where(SourcePage.id == page_id, AssignmentBatch.owner_user_id == user_id)
    ).first()
    if not row:
        raise ApiError(404, "PAGE_NOT_FOUND", "页面不存在或当前账号无权访问。")
    return row[0], row[1], row[2]


def _active_ocr_task(db: Session, batch_id: str) -> Task | None:
    return db.scalar(
        select(Task)
        .where(
            Task.batch_id == batch_id,
            Task.task_type == "ocr",
            Task.status.in_(["queued", "running"]),
        )
        .order_by(Task.created_at.desc())
    )


def _correction_blocked(message: str, details: list[dict[str, Any]] | None = None) -> ApiError:
    return ApiError(422, "OCR_CORRECTION_BLOCKED", message, details)


def _get_page_grouping(db: Session, page_id: str) -> PageGrouping | None:
    return db.scalar(select(PageGrouping).where(PageGrouping.source_page_id == page_id))


def _get_batch_questions(db: Session, batch_id: str, question_ids: set[str]) -> dict[str, BatchQuestion]:
    questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch_id)))
    by_id = {question.id: question for question in questions}
    missing = sorted(question_ids - by_id.keys())
    if missing:
        raise _correction_blocked("存在不属于当前批次的题目，不能保存校对结果。", [{"question_id": item} for item in missing])
    return by_id


def _validate_answer_inputs(payload: OcrCorrectionRequest) -> None:
    seen: set[str] = set()
    errors: list[dict[str, Any]] = []
    for answer in payload.answers:
        if answer.question_id in seen:
            errors.append({"question_id": answer.question_id, "code": "DUPLICATE_QUESTION"})
        seen.add(answer.question_id)
        text = answer.answer_text.strip() if answer.answer_text else ""
        if answer.is_blank_confirmed and text:
            errors.append({"question_id": answer.question_id, "code": "BLANK_TEXT_CONFLICT"})
        if answer.present_on_page and not answer.is_blank_confirmed and not text:
            errors.append({"question_id": answer.question_id, "code": "ANSWER_TEXT_REQUIRED"})
    if errors:
        raise _correction_blocked("已定位的题目必须填写答案，或明确勾选“确认本题为空题”；请修正后重试。", errors)


def _validate_page_sequences(db: Session, assignment_group_id: str) -> None:
    groupings = list(
        db.scalars(
            select(PageGrouping)
            .where(PageGrouping.assignment_group_id == assignment_group_id)
            .order_by(PageGrouping.page_sequence)
        )
    )
    sequences = [item.page_sequence for item in groupings if item.page_sequence is not None]
    if len(sequences) != len(set(sequences)):
        raise _correction_blocked("同一学生作业内页序重复，不能确认校对结果。", [{"code": "DUPLICATE_PAGE_SEQUENCE"}])
    expected = list(range(1, len(sequences) + 1))
    if sorted(sequences) != expected:
        raise _correction_blocked("同一学生作业内页序必须从 1 开始连续排列。", [{"code": "NON_CONTIGUOUS_PAGE_SEQUENCE"}])


def _latest_successful_ocr_text(db: Session, page_id: str, question_id: str, *, allow_all_blocks: bool = False) -> str:
    """读取单页当前成功 OCR 的题目文本，供同一学生多页答案拼接。"""

    runs = list(
        db.scalars(
            select(OcrRun)
            .where(OcrRun.source_page_id == page_id, OcrRun.status == "succeeded")
            .order_by(OcrRun.started_at.desc(), OcrRun.id.desc())
        )
    )
    for run in runs:
        blocks = list(
            db.scalars(
                select(OcrBlock)
                .where(OcrBlock.ocr_run_id == run.id, OcrBlock.question_id == question_id)
            )
        )
        if not blocks and allow_all_blocks:
            blocks = list(
                db.scalars(
                    select(OcrBlock)
                    .where(OcrBlock.ocr_run_id == run.id)
                )
            )
        blocks = sort_ocr_blocks_reading_order(blocks)
        text = "\n".join(block.text_raw.strip() for block in blocks if block.text_raw and block.text_raw.strip()).strip()
        if text:
            return text
    return ""


def _joined_student_ocr_text(db: Session, batch_id: str, assignment_group_id: str, question_id: str, current_page_id: str, current_text: str) -> str:
    """按同一学生的合并页序拼接题目 OCR 文本，避免第二页覆盖第一页。"""

    groupings = list(
        db.scalars(
            select(PageGrouping)
            .where(PageGrouping.assignment_group_id == assignment_group_id)
            .order_by(PageGrouping.page_sequence.asc(), PageGrouping.source_page_id.asc())
        )
    )
    question_count = db.scalar(select(func.count(BatchQuestion.id)).where(BatchQuestion.batch_id == batch_id)) or 0
    parts: list[str] = []
    for grouping in groupings:
        page_text = current_text.strip() if grouping.source_page_id == current_page_id else _latest_successful_ocr_text(
            db,
            grouping.source_page_id,
            question_id,
            allow_all_blocks=question_count == 1,
        )
        if page_text:
            parts.append(page_text)
    return "\n".join(parts).strip()


def _grouping_for_update(
    db: Session,
    batch: AssignmentBatch,
    payload: PageGroupingRequest,
) -> tuple[AssignmentGroup | None, Student | None]:
    student = None
    if payload.student_id:
        student = db.scalar(select(Student).where(Student.id == payload.student_id, Student.class_id == batch.class_id))
        if not student:
            raise ApiError(422, "STUDENT_NOT_IN_BATCH_CLASS", "所选学生不属于当前批次的班级。")
    group = None
    if payload.assignment_group_id:
        group = db.scalar(
            select(AssignmentGroup).where(
                AssignmentGroup.id == payload.assignment_group_id,
                AssignmentGroup.batch_id == batch.id,
            )
        )
        if not group:
            raise ApiError(422, "ASSIGNMENT_GROUP_NOT_FOUND", "学生作业对象不存在或不属于当前批次。")
        if student and group.student_id and group.student_id != student.id:
            raise ApiError(409, "GROUP_STUDENT_CONFLICT", "学生与作业对象归属不一致，请刷新后重试。")
        if not student and group.student_id:
            student = db.get(Student, group.student_id)
    elif student:
        group = db.scalar(
            select(AssignmentGroup)
            .where(AssignmentGroup.batch_id == batch.id, AssignmentGroup.student_id == student.id)
            .order_by(AssignmentGroup.created_at)
        )
    if student and not group:
        now = utc_now()
        group = AssignmentGroup(
            id=new_id("group"),
            batch_id=batch.id,
            student_id=student.id,
            status="needs_assignment",
            grouping_confidence=None,
            grouping_reason=payload.reason,
            confirmed_by=None,
            confirmed_at=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        db.add(group)
        db.flush()
    return group, student


def _write_review_record(
    db: Session,
    user_id: str,
    entity_type: str,
    entity_id: str,
    action: str,
    before: Any,
    after: Any,
    reason: str | None,
) -> None:
    db.add(
        ReviewRecord(
            id=new_id("review"),
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_json=json.dumps(before, ensure_ascii=False) if before is not None else None,
            after_json=json.dumps(after, ensure_ascii=False) if after is not None else None,
            reason=reason,
            created_at=utc_now(),
        )
    )


def _queue_ocr_task(
    db: Session,
    batch: AssignmentBatch,
    pages: list[SourcePage],
    profile: OcrProfile,
    idempotency_key: str | None,
    *,
    file_role: FileRole = "student_work",
    resource_id: str | None = None,
    question_layout: dict[str, Any] | None = None,
) -> tuple[Task, list[OcrRun]]:
    now = utc_now()
    request_hash = hashlib.sha256(profile.mode.encode("utf-8")).hexdigest()
    run_records: list[OcrRun] = []
    options = {"recognition_mode": profile.mode, "file_role": file_role}
    if question_layout is not None:
        options["question_layout"] = question_layout
    task = Task(
        id=new_id("task"),
        task_type="ocr",
        batch_id=batch.id,
        assignment_group_id=None,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        options_json=json.dumps(options, ensure_ascii=False),
        status="queued",
        stage="queued",
        progress_current=0,
        progress_total=len(pages),
        retry_count=0,
        max_retries=3,
        error_code=None,
        error_message=None,
        error_items_json="[]",
        created_at=now,
        started_at=None,
        finished_at=None,
        worker_id=None,
        heartbeat_at=None,
    )
    db.add(task)
    for page in pages:
        page.status = "ocr_processing"
        page.updated_at = now
        run = OcrRun(
            id=new_id("ocr_run"),
            batch_id=batch.id,
            source_page_id=page.id,
            engine_name=profile.engine_name,
            engine_version=profile.engine_version,
            preprocess_version=profile.preprocess_version,
            status="queued",
            raw_output_storage_key=None,
            error_code=None,
            started_at=None,
            finished_at=None,
        )
        run_records.append(run)
        db.add(run)
    batch.status = "ocr_processing"
    batch.updated_at = now
    batch.version += 1
    db.flush()
    options["ocr_run_ids"] = [run.id for run in run_records]
    task.options_json = json.dumps(options, ensure_ascii=False)
    return task, run_records


@router.get("/api/v1/batches/{batch_id}/pages")
def list_pages(
    batch_id: str,
    request: Request,
    status: str | None = Query(default=None),
    file_role: FileRole = Query(default="student_work"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    query = (
        select(SourcePage, SourceFile)
        .join(SourceFile, SourcePage.source_file_id == SourceFile.id)
        .where(SourcePage.batch_id == batch.id, SourceFile.file_role == file_role)
        .order_by(SourceFile.created_at, SourcePage.page_index)
    )
    if status:
        query = query.where(SourcePage.status == status)
    rows = db.execute(query).all()
    data = [_page_payload(db, page, source_file) for page, source_file in rows]
    return success_payload(request, data, meta={"page": 1, "page_size": len(data), "total": len(data)})


@router.get("/api/v1/pages/{page_id}")
def get_page(page_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page, source_file, batch = _owned_page(db, user.id, page_id)
    result = _page_payload(db, page, source_file, detail=True)
    result["batch_id"] = batch.id
    result["batch_title"] = batch.title
    result["subject"] = batch.subject
    return success_payload(request, result)


@router.post("/api/v1/batches/{batch_id}/ocr-runs", status_code=202)
def create_ocr_run(
    batch_id: str,
    payload: OcrRunRequest,
    request: Request,
    file_role: FileRole = Query(default="student_work"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    if batch.status in {"archived", "grading", "reviewed"}:
        raise ApiError(409, "BATCH_OCR_LOCKED", "当前批次已进入不可重新识别的阶段，请新建批次或处理待复核内容。")
    profile = OCR_PROFILES[payload.recognition_mode]
    question_layout = _question_layout_snapshot(db, batch.id) if file_role == "student_work" else None
    if file_role == "student_work" and question_layout is None:
        raise ApiError(409, "QUESTION_LAYOUT_REQUIRED", "请先完成题目页 OCR，并确认题目数量和位置模板，再开始学生作业识别。")
    if idempotency_key:
        existing = db.scalar(
            select(Task).where(Task.batch_id == batch.id, Task.task_type == "ocr", Task.idempotency_key == idempotency_key)
        )
        if existing:
            existing_options = _parse_json(existing.options_json, {})
            recorded_mode = existing_options.get("recognition_mode") if isinstance(existing_options, dict) else None
            recorded_file_role = existing_options.get("file_role", "student_work") if isinstance(existing_options, dict) else "student_work"
            if recorded_mode and recorded_mode != profile.mode:
                raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "相同幂等键已用于其他识别模式，请更换幂等键后重试。")
            if recorded_file_role != file_role:
                raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "相同幂等键已用于其他文件类型，请更换幂等键后重试。")
            return success_payload(
                request,
                {"task": _task_payload(existing, profile, existing.progress_total), "reused": True},
            )
    active = _active_ocr_task(db, batch.id)
    if active:
        raise ApiError(409, "OCR_TASK_ALREADY_RUNNING", "当前批次已有 OCR 任务排队或运行中，请先查询该任务状态。", [{"task_id": active.id}])
    pages = list(
        db.scalars(
            select(SourcePage)
            .join(SourceFile, SourcePage.source_file_id == SourceFile.id)
            .where(SourcePage.batch_id == batch.id, SourceFile.status == "success", SourceFile.file_role == file_role)
            .order_by(SourcePage.id)
        )
    )
    if not pages:
        raise ApiError(409, "OCR_NO_PAGES", "当前批次没有可识别的成功文件页面，请先完成上传。")
    task, _runs = _queue_ocr_task(
        db,
        batch,
        pages,
        profile,
        idempotency_key,
        file_role=file_role,
        question_layout=question_layout,
    )
    db.commit()
    return success_payload(
        request,
        {
            "task": _task_payload(task, profile, len(pages)),
            "reused": False,
            "execution": "queued_for_worker",
            "message": "OCR 任务已入队；Worker 将按该 profile 执行预处理、区域提取和识别。",
        },
    )


@router.post("/api/v1/pages/{page_id}/ocr-retry", status_code=202)
def retry_page_ocr(
    page_id: str,
    payload: OcrRunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    page, _source_file, batch = _owned_page(db, user.id, page_id)
    profile = OCR_PROFILES[payload.recognition_mode]
    question_layout = _question_layout_snapshot(db, batch.id) if _source_file.file_role == "student_work" else None
    if _source_file.file_role == "student_work" and question_layout is None:
        raise ApiError(409, "QUESTION_LAYOUT_REQUIRED", "请先完成题目页 OCR，并确认题目数量和位置模板，再重试学生作业识别。")
    if idempotency_key:
        existing = db.scalar(
            select(Task).where(Task.batch_id == batch.id, Task.task_type == "ocr", Task.idempotency_key == idempotency_key)
        )
        if existing and existing.resource_id:
            existing_options = _parse_json(existing.options_json, {})
            recorded_mode = existing_options.get("recognition_mode") if isinstance(existing_options, dict) else None
            recorded_file_role = existing_options.get("file_role", "student_work") if isinstance(existing_options, dict) else "student_work"
            if recorded_mode and recorded_mode != profile.mode:
                raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "相同幂等键已用于其他识别模式，请更换幂等键后重试。")
            if recorded_file_role != _source_file.file_role:
                raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "相同幂等键已用于其他文件类型，请更换幂等键后重试。")
            return success_payload(
                request,
                {"task": _task_payload(existing, profile, 1), "ocr_run_id": existing.resource_id, "reused": True},
            )
    if _active_ocr_task(db, batch.id):
        raise ApiError(409, "OCR_TASK_ALREADY_RUNNING", "当前批次已有 OCR 任务排队或运行中，请先查询该任务状态。")
    task, runs = _queue_ocr_task(
        db,
        batch,
        [page],
        profile,
        idempotency_key,
        file_role=_source_file.file_role,
        question_layout=question_layout,
    )
    ocr_run = runs[0]
    task.resource_id = ocr_run.id
    retry_options = {"recognition_mode": profile.mode, "file_role": _source_file.file_role, "ocr_run_ids": [ocr_run.id]}
    if question_layout is not None:
        retry_options["question_layout"] = question_layout
    task.options_json = json.dumps(retry_options, ensure_ascii=False)
    db.commit()
    return success_payload(
        request,
        {
            "task": _task_payload(task, profile, 1),
            "ocr_run_id": ocr_run.id,
            "reused": False,
            "execution": "queued_for_worker",
        },
    )


@router.post("/api/v1/pages/{page_id}/answer-photo-ocr", status_code=202)
def create_answer_photo_ocr(
    page_id: str,
    request: Request,
    file: UploadFile = File(...),
    question_id: str = Form(...),
    recognition_mode: RecognitionMode = Form(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传单道主观题答案照片，并排队进入同一套 OCR LangGraph 流程。"""

    page, _source_file, batch = _owned_page(db, user.id, page_id)
    if batch.status in {"archived", "grading", "reviewed"}:
        raise ApiError(409, "ANSWER_PHOTO_OCR_LOCKED", "当前批次已进入不可修改阶段，不能上传答案照片。")
    grouping = _get_page_grouping(db, page.id)
    if not grouping or not grouping.assignment_group_id or not grouping.student_id:
        raise _correction_blocked("请先完成学生归属，再上传主观题答案照片。", [{"code": "GROUPING_REQUIRED"}])
    question = db.scalar(select(BatchQuestion).where(BatchQuestion.id == question_id, BatchQuestion.batch_id == batch.id))
    if not question:
        raise ApiError(404, "QUESTION_NOT_FOUND", "题目不存在或不属于当前批次。")
    if question.question_type != "subjective":
        raise ApiError(422, "ANSWER_PHOTO_SUBJECTIVE_ONLY", "答案照片 OCR 仅适用于主观题。")

    if idempotency_key:
        existing_task = db.scalar(
            select(Task).where(
                Task.batch_id == batch.id,
                Task.task_type == "answer_photo_ocr",
                Task.idempotency_key == idempotency_key,
            )
        )
        if existing_task and existing_task.resource_id:
            existing_options = _parse_json(existing_task.options_json, {})
            if isinstance(existing_options, dict) and (
                existing_options.get("recognition_mode") != recognition_mode
                or existing_options.get("question_id") != question.id
            ):
                raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "相同幂等键已用于其他题目或识别模式，请更换幂等键后重试。")
            existing_photo = db.get(AnswerPhoto, existing_task.resource_id)
            if existing_photo:
                return success_payload(
                    request,
                    {"task": _task_payload(existing_task, OCR_PROFILES[recognition_mode], 1), "photo": _answer_photo_payload(existing_photo, existing_task), "reused": True},
                )

    photo_id = new_id("answer_photo")
    task_id = new_id("task")
    now = utc_now()
    temp_path = _answer_photo_storage_root() / ".tmp" / f"{photo_id}.upload"
    final_path: Path | None = None
    try:
        filename, mime_type, size, sha256 = _copy_and_validate_answer_photo(file, temp_path)
        extension = Path(filename).suffix.lower()
        storage_key = f"batches/{batch.id}/answer-photos/{photo_id}/original{extension}"
        final_path = _answer_photo_path(storage_key)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final_path)

        task = Task(
            id=task_id,
            task_type="answer_photo_ocr",
            batch_id=batch.id,
            assignment_group_id=grouping.assignment_group_id,
            resource_id=photo_id,
            idempotency_key=idempotency_key,
            request_hash=hashlib.sha256(f"{question_id}:{recognition_mode}:{sha256}".encode("utf-8")).hexdigest(),
            options_json=json.dumps(
                {
                    "recognition_mode": recognition_mode,
                    "answer_photo_id": photo_id,
                    "source_page_id": page.id,
                    "question_id": question.id,
                },
                ensure_ascii=False,
            ),
            status="queued",
            stage="queued",
            progress_current=0,
            progress_total=1,
            retry_count=0,
            max_retries=2,
            error_code=None,
            error_message=None,
            error_items_json="[]",
            created_at=now,
            started_at=None,
            finished_at=None,
            worker_id=None,
            heartbeat_at=None,
        )
        photo = AnswerPhoto(
            id=photo_id,
            batch_id=batch.id,
            assignment_group_id=grouping.assignment_group_id,
            source_page_id=page.id,
            question_id=question.id,
            task_id=task.id,
            original_name=filename,
            original_storage_key=storage_key,
            processed_storage_key=None,
            raw_output_storage_key=None,
            mime_type=mime_type,
            extension=extension,
            size_bytes=size,
            sha256=sha256,
            recognition_mode=recognition_mode,
            status="queued",
            answer_text=None,
            confidence=None,
            workflow_json=None,
            error_code=None,
            error_message=None,
            created_by=user.id,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        # AnswerPhoto.task_id 是指向 tasks 的外键。显式先刷入任务，
        # 避免 SQLAlchemy 在没有 ORM relationship 的情况下先插入答案照片而触发外键 500。
        db.flush()
        db.add(photo)
        db.commit()
    except ApiError:
        temp_path.unlink(missing_ok=True)
        if final_path and final_path.exists():
            final_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        if final_path and final_path.exists():
            final_path.unlink(missing_ok=True)
        raise ApiError(500, "ANSWER_PHOTO_SAVE_FAILED", f"答案照片保存失败：{exc}") from exc
    except IntegrityError as exc:
        db.rollback()
        temp_path.unlink(missing_ok=True)
        if final_path and final_path.exists():
            final_path.unlink(missing_ok=True)
        raise ApiError(409, "ANSWER_PHOTO_SAVE_CONFLICT", "答案照片保存时发生数据关联冲突，请刷新页面后重试。") from exc

    profile = OCR_PROFILES[recognition_mode]
    return success_payload(
        request,
        {
            "task": _task_payload(task, profile, 1),
            "photo": _answer_photo_payload(photo, task),
            "reused": False,
            "execution": "queued_for_worker",
            "message": "答案照片已保存并进入 OCR 队列；识别完成后会回填到当前主观题文本框。",
        },
    )


@router.patch("/api/v1/pages/{page_id}/grouping")
def update_page_grouping(
    page_id: str,
    payload: PageGroupingRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    page, source_file, batch = _owned_page(db, user.id, page_id)
    current = _get_page_grouping(db, page.id)
    current_version = current.version if current else 1
    if payload.version != current_version:
        raise ApiError(
            409,
            "RESOURCE_VERSION_CONFLICT",
            "页面归属已被其他请求修改，请刷新后再提交。",
            [{"expected": payload.version, "actual": current_version}],
        )
    group, student = _grouping_for_update(db, batch, payload)
    before = None
    if current:
        before = {
            "student_id": current.student_id,
            "assignment_group_id": current.assignment_group_id,
            "page_sequence": current.page_sequence,
            "grouping_status": current.grouping_status,
            "version": current.version,
        }
        current.student_id = student.id if student else None
        current.assignment_group_id = group.id if group else None
        current.page_sequence = payload.page_sequence
        current.grouping_status = "suggested" if group and student and payload.page_sequence else "needs_review"
        current.grouping_reason = payload.reason
        current.grouping_confidence = None
        current.confirmed_by = None
        current.confirmed_at = None
        current.version += 1
        grouping = current
    else:
        grouping = PageGrouping(
            id=new_id("page_grouping"),
            source_page_id=page.id,
            assignment_group_id=group.id if group else None,
            student_id=student.id if student else None,
            page_sequence=payload.page_sequence,
            grouping_status="suggested" if group and student and payload.page_sequence else "needs_review",
            grouping_confidence=None,
            grouping_reason=payload.reason,
            confirmed_by=None,
            confirmed_at=None,
            version=1,
        )
        db.add(grouping)
    if group:
        group.grouping_reason = payload.reason
        group.status = "needs_assignment"
        group.updated_at = utc_now()
        group.version += 1
    after = {
        "student_id": grouping.student_id,
        "assignment_group_id": grouping.assignment_group_id,
        "page_sequence": grouping.page_sequence,
        "grouping_status": grouping.grouping_status,
        "version": grouping.version,
    }
    _write_review_record(db, user.id, "grouping", grouping.id, "page_grouping_updated", before, after, payload.reason)
    db.commit()
    return success_payload(request, _page_payload(db, page, source_file, detail=True))


@router.put("/api/v1/pages/{page_id}/correction")
def save_page_correction(
    page_id: str,
    payload: OcrCorrectionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    page, source_file, batch = _owned_page(db, user.id, page_id)
    grouping = _get_page_grouping(db, page.id)
    if not grouping or not grouping.assignment_group_id or not grouping.student_id:
        raise _correction_blocked("请先完成学生归属，再保存 OCR 校对结果。", [{"code": "GROUPING_REQUIRED"}])
    if grouping.version != payload.version:
        raise ApiError(
            409,
            "RESOURCE_VERSION_CONFLICT",
            "页面校对已被其他请求修改，请刷新后再提交。",
            [{"expected": payload.version, "actual": grouping.version}],
        )
    _validate_answer_inputs(payload)
    question_ids = {answer.question_id for answer in payload.answers if answer.present_on_page}
    questions = _get_batch_questions(db, batch.id, question_ids)
    if payload.save_mode == "confirm":
        if grouping.page_sequence is None:
            raise _correction_blocked("确认校对前必须填写学生作业内页序。", [{"code": "PAGE_SEQUENCE_REQUIRED"}])
        _validate_page_sequences(db, grouping.assignment_group_id)

    now = utc_now()
    saved_count = 0
    for item in payload.answers:
        if not item.present_on_page:
            continue
        question = questions[item.question_id]
        answer_photo = None
        if item.answer_photo_id:
            answer_photo = db.get(AnswerPhoto, item.answer_photo_id)
            if (
                answer_photo is None
                or answer_photo.batch_id != batch.id
                or answer_photo.assignment_group_id != grouping.assignment_group_id
                or answer_photo.source_page_id != page.id
                or answer_photo.question_id != question.id
                or answer_photo.status != "succeeded"
            ):
                raise _correction_blocked(
                    "答案照片不存在、尚未识别完成或不属于当前学生/题目，请重新识别后再保存。",
                    [{"question_id": question.id, "code": "ANSWER_PHOTO_INVALID"}],
                )
        text = item.answer_text.strip() if item.answer_text else None
        current_answer = db.scalar(
            select(QuestionAnswer).where(
                QuestionAnswer.assignment_group_id == grouping.assignment_group_id,
                QuestionAnswer.question_id == question.id,
            )
        )
        previous = None
        if current_answer:
            previous = {
                "current_version_id": current_answer.current_version_id,
                "answer_text_joined": current_answer.answer_text_joined,
                "coverage_status": current_answer.coverage_status,
            }
        current_version = db.get(AnswerVersion, current_answer.current_version_id) if current_answer else None
        if item.source_type == "ocr" and not item.is_blank_confirmed and text:
            if current_version and current_version.source_type in {"manual", "teacher_corrected"}:
                previous_text = (current_version.answer_text or "").strip()
                if previous_text and text not in previous_text:
                    text = f"{previous_text}\n{text}"
                elif previous_text:
                    text = previous_text
            else:
                joined_text = _joined_student_ocr_text(
                    db,
                    batch.id,
                    grouping.assignment_group_id,
                    question.id,
                    page.id,
                    text,
                )
                if joined_text:
                    text = joined_text
        latest_version = db.scalar(
            select(func.max(AnswerVersion.version_no)).where(
                AnswerVersion.assignment_group_id == grouping.assignment_group_id,
                AnswerVersion.question_id == question.id,
            )
        ) or 0
        answer_version = AnswerVersion(
            id=new_id("answer_version"),
            assignment_group_id=grouping.assignment_group_id,
            source_page_id=page.id,
            answer_photo_id=answer_photo.id if answer_photo else None,
            question_id=question.id,
            version_no=int(latest_version) + 1,
            answer_text=None if item.is_blank_confirmed else text,
            is_blank_confirmed=int(item.is_blank_confirmed),
            source_type=item.source_type,
            confidence=None,
            created_by=user.id,
            created_at=now,
        )
        db.add(answer_version)
        db.flush()
        coverage_status = "reviewed" if payload.save_mode == "confirm" else "teacher_saved"
        if current_answer:
            current_answer.current_version_id = answer_version.id
            current_answer.answer_text_joined = answer_version.answer_text
            current_answer.coverage_status = coverage_status
            current_answer.updated_at = now
            answer_record_id = current_answer.id
        else:
            current_answer = QuestionAnswer(
                id=new_id("question_answer"),
                assignment_group_id=grouping.assignment_group_id,
                question_id=question.id,
                current_version_id=answer_version.id,
                answer_text_joined=answer_version.answer_text,
                coverage_status=coverage_status,
                updated_at=now,
            )
            db.add(current_answer)
            answer_record_id = current_answer.id
        _write_review_record(
            db,
            user.id,
            "answer",
            answer_record_id,
            "ocr_correction_saved",
            previous,
            {
                "answer_version_id": answer_version.id,
                "answer_text": answer_version.answer_text,
                "is_blank_confirmed": bool(answer_version.is_blank_confirmed),
                "coverage_status": coverage_status,
            },
            "teacher correction" if item.source_type == "teacher_corrected" else None,
        )
        saved_count += 1

    grouping.version += 1
    if payload.save_mode == "confirm":
        grouping.grouping_status = "confirmed"
        grouping.confirmed_by = user.id
        grouping.confirmed_at = now
        grouping.assignment_group_id = grouping.assignment_group_id
        group = db.get(AssignmentGroup, grouping.assignment_group_id)
        if group:
            group.status = "assigned"
            group.confirmed_by = user.id
            group.confirmed_at = now
            group.updated_at = now
            group.version += 1
    else:
        grouping.grouping_status = "suggested"
    _write_review_record(
        db,
        user.id,
        "grouping",
        grouping.id,
        "ocr_correction_confirmed" if payload.save_mode == "confirm" else "ocr_correction_saved",
        {"version": payload.version},
        {"version": grouping.version, "saved_answer_count": saved_count},
        None,
    )
    db.commit()
    result = _page_payload(db, page, source_file, detail=True)
    result["saved_answer_count"] = saved_count
    result["save_mode"] = payload.save_mode
    return success_payload(request, result)


@router.post("/api/v1/pages/{page_id}/confirm")
def confirm_page(
    page_id: str,
    payload: PageConfirmRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    page, source_file, batch = _owned_page(db, user.id, page_id)
    grouping = _get_page_grouping(db, page.id)
    if not grouping or not grouping.assignment_group_id or not grouping.student_id:
        raise _correction_blocked("确认页面前必须完成学生归属。", [{"code": "GROUPING_REQUIRED"}])
    if grouping.page_sequence is None:
        raise _correction_blocked("确认页面前必须填写学生作业内页序。", [{"code": "PAGE_SEQUENCE_REQUIRED"}])
    if grouping.version != payload.version:
        raise ApiError(
            409,
            "RESOURCE_VERSION_CONFLICT",
            "页面校对已被其他请求修改，请刷新后再提交。",
            [{"expected": payload.version, "actual": grouping.version}],
        )
    _validate_page_sequences(db, grouping.assignment_group_id)
    now = utc_now()
    before = {"grouping_status": grouping.grouping_status, "version": grouping.version}
    grouping.grouping_status = "confirmed"
    grouping.confirmed_by = user.id
    grouping.confirmed_at = now
    grouping.version += 1
    group = db.get(AssignmentGroup, grouping.assignment_group_id)
    if group:
        group.status = "assigned"
        group.confirmed_by = user.id
        group.confirmed_at = now
        group.updated_at = now
        group.version += 1
    _write_review_record(
        db,
        user.id,
        "grouping",
        grouping.id,
        "page_grouping_confirmed",
        before,
        {"grouping_status": grouping.grouping_status, "version": grouping.version},
        None,
    )
    db.commit()
    return success_payload(request, _page_payload(db, page, source_file, detail=True))


@router.get("/api/v1/pages/{page_id}/history")
def page_history(page_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page, _source_file, _batch = _owned_page(db, user.id, page_id)
    grouping = _get_page_grouping(db, page.id)
    records = []
    entity_ids = {page.id}
    if grouping:
        entity_ids.add(grouping.id)
        answer_records = list(
            db.scalars(select(QuestionAnswer).where(QuestionAnswer.assignment_group_id == grouping.assignment_group_id))
        )
        entity_ids.update(item.id for item in answer_records)
    review_rows = list(
        db.scalars(
            select(ReviewRecord)
            .where(ReviewRecord.entity_type.in_(["grouping", "answer"]), ReviewRecord.entity_id.in_(entity_ids))
            .order_by(ReviewRecord.created_at.desc())
        )
    )
    for item in review_rows:
        records.append(
            {
                "id": item.id,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "action": item.action,
                "before": _parse_json(item.before_json, None),
                "after": _parse_json(item.after_json, None),
                "reason": item.reason,
                "created_at": item.created_at,
            }
        )
    answer_versions = list(
        db.scalars(select(AnswerVersion).where(AnswerVersion.source_page_id == page.id).order_by(AnswerVersion.created_at.desc()))
    )
    return success_payload(
        request,
        {
            "page_id": page.id,
            "reviews": records,
            "answer_versions": [
                {
                    "id": item.id,
                    "assignment_group_id": item.assignment_group_id,
                    "source_page_id": item.source_page_id,
                    "answer_photo_id": item.answer_photo_id,
                    "question_id": item.question_id,
                    "version_no": item.version_no,
                    "answer_text": item.answer_text,
                    "is_blank_confirmed": bool(item.is_blank_confirmed),
                    "source_type": item.source_type,
                    "confidence": str(item.confidence) if item.confidence is not None else None,
                    "created_by": item.created_by,
                    "created_at": item.created_at,
                }
                for item in answer_versions
            ],
        },
    )
