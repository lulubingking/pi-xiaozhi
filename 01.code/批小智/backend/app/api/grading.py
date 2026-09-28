"""批改运行、逐题任务和评分结果查询接口。

本阶段只负责严格校验前置条件并持久化任务，不调用外部模型，也不生成伪造分数。
客观题规则引擎和主观题模型 Worker 在任务入队后读取这些记录继续处理。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.batches import get_owned_batch
from app.api.deps import get_current_user
from app.api.errors import ApiError, success_payload
from app.core.model_config import get_model_config, resolve_api_key
from app.core.utils import new_id, utc_now
from app.db.session import get_db
from app.models import (
    AnswerVersion,
    Anomaly,
    AssignmentBatch,
    AssignmentGroup,
    BatchQuestion,
    GradingPointResult,
    GradingResult,
    GradingRun,
    GradingTask,
    PageGrouping,
    QuestionAnswer,
    Rubric,
    RubricPoint,
    RubricVersion,
    ReviewRecord,
    SourceFile,
    SourcePage,
    Student,
    Task,
    User,
)
from app.schemas import GradingRunRequest, GradingScopeRequest, ReviewItemRequest
from app.scoring.llm_adapter import PROMPT_VERSION as SUBJECTIVE_PROMPT_VERSION
from app.scoring.objective_rules import RULE_VERSION as OBJECTIVE_RULE_VERSION

router = APIRouter(tags=["grading"])

RULE_VERSION = OBJECTIVE_RULE_VERSION
PROMPT_VERSION = SUBJECTIVE_PROMPT_VERSION
CONFIRMED_COVERAGE_STATUSES = {"complete", "reviewed"}


def _parse_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _score_text(value: Any) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _detail(code: str, message: str, **fields: Any) -> dict[str, Any]:
    result = {"code": code, "message": message}
    result.update(fields)
    return result


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
    """统一记录评分结果的保存、确认和重跑审计。"""

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


def _canonical_request(payload: GradingRunRequest) -> str:
    body = payload.model_dump(mode="json")
    body["scope"]["assignment_group_ids"] = sorted(set(body["scope"]["assignment_group_ids"]))
    body["scope"]["question_ids"] = sorted(set(body["scope"]["question_ids"]))
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _scope_ids(scope: GradingScopeRequest) -> tuple[list[str], list[str]]:
    return list(dict.fromkeys(scope.assignment_group_ids)), list(dict.fromkeys(scope.question_ids))


def _owned_run(db: Session, user: User, run_id: str) -> tuple[GradingRun, AssignmentBatch]:
    row = db.execute(
        select(GradingRun, AssignmentBatch)
        .join(AssignmentBatch, AssignmentBatch.id == GradingRun.batch_id)
        .where(GradingRun.id == run_id, AssignmentBatch.owner_user_id == user.id)
    ).first()
    if not row:
        raise ApiError(404, "GRADING_RUN_NOT_FOUND", "批改运行不存在或当前账号无权访问。")
    return row[0], row[1]


def _owned_grading_task(db: Session, user: User, task_id: str) -> tuple[GradingTask, GradingRun, AssignmentBatch]:
    row = db.execute(
        select(GradingTask, GradingRun, AssignmentBatch)
        .join(GradingRun, GradingRun.id == GradingTask.grading_run_id)
        .join(AssignmentBatch, AssignmentBatch.id == GradingRun.batch_id)
        .where(GradingTask.id == task_id, AssignmentBatch.owner_user_id == user.id)
    ).first()
    if not row:
        raise ApiError(404, "GRADING_TASK_NOT_FOUND", "批改题目任务不存在或当前账号无权访问。")
    return row[0], row[1], row[2]


def _model_snapshot(db: Session, user: User) -> dict[str, str | None]:
    config = get_model_config(db, user)
    return {
        "model_provider": config.provider_name,
        "model_name": config.model_name,
    }


def _validate_scope_and_inputs(
    db: Session,
    user: User,
    batch: AssignmentBatch,
    requested_scope: GradingScopeRequest,
) -> tuple[list[AssignmentGroup], list[BatchQuestion], dict[tuple[str, str], QuestionAnswer], list[dict[str, Any]]]:
    """返回解析后的范围和阻塞项；阻塞项必须在事务写入前完整检查。"""

    errors: list[dict[str, Any]] = []
    requested_group_ids, requested_question_ids = _scope_ids(requested_scope)

    all_questions = list(
        db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id).order_by(BatchQuestion.sort_order))
    )
    all_groups = list(db.scalars(select(AssignmentGroup).where(AssignmentGroup.batch_id == batch.id)))
    questions_by_id = {item.id: item for item in all_questions}
    groups_by_id = {item.id: item for item in all_groups}

    if not all_questions:
        errors.append(_detail("QUESTIONS_REQUIRED", "批次至少需要配置一道题目。"))
    if not all_groups:
        errors.append(_detail("ASSIGNMENT_GROUP_REQUIRED", "批次还没有可批改的学生作业对象。"))

    if requested_question_ids:
        for question_id in requested_question_ids:
            if question_id not in questions_by_id:
                errors.append(_detail("QUESTION_NOT_IN_BATCH", "指定题目不属于当前批次。", question_id=question_id))
        questions = [questions_by_id[item] for item in requested_question_ids if item in questions_by_id]
    else:
        questions = all_questions

    if requested_group_ids:
        for group_id in requested_group_ids:
            if group_id not in groups_by_id:
                errors.append(_detail("ASSIGNMENT_GROUP_NOT_IN_BATCH", "指定学生作业对象不属于当前批次。", assignment_group_id=group_id))
        groups = [groups_by_id[item] for item in requested_group_ids if item in groups_by_id]
    else:
        groups = [item for item in all_groups if item.student_id]

    if batch.status in {"archived", "reviewed"}:
        errors.append(_detail("BATCH_STATUS_BLOCKED", "当前批次状态不允许发起新的批改运行。", status=batch.status))

    source_files = list(
        db.scalars(select(SourceFile).where(SourceFile.batch_id == batch.id, SourceFile.file_role == "student_work"))
    )
    if not source_files:
        errors.append(_detail("SOURCE_FILES_REQUIRED", "批次还没有上传成功的作业文件。"))

    pages = list(
        db.scalars(
            select(SourcePage)
            .join(SourceFile, SourcePage.source_file_id == SourceFile.id)
            .where(SourcePage.batch_id == batch.id)
            .where(SourceFile.file_role == "student_work")
            .order_by(SourcePage.source_file_id, SourcePage.page_index)
        )
    )
    group_ids = {item.id for item in groups}
    relevant_pages = []
    groupings: dict[str, PageGrouping] = {}
    for page in pages:
        grouping = db.scalar(select(PageGrouping).where(PageGrouping.source_page_id == page.id))
        if grouping:
            groupings[page.id] = grouping
        if not requested_group_ids or (grouping and grouping.assignment_group_id in group_ids):
            relevant_pages.append((page, grouping))

    if not pages:
        errors.append(_detail("OCR_PAGES_REQUIRED", "批次还没有可用于批改的页面。"))
    elif not relevant_pages:
        errors.append(_detail("GRADING_SCOPE_EMPTY", "指定范围没有对应的已生成页面。"))

    relevant_file_ids = {page.source_file_id for page, _grouping in relevant_pages}
    file_by_id = {item.id: item for item in source_files}
    for file_id in relevant_file_ids:
        source_file = file_by_id.get(file_id)
        if source_file and source_file.status != "success":
            errors.append(
                _detail(
                    "SOURCE_FILE_NOT_READY",
                    "范围内存在未完成上传处理的文件。",
                    file_id=file_id,
                    status=source_file.status,
                )
            )

    relevant_group_ids = set()
    for page, grouping in relevant_pages:
        if grouping and grouping.assignment_group_id:
            relevant_group_ids.add(grouping.assignment_group_id)
        if page.status != "ocr_ready":
            errors.append(
                _detail(
                    "OCR_NOT_READY",
                    "范围内存在尚未完成 OCR 的页面。",
                    page_id=page.id,
                    status=page.status,
                )
            )
        if (
            not grouping
            or grouping.grouping_status != "confirmed"
            or not grouping.assignment_group_id
            or not grouping.student_id
            or grouping.page_sequence is None
        ):
            errors.append(
                _detail(
                    "PAGE_GROUPING_UNCONFIRMED",
                    "范围内存在未确认学生归属或页序的页面。",
                    page_id=page.id,
                )
            )

    if not requested_group_ids:
        groups = [item for item in groups if item.id in relevant_group_ids]
    for group in groups:
        if not group.student_id or group.status != "assigned":
            errors.append(
                _detail(
                    "ASSIGNMENT_GROUP_UNCONFIRMED",
                    "学生作业对象尚未完成学生归属确认。",
                    assignment_group_id=group.id,
                    status=group.status,
                )
            )

    qa_rows = []
    if groups and questions:
        qa_rows = list(
            db.scalars(
                select(QuestionAnswer).where(
                    QuestionAnswer.assignment_group_id.in_([item.id for item in groups]),
                    QuestionAnswer.question_id.in_([item.id for item in questions]),
                )
            )
        )
    answers = {(item.assignment_group_id, item.question_id): item for item in qa_rows}
    answer_versions = {
        item.id: item
        for item in db.scalars(select(AnswerVersion).where(AnswerVersion.id.in_([item.current_version_id for item in qa_rows])))
    } if qa_rows else {}

    for group in groups:
        for question in questions:
            answer = answers.get((group.id, question.id))
            if not answer:
                errors.append(
                    _detail(
                        "ANSWER_COVERAGE_MISSING",
                        "学生作业对象缺少该题的已确认答案记录。",
                        assignment_group_id=group.id,
                        question_id=question.id,
                    )
                )
                continue
            if answer.coverage_status not in CONFIRMED_COVERAGE_STATUSES:
                errors.append(
                    _detail(
                        "ANSWER_COVERAGE_NOT_CONFIRMED",
                        "该题答案尚未完成教师确认。",
                        assignment_group_id=group.id,
                        question_id=question.id,
                        coverage_status=answer.coverage_status,
                    )
                )
            if answer.current_version_id not in answer_versions:
                errors.append(
                    _detail(
                        "ANSWER_VERSION_MISSING",
                        "题目答案缺少当前版本，不能创建可追溯批改任务。",
                        assignment_group_id=group.id,
                        question_id=question.id,
                    )
                )

    subjective_questions = [item for item in questions if item.question_type == "subjective"]
    for question in subjective_questions:
        if not (question.question_prompt or "").strip():
            errors.append(
                _detail(
                    "SUBJECTIVE_PROMPT_MISSING",
                    "主观题缺少来自上传试卷的已确认题干。",
                    question_id=question.id,
                )
            )
        if not question.rubric_version_id:
            errors.append(
                _detail("SUBJECTIVE_RUBRIC_MISSING", "主观题缺少评分标准版本。", question_id=question.id)
            )
            continue
        version = db.get(RubricVersion, question.rubric_version_id)
        rubric = db.get(Rubric, version.rubric_id) if version else None
        if (
            not version
            or version.created_by != user.id
            or not rubric
            or rubric.owner_user_id != user.id
            or rubric.status != "active"
            or rubric.subject != batch.subject
        ):
            errors.append(
                _detail(
                    "SUBJECTIVE_RUBRIC_INVALID",
                    "主观题评分标准不存在、已停用或与批次学科不一致。",
                    question_id=question.id,
                    rubric_version_id=question.rubric_version_id,
                )
            )

    if subjective_questions:
        model = get_model_config(db, user)
        if not model.model_name or not model.base_url:
            errors.append(
                _detail(
                    "MODEL_CONFIG_INCOMPLETE",
                    "主观题批改需要配置服务商、模型和 Base URL。",
                )
            )
        elif not resolve_api_key(model.api_key_ref, user.id):
            errors.append(
                _detail(
                    "MODEL_KEY_UNAVAILABLE",
                    "当前后端进程中没有可用的模型 API Key，暂不能入队主观题批改。",
                )
            )

    if not questions:
        errors.append(_detail("GRADING_QUESTIONS_EMPTY", "当前批改范围没有可处理的题目。"))
    if not groups:
        errors.append(_detail("GRADING_STUDENTS_EMPTY", "当前批改范围没有可处理的学生作业对象。"))

    return groups, questions, answers, errors


def _task_input_hash(group: AssignmentGroup, question: BatchQuestion, answer: QuestionAnswer, answer_version: AnswerVersion) -> str:
    snapshot = {
        "assignment_group_id": group.id,
        "question_id": question.id,
        "question_version": question.version,
        "question_prompt": question.question_prompt,
        "answer_id": answer.id,
        "answer_version_id": answer_version.id,
        "answer_text": answer_version.answer_text,
        "coverage_status": answer.coverage_status,
        "rubric_version_id": question.rubric_version_id,
    }
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _task_payload(db: Session, task: GradingTask) -> dict[str, Any]:
    group = db.get(AssignmentGroup, task.assignment_group_id)
    student = db.get(Student, group.student_id) if group and group.student_id else None
    question = db.get(BatchQuestion, task.question_id)
    result = db.scalar(
        select(GradingResult)
        .where(GradingResult.grading_task_id == task.id)
        .order_by(GradingResult.updated_at.desc(), GradingResult.created_at.desc())
        .limit(1)
    )
    return {
        "id": task.id,
        "grading_run_id": task.grading_run_id,
        "assignment_group_id": task.assignment_group_id,
        "student_id": student.id if student else None,
        "student_code": student.student_code if student else None,
        "student_name": student.display_name if student else None,
        "question_id": task.question_id,
        "question_no": question.question_no if question else None,
        "task_type": task.task_type,
        "status": task.status,
        "input_hash": task.input_hash,
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "result_id": result.id if result else None,
        "result_status": result.result_status if result else None,
        "suggested_score": _score_text(result.suggested_score) if result else None,
        "teacher_score": _score_text(result.teacher_score) if result else None,
        "result_version": result.version if result else None,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _run_payload(db: Session, run: GradingRun) -> dict[str, Any]:
    tasks = list(db.scalars(select(GradingTask).where(GradingTask.grading_run_id == run.id).order_by(GradingTask.created_at, GradingTask.id)))
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    scope_snapshot = _parse_json(run.scope_json, {})
    task_record = db.scalar(select(Task).where(Task.resource_id == run.id, Task.task_type == "grading"))
    execution_scope = (
        "single_task"
        if task_record and task_record.progress_total > 0 and task_record.progress_total < len(tasks)
        else "run"
    )
    return {
        "id": run.id,
        "batch_id": run.batch_id,
        "scope": scope_snapshot.get("scope", scope_snapshot) if isinstance(scope_snapshot, dict) else {},
        "model_provider": scope_snapshot.get("model_provider") if isinstance(scope_snapshot, dict) else None,
        "status": run.status,
        "execution_scope": execution_scope,
        "rule_version": run.rule_version,
        "model_name": run.model_name,
        "prompt_version": run.prompt_version,
        "created_by": run.created_by,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "task_id": task_record.id if task_record else None,
        "task_status": task_record.status if task_record else None,
        "stage": task_record.stage if task_record else None,
        "progress_current": task_record.progress_current if task_record else 0,
        "progress_total": task_record.progress_total if task_record else len(tasks),
        "worker_id": task_record.worker_id if task_record else None,
        "heartbeat_at": task_record.heartbeat_at if task_record else None,
        "error_code": task_record.error_code if task_record else None,
        "error_message": task_record.error_message if task_record else None,
        "counts": counts,
        "total_tasks": len(tasks),
    }


@router.post("/batches/{batch_id}/grading-runs", status_code=202)
def create_grading_run(
    batch_id: str,
    payload: GradingRunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    request_hash = hashlib.sha256(_canonical_request(payload).encode("utf-8")).hexdigest()
    if idempotency_key:
        existing_task = db.scalar(
            select(Task).where(Task.batch_id == batch.id, Task.task_type == "grading", Task.idempotency_key == idempotency_key)
        )
        if existing_task:
            if existing_task.request_hash != request_hash:
                raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "相同幂等键已用于不同的批改请求，请更换幂等键后重试。")
            existing_run = db.get(GradingRun, existing_task.resource_id) if existing_task.resource_id else None
            if existing_run:
                return success_payload(request, {"run": _run_payload(db, existing_run), "reused": True})

    active_run = db.scalar(
        select(GradingRun)
        .where(GradingRun.batch_id == batch.id, GradingRun.status.in_(["queued", "running"]))
        .order_by(GradingRun.created_at.desc())
        .limit(1)
    )
    if active_run:
        raise ApiError(409, "GRADING_TASK_ALREADY_RUNNING", "当前批次已有批改运行排队或执行中，请先查询该运行状态。", [{"run_id": active_run.id}])

    groups, questions, answers, errors = _validate_scope_and_inputs(db, user, batch, payload.scope)
    if errors:
        raise ApiError(422, "GRADING_PRECONDITION_FAILED", "当前批次尚未满足发起批改的前置条件。", errors)

    model_snapshot = _model_snapshot(db, user)
    group_ids = [item.id for item in groups]
    question_ids = [item.id for item in questions]
    previous_runs = list(
        db.scalars(select(GradingRun).where(GradingRun.batch_id == batch.id).order_by(GradingRun.created_at.desc()))
    )
    for previous_run in previous_runs:
        previous_scope = _parse_json(previous_run.scope_json, {})
        previous_resolved = previous_scope.get("scope", {}) if isinstance(previous_scope, dict) else {}
        if (
            set(previous_resolved.get("assignment_group_ids", [])) == set(group_ids)
            and set(previous_resolved.get("question_ids", [])) == set(question_ids)
            and not payload.force_rerun
        ):
            raise ApiError(
                409,
                "GRADING_RUN_EXISTS",
                "当前范围已经存在批改运行；如需重新评估，请显式传入 force_rerun=true。",
                [{"run_id": previous_run.id, "status": previous_run.status}],
            )

    now = utc_now()
    scope_snapshot = {
        "scope": {"assignment_group_ids": group_ids, "question_ids": question_ids},
        "requested_scope": payload.scope.model_dump(mode="json"),
        "force_rerun": payload.force_rerun,
        **model_snapshot,
    }
    run = GradingRun(
        id=new_id("grading_run"),
        batch_id=batch.id,
        scope_json=json.dumps(scope_snapshot, ensure_ascii=False, sort_keys=True),
        status="queued",
        rule_version=RULE_VERSION,
        model_name=model_snapshot["model_name"] if any(item.question_type == "subjective" for item in questions) else None,
        prompt_version=PROMPT_VERSION,
        created_by=user.id,
        created_at=now,
        finished_at=None,
    )
    db.add(run)
    db.flush()

    grading_tasks: list[GradingTask] = []
    for group in groups:
        for question in questions:
            answer = answers[(group.id, question.id)]
            answer_version = db.get(AnswerVersion, answer.current_version_id)
            if answer_version is None:
                raise ApiError(422, "ANSWER_VERSION_MISSING", "题目答案缺少当前版本，不能创建批改任务。")
            grading_tasks.append(
                GradingTask(
                    id=new_id("grading_task"),
                    grading_run_id=run.id,
                    assignment_group_id=group.id,
                    question_id=question.id,
                    task_type=question.question_type,
                    status="queued",
                    input_hash=_task_input_hash(group, question, answer, answer_version),
                    retry_count=0,
                    max_retries=2,
                    locked_by=None,
                    heartbeat_at=None,
                    error_code=None,
                    error_message=None,
                    created_at=now,
                    updated_at=now,
                )
            )
    db.add_all(grading_tasks)
    queue_task = Task(
        id=new_id("task"),
        task_type="grading",
        batch_id=batch.id,
        assignment_group_id=None,
        resource_id=run.id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        options_json=json.dumps(
            {
                "grading_run_id": run.id,
                "model_provider": model_snapshot["model_provider"],
                "model_name": model_snapshot["model_name"],
                "task_count": len(grading_tasks),
            },
            ensure_ascii=False,
        ),
        status="queued",
        stage="grading_queued",
        progress_current=0,
        progress_total=len(grading_tasks),
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
    db.add(queue_task)
    batch.status = "grading"
    batch.updated_at = now
    batch.version += 1
    db.commit()
    return success_payload(
        request,
        {
            "run": _run_payload(db, run),
            "reused": False,
            "execution": "queued_for_worker",
            "message": "批改任务已入队；当前阶段不会在 API 请求中直接调用外部模型。",
        },
    )


@router.get("/grading-runs/{run_id}")
def get_grading_run(
    run_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run, _batch = _owned_run(db, user, run_id)
    return success_payload(request, _run_payload(db, run))


@router.get("/batches/{batch_id}/grading-runs/latest")
def get_latest_grading_run(
    batch_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回批次最近一次运行，供页面恢复排队/执行状态。"""

    batch = get_owned_batch(db, user.id, batch_id)
    run = db.scalar(
        select(GradingRun)
        .where(GradingRun.batch_id == batch.id)
        .order_by(GradingRun.created_at.desc())
        .limit(1)
    )
    return success_payload(request, _run_payload(db, run) if run else None)


