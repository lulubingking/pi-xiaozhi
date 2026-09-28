"""教师复核改分、确认和原因校验的回归测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TEST_ROOT = Path(tempfile.mkdtemp(prefix="pixiaozhi-review-validation-"))
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{(TEST_ROOT / 'test.db').as_posix()}"
os.environ["STORAGE_ROOT"] = str(TEST_ROOT / "uploads")
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api import grading
from app.api.errors import ApiError
from app.schemas import ReviewItemRequest


class GradingReviewValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.question = SimpleNamespace(id="question-1", question_type="subjective", max_score=Decimal("4.00"))
        self.result = SimpleNamespace(
            id="result-1",
            question_id=self.question.id,
            version=1,
            result_status="pending_review",
            suggested_score=Decimal("2.00"),
            teacher_score=Decimal("2.00"),
            teacher_comment=None,
            updated_at=None,
        )

    def test_changed_teacher_score_requires_reason_and_is_accepted_with_reason(self) -> None:
        without_reason = ReviewItemRequest(teacher_score=Decimal("3.00"), version=1)
        with self.assertRaises(ApiError) as raised:
            grading._validate_review_request(None, self.result, self.question, without_reason, confirm=True)
        self.assertEqual(raised.exception.code, "REVIEW_REASON_REQUIRED")

        with_reason = ReviewItemRequest(teacher_score=Decimal("3.00"), reason="按原图复核后调整", version=1)
        self.assertEqual(grading._validate_review_request(None, self.result, self.question, with_reason, confirm=True), Decimal("3.00"))

    def test_unchanged_reviewed_result_can_be_reconfirmed_without_reason(self) -> None:
        self.result.result_status = "reviewed"
        unchanged = ReviewItemRequest(teacher_score=Decimal("2.00"), version=1)
        self.assertEqual(grading._validate_review_request(None, self.result, self.question, unchanged, confirm=True), Decimal("2.00"))

    def test_reopening_reviewed_result_requires_reason(self) -> None:
        self.result.result_status = "reviewed"
        unchanged = ReviewItemRequest(teacher_score=Decimal("2.00"), version=1)
        with self.assertRaises(ApiError) as raised:
            grading._validate_review_request(None, self.result, self.question, unchanged, confirm=False)
        self.assertEqual(raised.exception.code, "REVIEW_REASON_REQUIRED")

    def test_subjective_score_cannot_exceed_question_maximum(self) -> None:
        payload = ReviewItemRequest(teacher_score=Decimal("4.01"), reason="测试越界", version=1)
        with self.assertRaises(ApiError) as raised:
            grading._validate_review_request(None, self.result, self.question, payload, confirm=True)
        self.assertEqual(raised.exception.code, "SCORE_OUT_OF_RANGE")

    def test_save_persists_teacher_score_and_keeps_result_pending(self) -> None:
        payload = ReviewItemRequest(teacher_score=Decimal("3.00"), reason="根据原图调整", version=1)
        db = SimpleNamespace(get=lambda _model, _id: self.question, commit=lambda: None)
        before = {"version": 1, "result_status": "pending_review", "teacher_score": "2.00", "teacher_comment": None}
        user = SimpleNamespace(id="teacher-1")

        with (
            patch.object(grading, "_review_item_payload", return_value=before),
            patch.object(grading, "_write_review_record") as write_record,
            patch.object(grading, "_maybe_finalize_batch"),
        ):
            grading._save_review_item(db, user, self.result, SimpleNamespace(id="task-1"), SimpleNamespace(id="run-1"), SimpleNamespace(id="batch-1"), payload, confirm=False)

        self.assertEqual(self.result.teacher_score, Decimal("3.00"))
        self.assertEqual(self.result.result_status, "pending_review")
        self.assertEqual(self.result.version, 2)
        self.assertEqual(write_record.call_args.args[-1], "根据原图调整")

    def test_confirm_persists_score_and_marks_result_reviewed(self) -> None:
        payload = ReviewItemRequest(teacher_score=Decimal("3.00"), reason="根据原图确认", version=1)
        db = SimpleNamespace(get=lambda _model, _id: self.question, commit=lambda: None)
        before = {"version": 1, "result_status": "pending_review", "teacher_score": "2.00", "teacher_comment": None}

        with (
            patch.object(grading, "_review_item_payload", return_value=before),
            patch.object(grading, "_write_review_record"),
            patch.object(grading, "_maybe_finalize_batch"),
        ):
            grading._save_review_item(db, SimpleNamespace(id="teacher-1"), self.result, SimpleNamespace(id="task-1"), SimpleNamespace(id="run-1"), SimpleNamespace(id="batch-1"), payload, confirm=True)

        self.assertEqual(self.result.teacher_score, Decimal("3.00"))
        self.assertEqual(self.result.result_status, "reviewed")
        self.assertEqual(self.result.version, 2)


if __name__ == "__main__":
    unittest.main()
