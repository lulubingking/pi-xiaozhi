"""按开发技术文档定义的首版 SQLite 业务模型。

本文件只声明表结构和约束，不在应用启动时自动创建或修改数据库表；表结构变更必须通过 Alembic 迁移完成。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


Timestamp = String(40)
Identifier = String(64)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="teacher")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    failed_login_count: Mapped[int] = mapped_column(nullable=False, default=0)
    locked_until: Mapped[str | None] = mapped_column(Timestamp)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    user_id: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    remember_me: Mapped[int] = mapped_column(nullable=False, default=0)
    expires_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(Timestamp)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    last_seen_at: Mapped[str | None] = mapped_column(Timestamp)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    user_id: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    event_name: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(Identifier, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class Task(Base):
    """跨上传、OCR、评分和导出的持久化任务记录。"""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("batch_id", "task_type", "idempotency_key", name="uq_tasks_batch_type_idempotency"),
        Index("ix_tasks_status_created", "status", "created_at"),
        Index("ix_tasks_batch_status", "batch_id", "status"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False)
    batch_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("assignment_batches.id"))
    assignment_group_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("assignment_groups.id"))
    resource_id: Mapped[str | None] = mapped_column(Identifier)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    options_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    progress_current: Mapped[int] = mapped_column(nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(nullable=False, default=3)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_items_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    started_at: Mapped[str | None] = mapped_column(Timestamp)
    finished_at: Mapped[str | None] = mapped_column(Timestamp)
    worker_id: Mapped[str | None] = mapped_column(String(120))
    heartbeat_at: Mapped[str | None] = mapped_column(Timestamp)


class ClassRoom(Base):
    __tablename__ = "classes"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", "status", name="uq_classes_owner_name_status"),)

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("class_id", "student_code", name="uq_students_class_code"),
        Index("ix_students_class_code", "class_id", "student_code"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    class_id: Mapped[str] = mapped_column(Identifier, ForeignKey("classes.id"), nullable=False)
    student_code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class AssignmentBatch(Base):
    __tablename__ = "assignment_batches"
    __table_args__ = (
        CheckConstraint("total_score > 0", name="ck_assignment_batches_total_score_positive"),
        Index("ix_batches_owner_status_updated", "owner_user_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    class_id: Mapped[str] = mapped_column(Identifier, ForeignKey("classes.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(32), nullable=False)
    total_score: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    current_revision: Mapped[int] = mapped_column(nullable=False, default=1)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    archived_at: Mapped[str | None] = mapped_column(Timestamp)


class BatchQuestion(Base):
    __tablename__ = "batch_questions"
    __table_args__ = (
        UniqueConstraint("batch_id", "question_no", name="uq_batch_questions_batch_no"),
        CheckConstraint("max_score > 0", name="ck_batch_questions_max_score_positive"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    question_no: Mapped[str] = mapped_column(String(32), nullable=False)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False)
    max_score: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    question_prompt: Mapped[str | None] = mapped_column(Text)
    reference_answer: Mapped[str | None] = mapped_column(Text)
    objective_rule_json: Mapped[str | None] = mapped_column(Text)
    rubric_version_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("rubric_versions.id"))
    sort_order: Mapped[int] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class QuestionLayout(Base):
    """批次共享的题目版式模板。

    ``regions_json`` 保存题目页 OCR 自动推导出的归一化题目区域。学生页 OCR
    只在模板确认后读取它，避免把整张照片当作一块手写答案识别。
    """

    __tablename__ = "question_layouts"
    __table_args__ = (
        UniqueConstraint("batch_id", name="uq_question_layouts_batch"),
        Index("ix_question_layouts_batch_status", "batch_id", "status"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    source_page_id: Mapped[str] = mapped_column(Identifier, ForeignKey("source_pages.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="confirmed")
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    source_pages_json: Mapped[str] = mapped_column(Text, nullable=False)
    regions_json: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(Identifier, ForeignKey("users.id"))
    confirmed_at: Mapped[str | None] = mapped_column(Timestamp)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class Rubric(Base):
    __tablename__ = "rubrics"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(32), nullable=False)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    __table_args__ = (
        UniqueConstraint("rubric_id", "version_no", name="uq_rubric_versions_no"),
        CheckConstraint("total_score > 0", name="ck_rubric_versions_total_score_positive"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    rubric_id: Mapped[str] = mapped_column(Identifier, ForeignKey("rubrics.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(nullable=False)
    total_score: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="published")
    source_version_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("rubric_versions.id"))
    created_by: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class RubricPoint(Base):
    __tablename__ = "rubric_points"
    __table_args__ = (CheckConstraint("max_score > 0", name="ck_rubric_points_max_score_positive"),)

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    rubric_version_id: Mapped[str] = mapped_column(Identifier, ForeignKey("rubric_versions.id"), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    max_score: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sort_order: Mapped[int] = mapped_column(nullable=False)


class RubricExample(Base):
    __tablename__ = "rubric_examples"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    rubric_version_id: Mapped[str] = mapped_column(Identifier, ForeignKey("rubric_versions.id"), nullable=False)
    rubric_point_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("rubric_points.id"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_source_files_size_positive"),
        Index("ix_source_files_batch_status", "batch_id", "status"),
        Index("ix_source_files_batch_sha256", "batch_id", "sha256"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_role: Mapped[str] = mapped_column(String(32), nullable=False, default="student_work")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    failure_code: Mapped[str | None] = mapped_column(String(80))
    page_count: Mapped[int | None] = mapped_column()
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class SourcePage(Base):
    __tablename__ = "source_pages"
    __table_args__ = (
        UniqueConstraint("source_file_id", "page_index", name="uq_source_pages_file_index"),
        CheckConstraint("page_index >= 1", name="ck_source_pages_page_index_positive"),
        Index("ix_source_pages_batch_status", "batch_id", "status"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    source_file_id: Mapped[str] = mapped_column(Identifier, ForeignKey("source_files.id"), nullable=False)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    page_index: Mapped[int] = mapped_column(nullable=False)
    original_storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    processed_storage_key: Mapped[str | None] = mapped_column(String(500))
    transform_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class AssignmentGroup(Base):
    __tablename__ = "assignment_groups"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    student_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("students.id"))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    grouping_confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    grouping_reason: Mapped[str | None] = mapped_column(String(500))
    confirmed_by: Mapped[str | None] = mapped_column(Identifier, ForeignKey("users.id"))
    confirmed_at: Mapped[str | None] = mapped_column(Timestamp)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class PageGrouping(Base):
    __tablename__ = "page_groupings"
    __table_args__ = (
        UniqueConstraint("source_page_id", name="uq_page_groupings_page"),
        Index("ix_page_groupings_group_sequence", "assignment_group_id", "page_sequence"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    source_page_id: Mapped[str] = mapped_column(Identifier, ForeignKey("source_pages.id"), nullable=False)
    assignment_group_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("assignment_groups.id"))
    student_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("students.id"))
    page_sequence: Mapped[int | None] = mapped_column()
    grouping_status: Mapped[str] = mapped_column(String(40), nullable=False)
    grouping_confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    grouping_reason: Mapped[str | None] = mapped_column(String(500))
    confirmed_by: Mapped[str | None] = mapped_column(Identifier, ForeignKey("users.id"))
    confirmed_at: Mapped[str | None] = mapped_column(Timestamp)
    version: Mapped[int] = mapped_column(nullable=False, default=1)


class OcrRun(Base):
    __tablename__ = "ocr_runs"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    source_page_id: Mapped[str] = mapped_column(Identifier, ForeignKey("source_pages.id"), nullable=False)
    engine_name: Mapped[str] = mapped_column(String(80), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(160), nullable=False)
    preprocess_version: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_output_storage_key: Mapped[str | None] = mapped_column(String(500))
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[str | None] = mapped_column(Timestamp)
    finished_at: Mapped[str | None] = mapped_column(Timestamp)


class OcrBlock(Base):
    __tablename__ = "ocr_blocks"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    ocr_run_id: Mapped[str] = mapped_column(Identifier, ForeignKey("ocr_runs.id"), nullable=False)
    source_page_id: Mapped[str] = mapped_column(Identifier, ForeignKey("source_pages.id"), nullable=False)
    block_index: Mapped[int] = mapped_column(nullable=False)
    text_raw: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    x1: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    y1: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    x2: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    y2: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    question_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("batch_questions.id"))
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class AnswerVersion(Base):
    __tablename__ = "answer_versions"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    assignment_group_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_groups.id"), nullable=False)
    source_page_id: Mapped[str] = mapped_column(Identifier, ForeignKey("source_pages.id"), nullable=False)
    answer_photo_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("answer_photos.id"))
    question_id: Mapped[str] = mapped_column(Identifier, ForeignKey("batch_questions.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(nullable=False)
    answer_text: Mapped[str | None] = mapped_column(Text)
    is_blank_confirmed: Mapped[int] = mapped_column(nullable=False, default=0)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    created_by: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class QuestionAnswer(Base):
    __tablename__ = "question_answers"
    __table_args__ = (UniqueConstraint("assignment_group_id", "question_id", name="uq_question_answers_group_question"),)

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    assignment_group_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_groups.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(Identifier, ForeignKey("batch_questions.id"), nullable=False)
    current_version_id: Mapped[str] = mapped_column(Identifier, ForeignKey("answer_versions.id"), nullable=False)
    answer_text_joined: Mapped[str | None] = mapped_column(Text)
    coverage_status: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class AnswerPhoto(Base):
    """主观题答案照片及其 OCR 结果。

    答案照片是题目级辅助输入，不作为新的学生作业页参与页序合并，
    但保留原图、处理图、原始 OCR 和工作流快照，便于教师复核和追溯。
    """

    __tablename__ = "answer_photos"
    __table_args__ = (
        Index("ix_answer_photos_task_status", "task_id", "status"),
        Index("ix_answer_photos_group_question", "assignment_group_id", "question_id"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    assignment_group_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_groups.id"), nullable=False)
    source_page_id: Mapped[str] = mapped_column(Identifier, ForeignKey("source_pages.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(Identifier, ForeignKey("batch_questions.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("tasks.id"))
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    processed_storage_key: Mapped[str | None] = mapped_column(String(500))
    raw_output_storage_key: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recognition_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    answer_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    workflow_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class GradingRun(Base):
    __tablename__ = "grading_runs"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    scope_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(120), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Timestamp)


class GradingTask(Base):
    __tablename__ = "grading_tasks"
    __table_args__ = (Index("ix_grading_tasks_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    grading_run_id: Mapped[str] = mapped_column(Identifier, ForeignKey("grading_runs.id"), nullable=False)
    assignment_group_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_groups.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(Identifier, ForeignKey("batch_questions.id"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retry_count: Mapped[int] = mapped_column(nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(nullable=False, default=2)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    heartbeat_at: Mapped[str | None] = mapped_column(Timestamp)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class GradingResult(Base):
    __tablename__ = "grading_results"
    __table_args__ = (
        Index("ix_grading_results_group_question_status", "assignment_group_id", "question_id", "result_status"),
        Index("ix_grading_results_task_updated", "grading_task_id", "updated_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    grading_task_id: Mapped[str] = mapped_column(Identifier, ForeignKey("grading_tasks.id"), nullable=False)
    assignment_group_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_groups.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(Identifier, ForeignKey("batch_questions.id"), nullable=False)
    answer_version_id: Mapped[str] = mapped_column(Identifier, ForeignKey("answer_versions.id"), nullable=False)
    rubric_version_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("rubric_versions.id"))
    result_status: Mapped[str] = mapped_column(String(32), nullable=False)
    suggested_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    teacher_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    ai_comment: Mapped[str | None] = mapped_column(Text)
    teacher_comment: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[str | None] = mapped_column(Text)
    deduction_json: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(120))
    rule_version: Mapped[str | None] = mapped_column(String(120))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class GradingPointResult(Base):
    __tablename__ = "grading_point_results"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    grading_result_id: Mapped[str] = mapped_column(Identifier, ForeignKey("grading_results.id"), nullable=False)
    rubric_point_id: Mapped[str] = mapped_column(Identifier, ForeignKey("rubric_points.id"), nullable=False)
    suggested_score: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    teacher_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    evidence: Mapped[str | None] = mapped_column(Text)
    deduction_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class Anomaly(Base):
    __tablename__ = "anomalies"
    __table_args__ = (Index("ix_anomalies_batch_status_severity", "batch_id", "status", "severity"),)

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    assignment_group_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("assignment_groups.id"))
    source_page_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("source_pages.id"))
    question_id: Mapped[str | None] = mapped_column(Identifier, ForeignKey("batch_questions.id"))
    anomaly_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    resolved_at: Mapped[str | None] = mapped_column(Timestamp)


class ReviewRecord(Base):
    __tablename__ = "review_records"
    __table_args__ = (Index("ix_review_records_entity_created", "entity_type", "entity_id", "created_at"),)

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    user_id: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(Identifier, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)


class ReportExport(Base):
    __tablename__ = "report_exports"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Identifier, ForeignKey("assignment_batches.id"), nullable=False)
    requested_by: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), nullable=False)
    report_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Timestamp)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[str] = mapped_column(Identifier, primary_key=True)
    user_id: Mapped[str] = mapped_column(Identifier, ForeignKey("users.id"), unique=True, nullable=False)
    teacher_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(120), nullable=False, default="DeepSeek")
    model_name: Mapped[str | None] = mapped_column(String(120), default="deepseek-flash")
    model_base_url: Mapped[str | None] = mapped_column(String(500))
    api_key_ref: Mapped[str | None] = mapped_column(String(255))
    storage_root: Mapped[str] = mapped_column(String(500), nullable=False)
    retention_days: Mapped[int | None] = mapped_column()
    queue_limit: Mapped[int] = mapped_column(nullable=False, default=20)
    worker_concurrency: Mapped[int] = mapped_column(nullable=False, default=4)
    updated_at: Mapped[str] = mapped_column(Timestamp, nullable=False)
