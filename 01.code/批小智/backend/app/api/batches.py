"""作业批次及题目配置接口。"""

from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.errors import ApiError, success_payload
from app.core.utils import new_id, utc_now
from app.db.session import get_db
from app.models import (
    AssignmentGroup,
    AssignmentBatch,
    BatchQuestion,
    ClassRoom,
    GradingResult,
    GradingRun,
    GradingTask,
    OcrBlock,
    QuestionLayout,
    ReviewRecord,
    Rubric,
    RubricVersion,
    SourceFile,
    SourcePage,
    Student,
    User,
)
from app.question_layout_validation import question_layout_region_issues
from app.schemas import BatchCreateRequest, BatchPatchRequest, BatchQuestionsRequest, QuestionLayoutRequest, QuestionPromptRequest

router = APIRouter(prefix="/batches", tags=["batches"])


def score_text(value: Decimal | int | float | None) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


def question_payload(question: BatchQuestion) -> dict:
    return {
        "id": question.id,
        "question_no": question.question_no,
        "question_type": question.question_type,
        "max_score": score_text(question.max_score),
        "question_prompt": question.question_prompt,
        "reference_answer": question.reference_answer,
        "rubric_version_id": question.rubric_version_id,
        "sort_order": question.sort_order,
        "version": question.version,
    }


def batch_payload(batch: AssignmentBatch, questions: list[BatchQuestion], class_name: str | None = None) -> dict:
    return {
        "id": batch.id,
        "title": batch.title,
        "class_id": batch.class_id,
        "class_name": class_name,
        "subject": batch.subject,
        "total_score": score_text(batch.total_score),
        "status": batch.status,
        "current_revision": batch.current_revision,
        "version": batch.version,
        "questions": [question_payload(item) for item in questions],
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
        "archived_at": batch.archived_at,
    }


def question_layout_payload(layout: QuestionLayout | None) -> dict | None:
    if layout is None:
        return None
    try:
        source_pages = json.loads(layout.source_pages_json)
    except (TypeError, json.JSONDecodeError):
        source_pages = []
    try:
        regions = json.loads(layout.regions_json)
    except (TypeError, json.JSONDecodeError):
        regions = []
    return {
        "id": layout.id,
        "batch_id": layout.batch_id,
        "source_page_id": layout.source_page_id,
        "status": layout.status,
        "version": layout.version,
        "source_pages": source_pages,
        "regions": regions,
        "confirmed_by": layout.confirmed_by,
        "confirmed_at": layout.confirmed_at,
        "created_at": layout.created_at,
        "updated_at": layout.updated_at,
    }


def get_owned_batch(db: Session, user_id: str, batch_id: str) -> AssignmentBatch:
    batch = db.scalar(select(AssignmentBatch).where(AssignmentBatch.id == batch_id, AssignmentBatch.owner_user_id == user_id))
    if not batch:
        raise ApiError(404, "BATCH_NOT_FOUND", "作业批次不存在或当前账号无权访问。")
    return batch


def get_owned_class(db: Session, user_id: str, class_id: str, *, require_active: bool = True) -> ClassRoom:
    room = db.scalar(select(ClassRoom).where(ClassRoom.id == class_id, ClassRoom.owner_user_id == user_id))
    if not room:
        raise ApiError(404, "CLASS_NOT_FOUND", "班级不存在或当前账号无权访问。")
    if require_active and room.status != "active":
        raise ApiError(422, "CLASS_INACTIVE", "停用班级不能用于新建或修改批次。")
    return room


def validate_questions(total_score: Decimal, questions: list) -> None:
    question_nos = [item.question_no for item in questions]
    if len(question_nos) != len(set(question_nos)):
        raise ApiError(422, "DUPLICATE_QUESTION_NO", "题号必须唯一，请修正后重试。")
    actual = sum((Decimal(item.max_score) for item in questions), Decimal("0"))
    if actual != Decimal(total_score):
        raise ApiError(422, "BATCH_SCORE_MISMATCH", f"题目分值合计为 {actual:.2f}，与批次总分 {Decimal(total_score):.2f} 不一致。")


