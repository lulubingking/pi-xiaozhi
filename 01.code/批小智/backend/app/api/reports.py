"""批改报告查询接口；正式口径只统计整份作业已完成教师复核的学生。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
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
    BatchQuestion,
    ClassRoom,
    GradingResult,
    GradingRun,
    GradingTask,
    PageGrouping,
    ReportExport,
    SourceFile,
    SourcePage,
    Student,
    User,
)
from app.schemas import ReportExportRequest

router = APIRouter(tags=["reports"])
settings = get_settings()


def _score_text(value: Decimal | int | float | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _parse_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _latest_run(db: Session, batch_id: str) -> GradingRun | None:
    return db.scalar(select(GradingRun).where(GradingRun.batch_id == batch_id).order_by(GradingRun.created_at.desc()).limit(1))


def _latest_result(db: Session, task_id: str) -> GradingResult | None:
    return db.scalar(
        select(GradingResult)
        .where(GradingResult.grading_task_id == task_id)
        .order_by(GradingResult.updated_at.desc(), GradingResult.created_at.desc())
        .limit(1)
    )


def _report_rows(db: Session, batch: AssignmentBatch, mode: str) -> tuple[list[dict[str, Any]], int]:
    if mode not in {"reviewed", "ai_preview"}:
        raise ApiError(422, "REPORT_MODE_INVALID", "报告 mode 只能是 reviewed 或 ai_preview。")
    questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id).order_by(BatchQuestion.sort_order)))
    groups = list(db.scalars(select(AssignmentGroup).where(AssignmentGroup.batch_id == batch.id).order_by(AssignmentGroup.created_at, AssignmentGroup.id)))
    run = _latest_run(db, batch.id)
    all_tasks = list(db.scalars(select(GradingTask).where(GradingTask.grading_run_id == run.id))) if run else []
    results_by_group: dict[str, dict[str, GradingResult]] = {}
    for task in all_tasks:
        result = _latest_result(db, task.id)
        if result:
            results_by_group.setdefault(task.assignment_group_id, {})[task.question_id] = result

    rows: list[dict[str, Any]] = []
    pending_count = 0
    for group in groups:
        student = db.get(Student, group.student_id) if group.student_id else None
        result_map = results_by_group.get(group.id, {})
        complete = bool(questions) and all(result_map.get(question.id) and result_map[question.id].result_status == "reviewed" for question in questions)
        if not complete:
            pending_count += 1
        if mode == "reviewed" and not complete:
            continue
        score_values: list[Decimal] = []
        objective_score = Decimal("0")
        subjective_score = Decimal("0")
        objective_max = Decimal("0")
        subjective_max = Decimal("0")
        missing_questions: list[str] = []
        for question in questions:
            result = result_map.get(question.id)
            if result is None:
                missing_questions.append(question.question_no)
                continue
            score = result.teacher_score if mode == "reviewed" else result.suggested_score
            if score is None:
                missing_questions.append(question.question_no)
                continue
            score_values.append(score)
            if question.question_type == "objective":
                objective_score += score
                objective_max += question.max_score
            else:
                subjective_score += score
                subjective_max += question.max_score
        total_score = sum(score_values, Decimal("0")) if score_values else None
        rows.append(
            {
                "assignment_group_id": group.id,
                "student_id": student.id if student else None,
                "student_code": student.student_code if student else None,
                "student_name": student.display_name if student else None,
                "objective_score": _score_text(objective_score) if score_values else None,
                "objective_max_score": _score_text(objective_max) if objective_max else None,
                "subjective_score": _score_text(subjective_score) if score_values else None,
                "subjective_max_score": _score_text(subjective_max) if subjective_max else None,
                "total_score": _score_text(total_score),
                "total_max_score": _score_text(batch.total_score),
                "status": "reviewed" if complete else "pending_review",
                "missing_questions": missing_questions,
            }
        )
    return rows, pending_count


def _report_context(batch: AssignmentBatch, mode: str, rows: list[dict[str, Any]], pending_count: int) -> dict[str, Any]:
    return {
        "batch_id": batch.id,
        "batch_title": batch.title,
        "class_id": batch.class_id,
        "subject": batch.subject,
        "total_score": _score_text(batch.total_score),
        "mode": mode,
        "students": rows,
        "pending_review_count": pending_count,
        "generated_at": utc_now(),
    }


def _student_report_detail(
    db: Session,
    batch: AssignmentBatch,
    group: AssignmentGroup,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Build the traceable evidence shown when a teacher opens one report row."""

    page_rows = list(
        db.execute(
            select(SourcePage, SourceFile, PageGrouping)
            .join(SourceFile, SourceFile.id == SourcePage.source_file_id)
            .join(PageGrouping, PageGrouping.source_page_id == SourcePage.id)
            .where(
                SourcePage.batch_id == batch.id,
                SourceFile.file_role == "student_work",
                PageGrouping.assignment_group_id == group.id,
            )
        ).all()
    )
    page_rows.sort(
        key=lambda item: (
            item[2].page_sequence is None,
            item[2].page_sequence if item[2].page_sequence is not None else item[0].page_index,
            item[0].page_index,
            item[0].id,
        )
    )
    pages = [
        {
            "id": page.id,
            "original_name": source_file.original_name,
            "mime_type": source_file.mime_type,
            "extension": source_file.extension,
            "page_index": page.page_index,
            "page_count": source_file.page_count,
            "page_sequence": grouping.page_sequence,
            "status": page.status,
            "is_pdf": source_file.mime_type == "application/pdf" or source_file.extension.lower().lstrip(".") == "pdf",
        }
        for page, source_file, grouping in page_rows
    ]

    questions = list(
        db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id).order_by(BatchQuestion.sort_order))
    )
    run = _latest_run(db, batch.id)
    tasks = (
        list(
            db.scalars(
                select(GradingTask).where(
                    GradingTask.grading_run_id == run.id,
                    GradingTask.assignment_group_id == group.id,
                )
            )
        )
        if run
        else []
    )
    result_by_question: dict[str, GradingResult] = {}
    task_by_question: dict[str, GradingTask] = {}
    for task in tasks:
        task_by_question[task.question_id] = task
        result = _latest_result(db, task.id)
        if result:
            result_by_question[task.question_id] = result

    ai_scores = [result.suggested_score for result in result_by_question.values() if result.suggested_score is not None]
    teacher_scores = [result.teacher_score for result in result_by_question.values() if result.teacher_score is not None]
    question_rows: list[dict[str, Any]] = []
    for question in questions:
        result = result_by_question.get(question.id)
        task = task_by_question.get(question.id)
        question_rows.append(
            {
                "id": question.id,
                "question_no": question.question_no,
                "question_type": question.question_type,
                "question_prompt": question.question_prompt,
                "max_score": _score_text(question.max_score),
                "ai_score": _score_text(result.suggested_score) if result else None,
                "teacher_score": _score_text(result.teacher_score) if result else None,
                "ai_comment": result.ai_comment if result else None,
                "teacher_comment": result.teacher_comment if result else None,
                "evidence": _parse_json(result.evidence_json, None) if result else None,
                "deduction": _parse_json(result.deduction_json, None) if result else None,
                "result_status": result.result_status if result else "pending_review",
                "task_status": task.status if task else "pending",
                "task_error_message": task.error_message if task else None,
            }
        )

    return {
        **row,
        "ai_total_score": _score_text(sum(ai_scores, Decimal("0"))) if ai_scores else None,
        "ai_total_max_score": _score_text(batch.total_score),
        "ai_scored_question_count": len(ai_scores),
        "teacher_total_score": _score_text(sum(teacher_scores, Decimal("0"))) if teacher_scores else None,
        "teacher_total_max_score": _score_text(batch.total_score),
        "pages": pages,
        "questions": question_rows,
    }