@router.get("/batches/{batch_id}/grading-tasks")
def list_grading_tasks(
    batch_id: str,
    request: Request,
    run_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    query = select(GradingTask).join(GradingRun, GradingRun.id == GradingTask.grading_run_id).where(GradingRun.batch_id == batch.id)
    if run_id:
        query = query.where(GradingTask.grading_run_id == run_id)
    if status:
        query = query.where(GradingTask.status == status)
    tasks = list(db.scalars(query.order_by(GradingTask.created_at, GradingTask.id)))
    return success_payload(
        request,
        [_task_payload(db, item) for item in tasks],
        meta={"page": 1, "page_size": len(tasks), "total": len(tasks)},
    )


def _queue_existing_task(db: Session, task: GradingTask, run: GradingRun, batch: AssignmentBatch, *, reset_retry: bool) -> None:
    general_task = db.scalar(select(Task).where(Task.task_type == "grading", Task.resource_id == run.id))
    if general_task is None:
        raise ApiError(409, "GRADING_QUEUE_RECORD_MISSING", "批改队列记录不存在，无法安全重跑当前题目。")
    if general_task.status in {"queued", "running"}:
        raise ApiError(409, "GRADING_RUN_ALREADY_ACTIVE", "当前批次正在执行批改，请等待本轮完成后再重跑单题。")

    now = utc_now()
    if reset_retry:
        task.retry_count = 0
    else:
        task.retry_count += 1
    task.status = "queued"
    task.error_code = None
    task.error_message = None
    task.locked_by = None
    task.heartbeat_at = None
    task.updated_at = now
    run.status = "queued"
    run.finished_at = None
    batch.status = "grading"
    batch.updated_at = now
    batch.version += 1
    general_task.status = "queued"
    general_task.stage = "grading_queued"
    general_task.progress_current = 0
    general_task.progress_total = 1
    general_task.error_code = None
    general_task.error_message = None
    general_task.error_items_json = "[]"
    general_task.started_at = None
    general_task.finished_at = None
    general_task.worker_id = None
    general_task.heartbeat_at = None


@router.post("/grading-tasks/{task_id}/retry", status_code=202)
def retry_grading_task(
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task, run, batch = _owned_grading_task(db, user, task_id)
    if task.status not in {"failed", "partial_failed"}:
        raise ApiError(409, "GRADING_TASK_NOT_RETRYABLE", "只有失败的批改任务可以重试。", [{"status": task.status}])
    if task.retry_count >= task.max_retries:
        raise ApiError(409, "GRADING_RETRY_LIMIT_REACHED", "该批改任务已达到最大重试次数，请转人工处理。")
    _queue_existing_task(db, task, run, batch, reset_retry=False)
    db.commit()
    return success_payload(request, {"task": _task_payload(db, task), "execution": "queued_for_worker"})


@router.post("/grading-tasks/{task_id}/rerun", status_code=202)
def rerun_grading_task(
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task, run, batch = _owned_grading_task(db, user, task_id)
    if task.status in {"queued", "running"}:
        raise ApiError(409, "GRADING_TASK_ALREADY_RUNNING", "该题目任务已经排队或执行中。")
    _queue_existing_task(db, task, run, batch, reset_retry=True)
    db.commit()
    return success_payload(request, {"task": _task_payload(db, task), "execution": "queued_for_worker"})


def _owned_review_item(
    db: Session, user: User, item_id: str
) -> tuple[GradingResult, GradingTask, GradingRun, AssignmentBatch]:
    row = db.execute(
        select(GradingResult, GradingTask, GradingRun, AssignmentBatch)
        .join(GradingTask, GradingTask.id == GradingResult.grading_task_id)
        .join(GradingRun, GradingRun.id == GradingTask.grading_run_id)
        .join(AssignmentBatch, AssignmentBatch.id == GradingRun.batch_id)
        .where(GradingResult.id == item_id, AssignmentBatch.owner_user_id == user.id)
    ).first()
    if not row:
        raise ApiError(404, "REVIEW_ITEM_NOT_FOUND", "复核题目不存在或当前账号无权访问。")
    return row[0], row[1], row[2], row[3]


def _review_anomaly_payload(anomaly: Anomaly) -> dict[str, Any]:
    return {
        "id": anomaly.id,
        "anomaly_type": anomaly.anomaly_type,
        "severity": anomaly.severity,
        "evidence": _parse_json(anomaly.evidence_json, None),
        "status": anomaly.status,
        "resolution": anomaly.resolution,
        "created_at": anomaly.created_at,
        "resolved_at": anomaly.resolved_at,
    }


def _review_item_payload(db: Session, result: GradingResult, task: GradingTask, run: GradingRun) -> dict[str, Any]:
    group = db.get(AssignmentGroup, result.assignment_group_id)
    student = db.get(Student, group.student_id) if group and group.student_id else None
    question = db.get(BatchQuestion, result.question_id)
    answer_version = db.get(AnswerVersion, result.answer_version_id)
    source_page = db.get(SourcePage, answer_version.source_page_id) if answer_version else None
    source_file = db.get(SourceFile, source_page.source_file_id) if source_page else None
    points = list(
        db.scalars(
            select(GradingPointResult)
            .where(GradingPointResult.grading_result_id == result.id)
            .order_by(GradingPointResult.created_at, GradingPointResult.id)
        )
    )
    rubric_points = {}
    if result.rubric_version_id:
        rubric_points = {
            item.id: item
            for item in db.scalars(
                select(RubricPoint).where(RubricPoint.rubric_version_id == result.rubric_version_id)
            )
        }
    anomalies = list(
        db.scalars(
            select(Anomaly)
            .where(
                Anomaly.batch_id == run.batch_id,
                Anomaly.assignment_group_id == result.assignment_group_id,
                Anomaly.question_id == result.question_id,
            )
            .order_by(Anomaly.created_at, Anomaly.id)
        )
    )
    return {
        "id": result.id,
        "version": result.version,
        "batch_id": run.batch_id,
        "grading_run_id": run.id,
        "grading_task_id": task.id,
        "assignment_group_id": result.assignment_group_id,
        "student": {
            "id": student.id if student else None,
            "student_code": student.student_code if student else None,
            "display_name": student.display_name if student else None,
        },
        "question": {
            "id": question.id if question else result.question_id,
            "question_no": question.question_no if question else None,
            "question_type": question.question_type if question else task.task_type,
            "question_prompt": question.question_prompt if question else None,
            "reference_answer": question.reference_answer if question else None,
            "max_score": _score_text(question.max_score) if question else None,
            "rubric_version_id": question.rubric_version_id if question else result.rubric_version_id,
        },
        "answer": {
            "version_id": answer_version.id if answer_version else result.answer_version_id,
            "version_no": answer_version.version_no if answer_version else None,
            "answer_text": answer_version.answer_text if answer_version else None,
            "is_blank_confirmed": bool(answer_version.is_blank_confirmed) if answer_version else False,
            "source_type": answer_version.source_type if answer_version else None,
            "source_page_id": answer_version.source_page_id if answer_version else None,
        },
        "source_page": {
            "id": source_page.id if source_page else None,
            "page_index": source_page.page_index if source_page else None,
            "original_name": source_file.original_name if source_file else None,
        },
        "result_status": result.result_status,
        "suggested_score": _score_text(result.suggested_score),
        "teacher_score": _score_text(result.teacher_score),
        "ai_comment": result.ai_comment,
        "teacher_comment": result.teacher_comment,
        "evidence": _parse_json(result.evidence_json, None),
        "deduction": _parse_json(result.deduction_json, None),
        "model_name": result.model_name,
        "prompt_version": result.prompt_version,
        "rule_version": result.rule_version,
        "input_hash": result.input_hash,
        "task_status": task.status,
        "task_error_code": task.error_code,
        "task_error_message": task.error_message,
        "points": [
            {
                "id": item.id,
                "rubric_point_id": item.rubric_point_id,
                "label": rubric_points[item.rubric_point_id].label if item.rubric_point_id in rubric_points else None,
                "max_score": _score_text(rubric_points[item.rubric_point_id].max_score) if item.rubric_point_id in rubric_points else None,
                "suggested_score": _score_text(item.suggested_score),
                "teacher_score": _score_text(item.teacher_score),
                "evidence": item.evidence,
                "deduction_reason": item.deduction_reason,
            }
            for item in points
        ],
        "anomalies": [_review_anomaly_payload(item) for item in anomalies],
        "created_at": result.created_at,
        "updated_at": result.updated_at,
    }


def _review_queue_result_statuses(status: str) -> tuple[str, ...] | None:
    """映射复核队列筛选；待处理列表也要保留失败和正在重跑的结果。"""
    if status == "all":
        return None
    if status == "pending_review":
        return ("pending_review", "stale", "failed")
    return (status,)


def _validate_independent_rerun_prerequisites(db: Session, user: User, task: GradingTask) -> None:
    """只校验当前学生当前题的重跑条件，不因批次内其他题目失败而阻断。"""
    question = db.get(BatchQuestion, task.question_id)
    if question is None:
        raise ApiError(422, "QUESTION_NOT_FOUND", "当前评分任务关联的题目不存在，无法单独重跑。")
    if question.question_type != "subjective":
        return

    model = get_model_config(db, user)
    if not model.model_name or not model.base_url:
        raise ApiError(422, "MODEL_CONFIG_INCOMPLETE", "单独重跑这道主观题前，请先配置模型、服务商和 Base URL。")
    if not resolve_api_key(model.api_key_ref, user.id):
        raise ApiError(422, "MODEL_KEY_UNAVAILABLE", "单独重跑这道主观题需要可用的模型 API Key；请先到模型配置中保存 Key。")


def _review_change_requires_reason(result: GradingResult, payload: ReviewItemRequest, *, confirm: bool) -> bool:
    # Reconfirming an unchanged result is a no-op; reopening a reviewed result
    # or changing its grading content must leave an audit reason.
    if result.result_status == "reviewed" and not confirm:
        return True
    if payload.teacher_score is not None:
        current = result.teacher_score if result.teacher_score is not None else result.suggested_score
        if current != payload.teacher_score:
            return True
    if "teacher_comment" in payload.model_fields_set and payload.teacher_comment != result.teacher_comment:
        return True
    return bool(payload.point_scores or payload.anomaly_resolution)


def _validate_review_request(
    db: Session, result: GradingResult, question: BatchQuestion | None, payload: ReviewItemRequest, *, confirm: bool
) -> Decimal:
    if result.version != payload.version:
        raise ApiError(
            409,
            "RESOURCE_VERSION_CONFLICT",
            "评分结果已被其他操作更新，请刷新复核详情后重试。",
            [{"current_version": result.version, "received_version": payload.version}],
        )
    if _review_change_requires_reason(result, payload, confirm=confirm) and not payload.reason:
        raise ApiError(422, "REVIEW_REASON_REQUIRED", "修改分数、评语、异常处理或撤回复核状态时必须填写修改原因。")
    if question is None:
        raise ApiError(422, "QUESTION_NOT_FOUND", "评分结果关联的题目不存在，无法复核。")
    target_score = payload.teacher_score
    if target_score is None:
        target_score = result.teacher_score if result.teacher_score is not None else result.suggested_score
    if target_score is None:
        raise ApiError(422, "TEACHER_SCORE_REQUIRED", "当前结果没有可确认的分数，请填写教师确认分数。")
    max_score = question.max_score
    if target_score < 0 or target_score > max_score:
        raise ApiError(422, "SCORE_OUT_OF_RANGE", "教师分数必须在题目分值范围内。", [{"max_score": _score_text(max_score)}])

    if payload.point_scores:
        point_rows = list(
            db.scalars(
                select(RubricPoint).where(
                    RubricPoint.rubric_version_id == question.rubric_version_id,
                    RubricPoint.id.in_([item.point_id for item in payload.point_scores]),
                )
            )
        )
        points_by_id = {item.id: item for item in point_rows}
        if not question.rubric_version_id or len(points_by_id) != len(payload.point_scores):
            raise ApiError(422, "REVIEW_POINTS_INVALID", "提交的评分点不属于当前题目绑定的评分标准。")
        point_sum = Decimal("0")
        for item in payload.point_scores:
            point = points_by_id[item.point_id]
            if item.teacher_score > point.max_score:
                raise ApiError(422, "POINT_SCORE_OUT_OF_RANGE", "评分点分数超过该评分点上限。", [{"point_id": point.id}])
            point_sum += item.teacher_score
        if point_sum != target_score:
            raise ApiError(422, "POINT_SCORE_SUM_MISMATCH", "评分点教师分数合计必须等于教师确认总分。")
    return target_score


def _apply_anomaly_resolution(
    db: Session, result: GradingResult, run: GradingRun, resolution: str | None
) -> None:
    if not resolution:
        return
    status = "ignored" if resolution == "ignored" else "confirmed" if resolution == "confirmed" else "resolved"
    now = utc_now()
    anomalies = list(
        db.scalars(
            select(Anomaly).where(
                Anomaly.batch_id == run.batch_id,
                Anomaly.assignment_group_id == result.assignment_group_id,
                Anomaly.question_id == result.question_id,
                Anomaly.status == "open",
            )
        )
    )
    for anomaly in anomalies:
        anomaly.status = status
        anomaly.resolution = resolution
        anomaly.resolved_at = now


def _maybe_finalize_batch(db: Session, batch: AssignmentBatch) -> None:
    latest_run = db.scalar(select(GradingRun).where(GradingRun.batch_id == batch.id).order_by(GradingRun.created_at.desc()).limit(1))
    if latest_run is None:
        return
    tasks = list(db.scalars(select(GradingTask).where(GradingTask.grading_run_id == latest_run.id)))
    results = [
        db.scalar(
            select(GradingResult)
            .where(GradingResult.grading_task_id == task.id)
            .order_by(GradingResult.updated_at.desc(), GradingResult.created_at.desc())
            .limit(1)
        )
        for task in tasks
    ]
    if tasks and len(results) == len(tasks) and all(item is not None and item.result_status == "reviewed" for item in results):
        latest_run.status = "succeeded"
        batch.status = "reviewed"
    else:
        batch.status = "pending_review"
    batch.updated_at = utc_now()
    batch.version += 1


def _save_review_item(
    db: Session,
    user: User,
    result: GradingResult,
    task: GradingTask,
    run: GradingRun,
    batch: AssignmentBatch,
    payload: ReviewItemRequest,
    *,
    confirm: bool,
) -> dict[str, Any]:
    question = db.get(BatchQuestion, result.question_id)
    target_score = _validate_review_request(db, result, question, payload, confirm=confirm)
    before = _review_item_payload(db, result, task, run)
    if payload.teacher_score is not None:
        result.teacher_score = payload.teacher_score
    elif result.teacher_score is None:
        result.teacher_score = target_score
    if "teacher_comment" in payload.model_fields_set:
        result.teacher_comment = payload.teacher_comment
    if payload.point_scores:
        point_scores = {item.point_id: item.teacher_score for item in payload.point_scores}
        point_rows = list(db.scalars(select(GradingPointResult).where(GradingPointResult.grading_result_id == result.id)))
        for point in point_rows:
            if point.rubric_point_id in point_scores:
                point.teacher_score = point_scores[point.rubric_point_id]
    _apply_anomaly_resolution(db, result, run, payload.anomaly_resolution)
    result.result_status = "reviewed" if confirm else "pending_review"
    result.version += 1
    result.updated_at = utc_now()
    _write_review_record(
        db,
        user.id,
        "grading_result",
        result.id,
        "grading_result_confirmed" if confirm else "grading_result_saved",
        {"version": before["version"], "result_status": before["result_status"], "teacher_score": before["teacher_score"], "teacher_comment": before["teacher_comment"]},
        {"version": result.version, "result_status": result.result_status, "teacher_score": _score_text(result.teacher_score), "teacher_comment": result.teacher_comment},
        payload.reason,
    )
    _maybe_finalize_batch(db, batch)
    db.commit()
    return _review_item_payload(db, result, task, run)


@router.get("/batches/{batch_id}/review-queue")
def list_review_queue(
    batch_id: str,
    request: Request,
    status: str = Query(default="pending_review"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    query = (
        select(GradingResult, GradingTask, GradingRun)
        .join(GradingTask, GradingTask.id == GradingResult.grading_task_id)
        .join(GradingRun, GradingRun.id == GradingTask.grading_run_id)
        .where(GradingRun.batch_id == batch.id)
    )
    status_values = _review_queue_result_statuses(status)
    if status_values is not None:
        query = query.where(GradingResult.result_status.in_(status_values))
    rows = list(db.execute(query.order_by(GradingResult.updated_at, GradingResult.id)).all())
    total = len(rows)
    start = (page - 1) * page_size
    items = [_review_item_payload(db, result, task, run) for result, task, run in rows[start : start + page_size]]
    items.sort(key=lambda item: (0 if any(anomaly["severity"] == "blocking" for anomaly in item["anomalies"]) else 1, item["updated_at"]))
    return success_payload(request, items, meta={"page": page, "page_size": page_size, "total": total})


@router.get("/review-items/{item_id}")
def get_review_item(
    item_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result, task, run, _batch = _owned_review_item(db, user, item_id)
    return success_payload(request, _review_item_payload(db, result, task, run))


@router.patch("/review-items/{item_id}")
def save_review_item(
    item_id: str,
    payload: ReviewItemRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result, task, run, batch = _owned_review_item(db, user, item_id)
    data = _save_review_item(db, user, result, task, run, batch, payload, confirm=False)
    return success_payload(request, data)


@router.post("/review-items/{item_id}/confirm")
def confirm_review_item(
    item_id: str,
    payload: ReviewItemRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result, task, run, batch = _owned_review_item(db, user, item_id)
    data = _save_review_item(db, user, result, task, run, batch, payload, confirm=True)
    return success_payload(request, data)


@router.post("/review-items/{item_id}/rerun", status_code=202)
def rerun_review_item(
    item_id: str,
    payload: ReviewItemRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result, task, run, batch = _owned_review_item(db, user, item_id)
    if result.version != payload.version:
        raise ApiError(409, "RESOURCE_VERSION_CONFLICT", "评分结果已被更新，请刷新后重试。")
    if result.result_status == "reviewed" and not payload.reason:
        raise ApiError(422, "REVIEW_REASON_REQUIRED", "已复核结果重跑前必须填写原因。")
    _validate_independent_rerun_prerequisites(db, user, task)
    before = {"result_status": result.result_status, "version": result.version}
    result.result_status = "stale"
    result.version += 1
    result.updated_at = utc_now()
    _queue_existing_task(db, task, run, batch, reset_retry=True)
    _write_review_record(db, user.id, "grading_result", result.id, "grading_result_rerun", before, {"result_status": "stale", "version": result.version}, payload.reason)
    db.commit()
    return success_payload(request, {"task": _task_payload(db, task), "execution": "queued_for_worker"})


@router.get("/review-items/{item_id}/history")
def get_review_item_history(
    item_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result, _task, _run, _batch = _owned_review_item(db, user, item_id)
    records = list(
        db.scalars(
            select(ReviewRecord)
            .where(ReviewRecord.entity_type == "grading_result", ReviewRecord.entity_id == result.id)
            .order_by(ReviewRecord.created_at, ReviewRecord.id)
        )
    )
    return success_payload(
        request,
        {
            "item_id": result.id,
            "records": [
                {
                    "id": item.id,
                    "action": item.action,
                    "before": _parse_json(item.before_json, None),
                    "after": _parse_json(item.after_json, None),
                    "reason": item.reason,
                    "created_at": item.created_at,
                }
                for item in records
            ],
        },
    )


@router.get("/grading-results/{result_id}")
def get_grading_result(
    result_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(GradingResult, GradingTask, GradingRun, AssignmentBatch)
        .join(GradingTask, GradingTask.id == GradingResult.grading_task_id)
        .join(GradingRun, GradingRun.id == GradingTask.grading_run_id)
        .join(AssignmentBatch, AssignmentBatch.id == GradingRun.batch_id)
        .where(GradingResult.id == result_id, AssignmentBatch.owner_user_id == user.id)
    ).first()
    if not row:
        raise ApiError(404, "GRADING_RESULT_NOT_FOUND", "评分结果不存在或当前账号无权访问。")
    result, task, run, batch = row
    points = list(db.scalars(select(GradingPointResult).where(GradingPointResult.grading_result_id == result.id).order_by(GradingPointResult.created_at)))
    return success_payload(
        request,
        {
            "id": result.id,
            "version": result.version,
            "batch_id": batch.id,
            "grading_run_id": run.id,
            "grading_task_id": task.id,
            "assignment_group_id": result.assignment_group_id,
            "question_id": result.question_id,
            "answer_version_id": result.answer_version_id,
            "rubric_version_id": result.rubric_version_id,
            "result_status": result.result_status,
            "suggested_score": _score_text(result.suggested_score),
            "teacher_score": _score_text(result.teacher_score),
            "ai_comment": result.ai_comment,
            "teacher_comment": result.teacher_comment,
            "evidence": _parse_json(result.evidence_json, None),
            "deduction": _parse_json(result.deduction_json, None),
            "model_name": result.model_name,
            "prompt_version": result.prompt_version,
            "rule_version": result.rule_version,
            "input_hash": result.input_hash,
            "created_at": result.created_at,
            "updated_at": result.updated_at,
            "points": [
                {
                    "id": item.id,
                    "rubric_point_id": item.rubric_point_id,
                    "suggested_score": _score_text(item.suggested_score),
                    "teacher_score": _score_text(item.teacher_score),
                    "evidence": item.evidence,
                    "deduction_reason": item.deduction_reason,
                    "created_at": item.created_at,
                }
                for item in points
            ],
        },
    )