def validate_rubric_ids(db: Session, user_id: str, questions: list) -> None:
    rubric_ids = {item.rubric_version_id for item in questions if item.rubric_version_id}
    if not rubric_ids:
        return
    found = set(
        db.scalars(
            select(RubricVersion.id).where(RubricVersion.id.in_(rubric_ids), RubricVersion.created_by == user_id)
        )
    )
    if found != rubric_ids:
        raise ApiError(422, "RUBRIC_NOT_FOUND", "存在当前账号不可用的评分标准版本，请重新选择评分标准。")


def make_questions(batch_id: str, questions: list, now: str) -> list[BatchQuestion]:
    return [
        BatchQuestion(
            id=new_id("question"), batch_id=batch_id, question_no=item.question_no, question_type=item.question_type,
            max_score=item.max_score, question_prompt=item.question_prompt, reference_answer=item.reference_answer, objective_rule_json=None,
            rubric_version_id=item.rubric_version_id, sort_order=index, version=1, created_at=now, updated_at=now
        )
        for index, item in enumerate(questions, start=1)
    ]


def batch_validation(db: Session, batch: AssignmentBatch, questions: list[BatchQuestion], room: ClassRoom) -> dict:
    errors = []
    if room.status != "active":
        errors.append({"code": "CLASS_INACTIVE", "message": "班级已停用，不能继续处理该批次。"})
    if not questions:
        errors.append({"code": "QUESTIONS_REQUIRED", "message": "至少需要配置一道题目。"})
    actual = sum((Decimal(item.max_score) for item in questions), Decimal("0"))
    if actual != Decimal(batch.total_score):
        errors.append({"code": "BATCH_SCORE_MISMATCH", "message": "题目分值合计与批次总分不一致。"})
    missing_rubric = [item.question_no for item in questions if item.question_type != "objective" and not item.rubric_version_id]
    if missing_rubric:
        errors.append({"code": "RUBRIC_REQUIRED", "message": f"主观题缺少评分标准：{', '.join(missing_rubric)}。"})
    has_files = db.scalar(
        select(SourceFile.id).where(SourceFile.batch_id == batch.id, SourceFile.file_role == "student_work").limit(1)
    ) is not None
    missing_prompts = [
        item.question_no
        for item in questions
        if item.question_type == "subjective" and not (item.question_prompt or "").strip()
    ]
    grading_errors = list(errors)
    if missing_prompts:
        grading_errors.append(
            {
                "code": "SUBJECTIVE_PROMPT_REQUIRED",
                "message": f"主观题缺少试卷题干：{', '.join(missing_prompts)}。",
            }
        )
    return {
        "valid_for_upload": len(errors) == 0,
        "valid_for_grading": len(grading_errors) == 0 and has_files,
        "errors": errors,
        "grading_errors": grading_errors,
        "has_files": has_files,
        "has_question_paper": db.scalar(
            select(SourceFile.id).where(SourceFile.batch_id == batch.id, SourceFile.file_role == "question_paper").limit(1)
        )
        is not None,
    }