def _build_export_snapshot(db: Session, batch: AssignmentBatch, mode: str) -> dict[str, Any]:
    """冻结导出所需的数据，Worker 只消费这份快照。"""

    rows, pending_count = _report_rows(db, batch, mode)
    questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id).order_by(BatchQuestion.sort_order)))
    groups = list(db.scalars(select(AssignmentGroup).where(AssignmentGroup.batch_id == batch.id)))
    group_by_id = {group.id: group for group in groups}
    run = _latest_run(db, batch.id)
    results_by_group: dict[str, dict[str, GradingResult]] = {}
    if run:
        for task in db.scalars(select(GradingTask).where(GradingTask.grading_run_id == run.id)):
            result = _latest_result(db, task.id)
            if result:
                results_by_group.setdefault(task.assignment_group_id, {})[task.question_id] = result

    student_rows: list[dict[str, Any]] = []
    for row in rows:
        result_map = results_by_group.get(row["assignment_group_id"], {})
        question_rows: list[dict[str, Any]] = []
        for question in questions:
            result = result_map.get(question.id)
            score = result.teacher_score if result and mode == "reviewed" else result.suggested_score if result else None
            question_rows.append(
                {
                    "question_no": question.question_no,
                    "question_type": question.question_type,
                    "max_score": _score_text(question.max_score),
                    "score": _score_text(score),
                    "status": result.result_status if result else "pending_review",
                    "teacher_comment": result.teacher_comment if result else None,
                    "ai_comment": result.ai_comment if result else None,
                    "evidence": _parse_json(result.evidence_json, None) if result else None,
                    "deduction": _parse_json(result.deduction_json, None) if result else None,
                }
            )
        student_rows.append({**row, "questions": question_rows})

    scores = [Decimal(item["total_score"]) for item in rows if item["total_score"] is not None]
    max_score = Decimal(batch.total_score)
    distribution = {"100%+": 0, "90-99%": 0, "80-89%": 0, "70-79%": 0, "60-69%": 0, "0-59%": 0}
    for score in scores:
        ratio = (score / max_score * Decimal("100")) if max_score else Decimal("0")
        key = "100%+" if ratio >= 100 else "90-99%" if ratio >= 90 else "80-89%" if ratio >= 80 else "70-79%" if ratio >= 70 else "60-69%" if ratio >= 60 else "0-59%"
        distribution[key] += 1
    highest = max(rows, key=lambda item: Decimal(item["total_score"] or "-1"), default=None)
    deduction_counter: Counter[str] = Counter()
    for student in student_rows:
        for question in student["questions"]:
            deduction = question.get("deduction")
            if isinstance(deduction, list):
                for item in deduction:
                    if isinstance(item, dict) and item.get("reason"):
                        deduction_counter[str(item["reason"])] += 1

    class_room = db.get(ClassRoom, batch.class_id)
    snapshot = {
        "batch": {
            "id": batch.id,
            "title": batch.title,
            "class_id": batch.class_id,
            "class_name": class_room.name if class_room else None,
            "subject": batch.subject,
            "total_score": _score_text(batch.total_score),
            "mode": mode,
            "pending_review_count": pending_count,
            "generated_at": utc_now(),
        },
        "statistics": {
            "student_count": len(rows),
            "scored_count": len(scores),
            "pending_review_count": pending_count,
            "average_score": _score_text(sum(scores, Decimal("0")) / len(scores) if scores else None),
            "highest_score": highest["total_score"] if highest else None,
            "highest_student_name": highest["student_name"] if highest else None,
            "distribution": distribution,
            "frequent_errors": [{"label": label, "count": count} for label, count in deduction_counter.most_common(10)],
        },
        "students": student_rows,
    }
    return snapshot


