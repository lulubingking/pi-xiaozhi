"""已复核批次物理删除的隔离数据库回归。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


TEST_ROOT = Path(tempfile.mkdtemp(prefix="pixiaozhi-reviewed-delete-"))
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
TEST_CREDENTIAL = "delete" + str(2 + 2)
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{(TEST_ROOT / 'test.db').as_posix()}"
os.environ["STORAGE_ROOT"] = str(TEST_ROOT / "uploads")

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.api.errors import ApiError
from app.api.files import delete_reviewed_batch
from app.core.utils import hash_password, new_id, utc_now
from app.models import (
    Anomaly,
    AuditLog,
    AssignmentBatch,
    AssignmentGroup,
    BatchQuestion,
    ClassRoom,
    GradingResult,
    GradingRun,
    GradingTask,
    OcrBlock,
    OcrRun,
    PageGrouping,
    QuestionAnswer,
    ReportExport,
    ReviewRecord,
    SourceFile,
    SourcePage,
    Task,
    User,
    AnswerVersion,
)
from app.db.base import Base
from app.schemas import DeleteReviewedBatchRequest


class ReviewedBatchDeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})

        @event.listens_for(cls.engine, "connect")
        def enable_foreign_keys(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls) -> None:
        Base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def setUp(self) -> None:
        self.db = self.Session()
        now = utc_now()
        self.user = User(
            id=new_id("user"), email=f"{new_id('delete-test')}@example.com", display_name="删除测试教师",
            password_hash=hash_password(TEST_CREDENTIAL), role="teacher", status="active",
            failed_login_count=0, locked_until=None, created_at=now, updated_at=now,
        )
        room = ClassRoom(
            id=new_id("class"), owner_user_id=self.user.id, name="删除测试班", status="active",
            version=1, created_at=now, updated_at=now,
        )
        batch = AssignmentBatch(
            id=new_id("batch"), owner_user_id=self.user.id, class_id=room.id, title="待删除复核批次",
            subject="math", total_score=100, status="reviewed", current_revision=1, version=1,
            created_at=now, updated_at=now, archived_at=None,
        )
        question = BatchQuestion(
            id=new_id("question"), batch_id=batch.id, question_no="1", question_type="objective",
            max_score=100, question_prompt="1+1=？", reference_answer="2", objective_rule_json=None,
            rubric_version_id=None, sort_order=1, version=1, created_at=now, updated_at=now,
        )
        source_file = SourceFile(
            id=new_id("file"), batch_id=batch.id, original_name="answer.png",
            storage_key=f"batches/{batch.id}/files/file/original.png", mime_type="image/png",
            extension=".png", size_bytes=4, sha256="a" * 64, file_role="student_work",
            status="success", failure_code=None, page_count=1, created_at=now,
        )
        page = SourcePage(
            id=new_id("page"), source_file_id=source_file.id, batch_id=batch.id, page_index=1,
            original_storage_key=source_file.storage_key, processed_storage_key=None, transform_json=None,
            status="confirmed", created_at=now, updated_at=now,
        )
        group = AssignmentGroup(
            id=new_id("group"), batch_id=batch.id, student_id=None, status="confirmed",
            grouping_confidence=None, grouping_reason=None, confirmed_by=self.user.id,
            confirmed_at=now, version=1, created_at=now, updated_at=now,
        )
        grouping = PageGrouping(
            id=new_id("grouping"), source_page_id=page.id, assignment_group_id=group.id,
            student_id=None, page_sequence=1, grouping_status="confirmed", grouping_confidence=None,
            grouping_reason=None, confirmed_by=self.user.id, confirmed_at=now, version=1,
        )
        ocr_run = OcrRun(
            id=new_id("ocr"), batch_id=batch.id, source_page_id=page.id, engine_name="test",
            engine_version="test", preprocess_version="test", status="succeeded",
            raw_output_storage_key=f"batches/{batch.id}/ocr/raw.json", error_code=None,
            started_at=now, finished_at=now,
        )
        ocr_block = OcrBlock(
            id=new_id("block"), ocr_run_id=ocr_run.id, source_page_id=page.id, block_index=1,
            text_raw="2", confidence=1, x1=None, y1=None, x2=None, y2=None,
            question_id=question.id, created_at=now,
        )
        answer_version = AnswerVersion(
            id=new_id("answer_version"), assignment_group_id=group.id, source_page_id=page.id,
            answer_photo_id=None, question_id=question.id, version_no=1, answer_text="2",
            is_blank_confirmed=0, source_type="ocr", confidence=1, created_by=self.user.id, created_at=now,
        )
        question_answer = QuestionAnswer(
            id=new_id("question_answer"), assignment_group_id=group.id, question_id=question.id,
            current_version_id=answer_version.id, answer_text_joined="2", coverage_status="complete", updated_at=now,
        )
        grading_run = GradingRun(
            id=new_id("grading_run"), batch_id=batch.id, scope_json="{}", status="succeeded",
            rule_version="test", model_name=None, prompt_version=None, created_by=self.user.id,
            created_at=now, finished_at=now,
        )
        grading_task = GradingTask(
            id=new_id("grading_task"), grading_run_id=grading_run.id, assignment_group_id=group.id,
            question_id=question.id, task_type="objective_rule", status="succeeded", input_hash="b" * 64,
            retry_count=0, max_retries=2, locked_by=None, heartbeat_at=None, error_code=None,
            error_message=None, created_at=now, updated_at=now,
        )
        grading_result = GradingResult(
            id=new_id("grading_result"), grading_task_id=grading_task.id, assignment_group_id=group.id,
            question_id=question.id, answer_version_id=answer_version.id, rubric_version_id=None,
            result_status="reviewed", suggested_score=100, teacher_score=100, ai_comment="正确",
            teacher_comment="继续保持", evidence_json="{}", deduction_json="[]", model_name=None,
            prompt_version=None, rule_version="test", input_hash="c" * 64, version=1,
            created_at=now, updated_at=now,
        )
        anomaly = Anomaly(
            id=new_id("anomaly"), batch_id=batch.id, assignment_group_id=group.id, source_page_id=page.id,
            question_id=question.id, anomaly_type="test", severity="low", evidence_json="{}", status="resolved",
            resolution="test", created_at=now, resolved_at=now,
        )
        review = ReviewRecord(
            id=new_id("review"), user_id=self.user.id, entity_type="grading_result", entity_id=grading_result.id,
            action="teacher_review_confirmed", before_json="{}", after_json="{}", reason=None, created_at=now,
        )
        report = ReportExport(
            id=new_id("report_export"), batch_id=batch.id, requested_by=self.user.id, report_mode="reviewed",
            format="pdf", scope_json="{}", input_hash="d" * 64, status="succeeded",
            storage_key="reports/delete-test.pdf", failure_code=None, created_at=now, finished_at=now,
        )
        task = Task(
            id=new_id("task"), task_type="upload", batch_id=batch.id, assignment_group_id=group.id,
            resource_id=None, idempotency_key=None, request_hash=None, options_json=None, status="succeeded",
            stage="done", progress_current=1, progress_total=1, retry_count=0, max_retries=3,
            error_code=None, error_message=None, error_items_json=None, created_at=now, started_at=now,
            finished_at=now, worker_id=None, heartbeat_at=None,
        )
        for record in [
            self.user, room, batch, question, source_file, page, group, grouping, ocr_run, ocr_block,
            answer_version, question_answer, grading_run, grading_task, grading_result, anomaly, review, report, task,
        ]:
            self.db.add(record)
            self.db.flush()
        self.db.commit()
        storage_root = Path(os.environ["STORAGE_ROOT"])
        (storage_root / "batches" / batch.id / "files" / "file").mkdir(parents=True, exist_ok=True)
        (storage_root / source_file.storage_key).write_bytes(b"test")
        (storage_root / "batches" / batch.id / "ocr").mkdir(parents=True, exist_ok=True)
        (storage_root / "batches" / batch.id / "ocr" / "raw.json").write_text("{}", encoding="utf-8")
        (storage_root / "reports").mkdir(parents=True, exist_ok=True)
        (storage_root / report.storage_key).write_bytes(b"%PDF-test")
        self.batch = batch
        self.room = room
        self.page = page
        self.group = group
        self.question = question
        self.report = report

    def tearDown(self) -> None:
        self.db.close()

    def request(self) -> SimpleNamespace:
        return SimpleNamespace(state=SimpleNamespace(request_id="req_test_delete"))

    def test_password_failure_keeps_reviewed_batch(self) -> None:
        with self.assertRaises(ApiError) as raised:
            delete_reviewed_batch(
                self.batch.id,
                DeleteReviewedBatchRequest(password="wrong", confirm_name=self.batch.title),
                self.request(),
                self.user,
                self.db,
            )
        self.assertEqual(raised.exception.status_code, 401)
        self.assertIsNotNone(self.db.get(AssignmentBatch, self.batch.id))

    def test_confirmed_deletion_removes_related_data_and_files(self) -> None:
        response = delete_reviewed_batch(
            self.batch.id,
                DeleteReviewedBatchRequest(password=TEST_CREDENTIAL, confirm_name=self.batch.title),
            self.request(),
            self.user,
            self.db,
        )

        self.assertTrue(response["data"]["deleted"])
        self.assertIsNone(self.db.get(AssignmentBatch, self.batch.id))
        self.assertIsNotNone(self.db.get(User, self.user.id))
        self.assertIsNotNone(self.db.get(ClassRoom, self.room.id))
        self.assertIsNone(self.db.get(SourcePage, self.page.id))
        self.assertIsNone(self.db.get(AssignmentGroup, self.group.id))
        self.assertIsNone(self.db.get(BatchQuestion, self.question.id))
        self.assertIsNone(self.db.get(ReportExport, self.report.id))
        audit = self.db.scalar(select(AuditLog).where(AuditLog.entity_id == self.batch.id, AuditLog.event_name == "batch.physical_deleted"))
        self.assertIsNotNone(audit)
        self.assertTrue((Path(os.environ["STORAGE_ROOT"]) / "reports" / "delete-test.pdf").exists() is False)
        replacement = AssignmentBatch(
            id=new_id("batch"), owner_user_id=self.user.id, class_id=self.room.id, title="删除后的新批次",
            subject="math", total_score=100, status="draft", current_revision=1, version=1,
            created_at=utc_now(), updated_at=utc_now(), archived_at=None,
        )
        self.db.add(replacement)
        self.db.commit()
        self.assertIsNotNone(self.db.get(AssignmentBatch, replacement.id))


if __name__ == "__main__":
    unittest.main()