@router.get("")
def list_batches(
    request: Request,
    status: str | None = Query(default=None),
    class_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filters = [AssignmentBatch.owner_user_id == user.id]
    if status:
        filters.append(AssignmentBatch.status == status)
    if class_id:
        filters.append(AssignmentBatch.class_id == class_id)

    total = db.scalar(select(func.count(AssignmentBatch.id)).where(*filters)) or 0
    rows = list(
        db.execute(
            select(AssignmentBatch, ClassRoom.name)
            .outerjoin(ClassRoom, ClassRoom.id == AssignmentBatch.class_id)
            .where(*filters)
            .order_by(AssignmentBatch.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    batch_ids = [batch.id for batch, _class_name in rows]
    questions_by_batch: dict[str, list[BatchQuestion]] = {batch_id: [] for batch_id in batch_ids}
    if batch_ids:
        for question in db.scalars(
            select(BatchQuestion)
            .where(BatchQuestion.batch_id.in_(batch_ids))
            .order_by(BatchQuestion.batch_id, BatchQuestion.sort_order)
        ):
            questions_by_batch[question.batch_id].append(question)
    data = [
        batch_payload(batch, questions_by_batch[batch.id], class_name)
        for batch, class_name in rows
    ]
    return success_payload(request, data, meta={"page": page, "page_size": page_size, "total": total})


@router.get("/summary")
def batch_summary(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return dashboard aggregates without one grading request per batch."""

    task_rows = list(
        db.execute(
            select(
                GradingTask,
                AssignmentBatch.id,
                AssignmentBatch.status,
                BatchQuestion.question_no,
                BatchQuestion.question_type,
                Student.student_code,
                Student.display_name,
            )
            .join(GradingRun, GradingRun.id == GradingTask.grading_run_id)
            .join(AssignmentBatch, AssignmentBatch.id == GradingRun.batch_id)
            .join(BatchQuestion, BatchQuestion.id == GradingTask.question_id)
            .join(AssignmentGroup, AssignmentGroup.id == GradingTask.assignment_group_id)
            .outerjoin(Student, Student.id == AssignmentGroup.student_id)
            .where(AssignmentBatch.owner_user_id == user.id)
            .order_by(GradingTask.updated_at.desc(), GradingTask.id.desc())
        )
    )
    task_ids = [row[0].id for row in task_rows]
    latest_results: dict[str, str | None] = {}
    if task_ids:
        for result in db.scalars(
            select(GradingResult)
            .where(GradingResult.grading_task_id.in_(task_ids))
            .order_by(GradingResult.updated_at.desc(), GradingResult.created_at.desc())
        ):
            latest_results.setdefault(result.grading_task_id, result.result_status)

    pending = 0
    processing = 0
    reviewed = 0
    task_items = []
    for task, batch_id, batch_status, question_no, question_type, student_code, student_name in task_rows:
        result_status = latest_results.get(task.id)
        if result_status == "pending_review":
            pending += 1
        if task.status in {"queued", "running"}:
            processing += 1
        if result_status == "reviewed":
            reviewed += 1
        if result_status != "reviewed" or task.status == "failed":
            task_items.append(
                {
                    "id": task.id,
                    "student": student_name or student_code or "未归属学生",
                    "question": f"第 {question_no or '—'} 题 · {'主观题' if question_type == 'subjective' else '客观题'}",
                    "status": "批改失败，请查看任务详情" if task.status == "failed" else "等待教师复核" if result_status == "pending_review" else "正在处理" if task.status == "running" else "等待处理",
                    "tone": "exception" if task.status == "failed" else "review" if result_status == "pending_review" else "pending",
                    "batch_id": batch_id,
                    "batch_status": batch_status,
                }
            )

    active_rubrics = db.scalar(
        select(func.count(Rubric.id)).where(Rubric.owner_user_id == user.id, Rubric.status == "active")
    ) or 0
    return success_payload(
        request,
        {
            "pending": pending,
            "processing": processing,
            "reviewed": reviewed,
            "rubrics": active_rubrics,
            "tasks": task_items[:3],
        },
    )


@router.post("")
def create_batch(payload: BatchCreateRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    room = get_owned_class(db, user.id, payload.class_id)
    validate_questions(payload.total_score, payload.questions)
    validate_rubric_ids(db, user.id, payload.questions)
    now = utc_now()
    batch = AssignmentBatch(
        id=new_id("batch"), owner_user_id=user.id, class_id=room.id, title=payload.title, subject=payload.subject,
        total_score=payload.total_score, status="draft", current_revision=1, version=1, created_at=now, updated_at=now, archived_at=None
    )
    questions = make_questions(batch.id, payload.questions, now)
    db.add(batch)
    db.flush()
    db.add_all(questions)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(422, "BATCH_CREATE_FAILED", "批次或题目配置不符合约束，请检查后重试。") from exc
    result = batch_payload(batch, questions, room.name)
    result["next_action"] = "upload" if payload.save_mode == "upload" else "edit"
    return success_payload(request, result)


@router.get("/{batch_id}")
def get_batch(batch_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = get_owned_batch(db, user.id, batch_id)
    questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id).order_by(BatchQuestion.sort_order)))
    room = db.get(ClassRoom, batch.class_id)
    return success_payload(request, batch_payload(batch, questions, room.name if room else None))


@router.patch("/{batch_id}")
def update_batch(
    batch_id: str,
    payload: BatchPatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    if batch.version != payload.version:
        raise ApiError(409, "RESOURCE_VERSION_CONFLICT", "批次已被其他请求修改，请刷新后再提交。", [{"expected": payload.version, "actual": batch.version}])
    if payload.class_id and payload.class_id != batch.class_id:
        room = get_owned_class(db, user.id, payload.class_id)
        batch.class_id = room.id
    if payload.title is not None:
        batch.title = payload.title
    if payload.subject is not None:
        batch.subject = payload.subject
    if payload.total_score is not None:
        questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id)))
        actual = sum((Decimal(item.max_score) for item in questions), Decimal("0"))
        if actual != Decimal(payload.total_score):
            raise ApiError(422, "BATCH_SCORE_MISMATCH", "新总分必须与当前题目分值合计一致。")
        batch.total_score = payload.total_score
    batch.version += 1
    batch.updated_at = utc_now()
    db.commit()
    questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id).order_by(BatchQuestion.sort_order)))
    room = db.get(ClassRoom, batch.class_id)
    return success_payload(request, batch_payload(batch, questions, room.name if room else None))


@router.delete("/{batch_id}")
def delete_batch(batch_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = get_owned_batch(db, user.id, batch_id)
    has_files = db.scalar(select(SourceFile.id).where(SourceFile.batch_id == batch.id).limit(1)) is not None
    if batch.status != "draft" or has_files:
        raise ApiError(409, "BATCH_NOT_DELETABLE", "只有未上传文件的草稿批次可以删除。")
    # Batch questions have a required foreign key and the SQLite schema does
    # not define ON DELETE CASCADE. Remove only the draft's own configuration
    # after the safety checks above; uploaded/OCR/grading history is never
    # cascaded by this endpoint.
    db.query(BatchQuestion).filter(BatchQuestion.batch_id == batch.id).delete(synchronize_session=False)
    db.delete(batch)
    db.commit()
    return success_payload(request, {"deleted": True, "id": batch_id})


@router.post("/{batch_id}/validate")
def validate_batch(batch_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = get_owned_batch(db, user.id, batch_id)
    room = get_owned_class(db, user.id, batch.class_id, require_active=False)
    questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id).order_by(BatchQuestion.sort_order)))
    validation = batch_validation(db, batch, questions, room)

    # 页面上的“前置条件”必须和真正创建批改任务时使用同一套严格校验。
    # 之前这里仅检查批次配置和是否存在作业文件，而
    # POST /grading-runs 还会检查学生答案覆盖、页序确认、评分标准以及
    # 主观题模型 Key，导致页面同时显示“已满足”和“不能发起批改”。
    # 使用局部导入避免 batches 与 grading 路由模块的循环导入。
    from app.api.grading import _validate_scope_and_inputs
    from app.schemas import GradingScopeRequest

    _groups, _questions, _answers, grading_errors = _validate_scope_and_inputs(
        db, user, batch, GradingScopeRequest()
    )
    validation["grading_errors"] = grading_errors
    validation["valid_for_grading"] = not grading_errors
    return success_payload(request, validation)


@router.put("/{batch_id}/questions")
def replace_questions(
    batch_id: str,
    payload: BatchQuestionsRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    if batch.version != payload.version:
        raise ApiError(409, "RESOURCE_VERSION_CONFLICT", "批次已被其他请求修改，请刷新后再提交。", [{"expected": payload.version, "actual": batch.version}])
    validate_questions(batch.total_score, payload.questions)
    validate_rubric_ids(db, user.id, payload.questions)
    # 题干/答案文件是生成题目草稿的输入，上传它不能阻止后续写入识别出的题号和题型。
    # 学生作业一旦上传，题目 ID 会与答案和批改任务产生关联，不能再删除重建题目。
    has_student_files = db.scalar(
        select(SourceFile.id).where(SourceFile.batch_id == batch.id, SourceFile.file_role == "student_work").limit(1)
    ) is not None
    if has_student_files:
        existing_questions = list(
            db.scalars(
                select(BatchQuestion)
                .where(BatchQuestion.batch_id == batch.id)
                .order_by(BatchQuestion.sort_order, BatchQuestion.question_no)
            )
        )
        same_structure = len(existing_questions) == len(payload.questions) and all(
            current.question_no == incoming.question_no
            and current.question_type == incoming.question_type
            and Decimal(current.max_score) == Decimal(incoming.max_score)
            and current.rubric_version_id == incoming.rubric_version_id
            for current, incoming in zip(existing_questions, payload.questions)
        )
        same_structure_by_order = payload.preserve_existing_structure and len(existing_questions) == len(payload.questions) and all(
            current.question_type == incoming.question_type
            and Decimal(current.max_score) == Decimal(incoming.max_score)
            and current.rubric_version_id == incoming.rubric_version_id
            for current, incoming in zip(existing_questions, payload.questions)
        )
        if not same_structure and not same_structure_by_order:
            raise ApiError(
                409,
                "BATCH_CONFIG_LOCKED",
                "批次已有学生作业，不能增删题目或改变题号、题型、分值和评分标准；如需调整题目结构，请新建批次。",
            )
        # 结构不变或教师显式选择按顺序映射时保留原题目 ID，只更新 OCR 回填的题干和参考答案，
        # 这样可以安全地重新 OCR，而不会让已有学生答案失去题目关联。
        now = utc_now()
        for current, incoming in zip(existing_questions, payload.questions):
            content_changed = (
                current.question_prompt != incoming.question_prompt
                or current.reference_answer != incoming.reference_answer
            )
            current.question_prompt = incoming.question_prompt
            current.reference_answer = incoming.reference_answer
            if content_changed:
                current.version += 1
            current.updated_at = now
        batch.current_revision += 1
        batch.version += 1
        batch.updated_at = now
        db.commit()
        return success_payload(request, batch_payload(batch, existing_questions))
    db.query(QuestionLayout).filter(QuestionLayout.batch_id == batch.id).delete(synchronize_session=False)
    db.query(BatchQuestion).filter(BatchQuestion.batch_id == batch.id).delete(synchronize_session=False)
    now = utc_now()
    questions = make_questions(batch.id, payload.questions, now)
    batch.current_revision += 1
    batch.version += 1
    batch.updated_at = now
    db.add_all(questions)
    db.commit()
    return success_payload(request, batch_payload(batch, questions))


@router.put("/{batch_id}/questions/{question_id}/prompt")
def update_question_prompt(
    batch_id: str,
    question_id: str,
    payload: QuestionPromptRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """保存从已上传试卷 OCR 页面确认的题目文本与批次共享参考答案快照。"""

    batch = get_owned_batch(db, user.id, batch_id)
    if batch.status in {"archived", "grading", "reviewed"}:
        raise ApiError(409, "BATCH_PROMPT_LOCKED", "当前批次已进入批改或复核阶段，不能直接修改题干。")
    question = db.scalar(select(BatchQuestion).where(BatchQuestion.id == question_id, BatchQuestion.batch_id == batch.id))
    if not question:
        raise ApiError(404, "QUESTION_NOT_FOUND", "题目不存在或不属于当前批次。")
    if question.version != payload.version:
        raise ApiError(
            409,
            "RESOURCE_VERSION_CONFLICT",
            "题目配置已被其他请求修改，请刷新后再提交。",
            [{"expected": payload.version, "actual": question.version}],
        )

    page = None
    if payload.source_page_id:
        page = db.scalar(
            select(SourcePage)
            .join(SourceFile, SourcePage.source_file_id == SourceFile.id)
            .where(
                SourcePage.id == payload.source_page_id,
                SourcePage.batch_id == batch.id,
                SourceFile.file_role == "question_paper",
            )
        )
        if not page:
            raise ApiError(422, "QUESTION_PAPER_PAGE_INVALID", "来源页面不是当前批次的题目页面。")

    block_ids = list(dict.fromkeys(payload.ocr_block_ids))
    blocks = []
    if block_ids and not page:
        raise ApiError(422, "OCR_PAGE_REQUIRED_FOR_BLOCKS", "绑定 OCR 文本块时必须提供已完成 OCR 的试卷页面。")
    if block_ids and page.status != "ocr_ready":
        raise ApiError(422, "OCR_PAGE_REQUIRED_FOR_BLOCKS", "当前题目页面尚未完成 OCR，不能绑定 OCR 文本块；可以先保存手动录入的题干。")
    if block_ids and page:
        blocks = list(
            db.scalars(
                select(OcrBlock).where(
                    OcrBlock.id.in_(block_ids),
                    OcrBlock.source_page_id == page.id,
                    OcrBlock.question_id.is_(None) | (OcrBlock.question_id == question.id),
                )
            )
        )
        if {item.id for item in blocks} != set(block_ids):
            raise ApiError(422, "OCR_BLOCK_INVALID", "所选 OCR 文本块不存在、不是当前试卷页面或已绑定其他题目。")

    question_prompt = payload.question_prompt
    reference_answer = payload.reference_answer
    if question.question_type == "subjective" and not (question_prompt or question.question_prompt):
        raise ApiError(422, "QUESTION_PROMPT_REQUIRED", "主观题需要填写题目文本。")

    now = utc_now()
    before = {
        "question_prompt": question.question_prompt,
        "reference_answer": question.reference_answer,
        "version": question.version,
    }
    if blocks:
        db.query(OcrBlock).filter(OcrBlock.source_page_id == page.id, OcrBlock.question_id == question.id).update(
            {OcrBlock.question_id: None}, synchronize_session=False
        )
        for block in blocks:
            block.question_id = question.id
    if "question_prompt" in payload.model_fields_set:
        question.question_prompt = question_prompt
    if "reference_answer" in payload.model_fields_set:
        question.reference_answer = reference_answer
    question.version += 1
    question.updated_at = now
    batch.current_revision += 1
    batch.version += 1
    batch.updated_at = now
    db.add(
        ReviewRecord(
            id=new_id("review"),
            user_id=user.id,
            entity_type="question_prompt",
            entity_id=question.id,
            action="question_content_updated",
            before_json=json.dumps(before, ensure_ascii=False, sort_keys=True),
            after_json=json.dumps(
                {
                    "question_prompt": question.question_prompt,
                    "reference_answer": question.reference_answer,
                    "source_page_id": page.id if page else None,
                    "ocr_block_ids": block_ids,
                    "version": question.version,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            reason="确认上传试卷 OCR 题干",
            created_at=now,
        )
    )
    db.commit()
    questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id).order_by(BatchQuestion.sort_order)))
    room = db.get(ClassRoom, batch.class_id)
    return success_payload(request, batch_payload(batch, questions, room.name if room else None))


@router.get("/{batch_id}/question-layout")
def get_question_layout(
    batch_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_owned_batch(db, user.id, batch_id)
    layout = db.scalar(select(QuestionLayout).where(QuestionLayout.batch_id == batch.id))
    return success_payload(request, question_layout_payload(layout))


@router.put("/{batch_id}/question-layout")
def save_question_layout(
    batch_id: str,
    payload: QuestionLayoutRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """确认题目页自动推导的区域模板，供同批次学生页复用。"""

    batch = get_owned_batch(db, user.id, batch_id)
    if batch.status in {"archived", "grading", "reviewed"}:
        raise ApiError(409, "QUESTION_LAYOUT_LOCKED", "当前批次已进入批改或复核阶段，不能修改题目版式模板。")
    questions = list(db.scalars(select(BatchQuestion).where(BatchQuestion.batch_id == batch.id).order_by(BatchQuestion.sort_order)))
    question_by_id = {question.id: question for question in questions}
    if not questions:
        raise ApiError(409, "QUESTION_LAYOUT_QUESTIONS_REQUIRED", "请先完成批次题目配置，再确认题目版式。")

    requested_page_ids = {item.source_page_id for item in payload.source_pages}
    pages = list(
        db.scalars(
            select(SourcePage)
            .join(SourceFile, SourcePage.source_file_id == SourceFile.id)
            .where(
                SourcePage.batch_id == batch.id,
                SourcePage.id.in_(requested_page_ids),
                SourceFile.file_role == "question_paper",
            )
        )
    )
    if {page.id for page in pages} != requested_page_ids:
        raise ApiError(422, "QUESTION_LAYOUT_PAGE_INVALID", "题目版式只能使用当前批次已上传的题目页。")
    if payload.source_page_id not in requested_page_ids:
        raise ApiError(422, "QUESTION_LAYOUT_SOURCE_PAGE_INVALID", "版式主来源页面必须属于提交的题目页。")

    page_by_id = {page.id: page for page in pages}
    for item in payload.regions:
        question = question_by_id.get(item.question_id)
        if question is None:
            raise ApiError(422, "QUESTION_LAYOUT_QUESTION_INVALID", "版式区域包含不属于当前批次的题目。", [{"question_id": item.question_id}])
        page = page_by_id.get(item.source_page_id)
        if page is None or page.page_index != item.page_index:
            raise ApiError(422, "QUESTION_LAYOUT_REGION_PAGE_INVALID", "版式区域的页面信息与题目页不一致。", [{"question_id": question.id}])
        if question.question_no != item.question_no:
            raise ApiError(422, "QUESTION_LAYOUT_QUESTION_NO_INVALID", "版式区域题号与批次题目配置不一致。", [{"question_id": question.id}])
        if item.ocr_block_ids and page.status != "ocr_ready":
            raise ApiError(422, "OCR_PAGE_REQUIRED_FOR_BLOCKS", "题目页面尚未完成 OCR，不能把 OCR 文本块绑定到版式区域；请先完成 OCR 或使用单题整页兜底模板。", [{"question_id": question.id}])

    covered_question_ids = {item.question_id for item in payload.regions}
    missing = [question.id for question in questions if question.id not in covered_question_ids]
    if missing:
        raise ApiError(422, "QUESTION_LAYOUT_INCOMPLETE", "题目页自动识别未覆盖全部题目，暂不能确认版式；请先补齐题目 OCR 或题目配置。", [{"question_id": item} for item in missing])

    overlap_issues = question_layout_region_issues([item.model_dump() for item in payload.regions])
    if overlap_issues:
        raise ApiError(
            422,
            "QUESTION_LAYOUT_REGIONS_OVERLAP",
            "不同题目的 OCR 区域重叠过多，保存会把其他题的文字归入本题。请在原图上重新框选答题区域后再保存。",
            overlap_issues,
        )

    layout = db.scalar(select(QuestionLayout).where(QuestionLayout.batch_id == batch.id))
    if layout is not None and layout.version != payload.version:
        raise ApiError(409, "RESOURCE_VERSION_CONFLICT", "题目版式已被其他请求修改，请刷新后重试。", [{"expected": payload.version, "actual": layout.version}])
    now = utc_now()
    before = question_layout_payload(layout)
    if layout is None:
        layout = QuestionLayout(
            id=new_id("layout"),
            batch_id=batch.id,
            source_page_id=payload.source_page_id,
            status="confirmed",
            version=1,
            source_pages_json=json.dumps([item.model_dump() for item in payload.source_pages], ensure_ascii=False),
            regions_json=json.dumps([item.model_dump() for item in payload.regions], ensure_ascii=False),
            confirmed_by=user.id,
            confirmed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(layout)
    else:
        layout.source_page_id = payload.source_page_id
        layout.status = "confirmed"
        layout.version += 1
        layout.source_pages_json = json.dumps([item.model_dump() for item in payload.source_pages], ensure_ascii=False)
        layout.regions_json = json.dumps([item.model_dump() for item in payload.regions], ensure_ascii=False)
        layout.confirmed_by = user.id
        layout.confirmed_at = now
        layout.updated_at = now
    batch.current_revision += 1
    batch.version += 1
    batch.updated_at = now
    db.add(
        ReviewRecord(
            id=new_id("review"),
            user_id=user.id,
            entity_type="question_layout",
            entity_id=layout.id,
            action="question_layout_confirmed",
            before_json=json.dumps(before, ensure_ascii=False) if before is not None else None,
            after_json=json.dumps(question_layout_payload(layout), ensure_ascii=False),
            reason="确认题目 OCR 自动推导的版式模板，后续学生页按题目区域识别",
            created_at=now,
        )
    )
    db.commit()
    return success_payload(request, question_layout_payload(layout))


@router.post("/{batch_id}/archive")
def archive_batch(batch_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = get_owned_batch(db, user.id, batch_id)
    if batch.status == "archived":
        return success_payload(request, {"id": batch.id, "status": batch.status, "archived_at": batch.archived_at})
    batch.status = "archived"
    batch.archived_at = utc_now()
    batch.updated_at = batch.archived_at
    batch.version += 1
    db.commit()
    return success_payload(request, {"id": batch.id, "status": batch.status, "archived_at": batch.archived_at})