def _owned_export(db: Session, user_id: str, export_id: str) -> ReportExport:
    export = db.scalar(
        select(ReportExport)
        .join(AssignmentBatch, ReportExport.batch_id == AssignmentBatch.id)
        .where(ReportExport.id == export_id, AssignmentBatch.owner_user_id == user_id)
    )
    if not export:
        raise ApiError(404, "REPORT_EXPORT_NOT_FOUND", "导出任务不存在或当前账号无权访问。")
    return export


def _export_path(storage_key: str) -> Path:
    root = Path(settings.storage_root).resolve()
    candidate = (root / storage_key).resolve()
    if root != candidate and root not in candidate.parents:
        raise ApiError(400, "INVALID_STORAGE_KEY", "报告文件存储路径校验失败。")
    return candidate


def _export_payload(export: ReportExport) -> dict[str, Any]:
    return {
        "id": export.id,
        "batch_id": export.batch_id,
        "report_mode": export.report_mode,
        "format": export.format,
        "status": export.status,
        "input_hash": export.input_hash,
        "failure_code": export.failure_code,
        "created_at": export.created_at,
        "finished_at": export.finished_at,
        "download_url": f"/api/v1/report-exports/{export.id}/download" if export.status == "succeeded" else None,
    }


@router.get("/api/v1/batches/{batch_id}/reports")
def get_batch_report(
    batch_id: str,
    request: Request,
    mode: str = Query(default="reviewed"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    rows, pending_count = _report_rows(db, batch, mode)
    start = (page - 1) * page_size
    data = _report_context(batch, mode, rows[start : start + page_size], pending_count)
    data["page"] = page
    data["page_size"] = page_size
    data["total"] = len(rows)
    return success_payload(request, data, meta={"page": page, "page_size": page_size, "total": len(rows)})


@router.get("/api/v1/batches/{batch_id}/reports/students/{student_id}")
def get_student_report(
    batch_id: str,
    student_id: str,
    request: Request,
    mode: str = Query(default="reviewed"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    rows, pending_count = _report_rows(db, batch, mode)
    row = next((item for item in rows if item["student_id"] == student_id), None)
    if row is None:
        raise ApiError(404, "REPORT_STUDENT_NOT_FOUND", "当前报告口径下不存在该学生成绩。")
    group = db.get(AssignmentGroup, row["assignment_group_id"])
    if group is None:
        raise ApiError(404, "REPORT_STUDENT_NOT_FOUND", "当前学生作业对象不存在或已被移除。")
    detail = _student_report_detail(db, batch, group, row)
    return success_payload(request, {"batch": _report_context(batch, mode, [], pending_count), "student": detail})


@router.get("/api/v1/batches/{batch_id}/reports/statistics")
def get_report_statistics(
    batch_id: str,
    request: Request,
    mode: str = Query(default="reviewed"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    rows, pending_count = _report_rows(db, batch, mode)
    scores = [Decimal(item["total_score"]) for item in rows if item["total_score"] is not None]
    max_score = Decimal(batch.total_score)
    distribution = {"100%+": 0, "90-99%": 0, "80-89%": 0, "70-79%": 0, "60-69%": 0, "0-59%": 0}
    for score in scores:
        ratio = (score / max_score * Decimal("100")) if max_score else Decimal("0")
        key = "100%+" if ratio >= 100 else "90-99%" if ratio >= 90 else "80-89%" if ratio >= 80 else "70-79%" if ratio >= 70 else "60-69%" if ratio >= 60 else "0-59%"
        distribution[key] += 1
    highest = max(rows, key=lambda item: Decimal(item["total_score"] or "-1"), default=None)
    deduction_counter: Counter[str] = Counter()
    run = _latest_run(db, batch.id)
    if run:
        for task in db.scalars(select(GradingTask).where(GradingTask.grading_run_id == run.id)):
            result = _latest_result(db, task.id)
            if not result:
                continue
            deduction = _parse_json(result.deduction_json, [])
            if isinstance(deduction, list):
                for item in deduction:
                    if isinstance(item, dict) and item.get("reason"):
                        deduction_counter[str(item["reason"])] += 1
    return success_payload(
        request,
        {
            "batch_id": batch.id,
            "batch_title": batch.title,
            "mode": mode,
            "student_count": len(rows),
            "scored_count": len(scores),
            "pending_review_count": pending_count,
            "average_score": _score_text(sum(scores, Decimal("0")) / len(scores) if scores else None),
            "highest_score": highest["total_score"] if highest else None,
            "highest_student_name": highest["student_name"] if highest else None,
            "distribution": distribution,
            "frequent_errors": [{"label": label, "count": count} for label, count in deduction_counter.most_common(10)],
        },
    )


@router.post("/api/v1/batches/{batch_id}/reports/exports", status_code=202)
def create_report_export(
    batch_id: str,
    payload: ReportExportRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """冻结当前报告并加入导出队列；文件生成由独立 Worker 完成。"""

    batch = get_owned_batch(db, user.id, batch_id)
    snapshot = _build_export_snapshot(db, batch, payload.report_mode)
    scope_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    export = ReportExport(
        id=new_id("report_export"),
        batch_id=batch.id,
        requested_by=user.id,
        report_mode=payload.report_mode,
        format=payload.format,
        scope_json=scope_json,
        input_hash=hashlib.sha256(scope_json.encode("utf-8")).hexdigest(),
        status="queued",
        storage_key=None,
        failure_code=None,
        created_at=utc_now(),
        finished_at=None,
    )
    db.add(export)
    db.commit()
    return success_payload(request, _export_payload(export))


@router.get("/api/v1/report-exports/{export_id}")
def get_report_export(
    export_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    export = _owned_export(db, user.id, export_id)
    return success_payload(request, _export_payload(export))


@router.get("/api/v1/report-exports/{export_id}/download")
def download_report_export(
    export_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    export = _owned_export(db, user.id, export_id)
    if export.status != "succeeded" or not export.storage_key:
        raise ApiError(409, "REPORT_EXPORT_NOT_READY", "报告文件尚未生成完成，请稍后重试。")
    path = _export_path(export.storage_key)
    if not path.is_file():
        raise ApiError(404, "REPORT_EXPORT_FILE_MISSING", "报告文件不存在，请重新发起导出。")
    media_type = "application/pdf" if export.format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    filename = f"pixiaozhi-report-{export.batch_id}-{export.id}.{export.format}"
    return FileResponse(path, media_type=media_type, filename=filename)
