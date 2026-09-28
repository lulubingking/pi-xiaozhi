"""单题重跑时队列进度与 Worker 状态的回归测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TEST_ROOT = Path(tempfile.mkdtemp(prefix="pixiaozhi-grading-queue-state-"))
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{(TEST_ROOT / 'test.db').as_posix()}"
os.environ["STORAGE_ROOT"] = str(TEST_ROOT / "uploads")
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api import grading
from app.api.errors import ApiError
from app.grading_worker import _claim_task


class GradingQueueStateTests(unittest.TestCase):
    def test_independent_subjective_rerun_requires_a_model_key(self) -> None:
        user = SimpleNamespace(id="teacher-1")
        task = SimpleNamespace(question_id="question-1")
        question = SimpleNamespace(question_type="subjective")
        model = SimpleNamespace(model_name="test-model", base_url="https://model.invalid", api_key_ref="key-ref")
        db = SimpleNamespace(get=lambda _model, _id: question)
        with patch.object(grading, "get_model_config", return_value=model), patch.object(grading, "resolve_api_key", return_value=None):
            with self.assertRaises(ApiError) as raised:
                grading._validate_independent_rerun_prerequisites(db, user, task)
        self.assertEqual(raised.exception.code, "MODEL_KEY_UNAVAILABLE")

    def test_independent_objective_rerun_does_not_require_a_model_key(self) -> None:
        user = SimpleNamespace(id="teacher-1")
        task = SimpleNamespace(question_id="question-1")
        question = SimpleNamespace(question_type="objective")
        db = SimpleNamespace(get=lambda _model, _id: question)
        with patch.object(grading, "get_model_config", side_effect=AssertionError("客观题不应读取模型配置")):
            grading._validate_independent_rerun_prerequisites(db, user, task)

    def test_pending_review_queue_includes_results_awaiting_rerun(self) -> None:
        self.assertEqual(
            grading._review_queue_result_statuses("pending_review"),
            ("pending_review", "stale", "failed"),
        )
        self.assertIsNone(grading._review_queue_result_statuses("all"))
        self.assertEqual(grading._review_queue_result_statuses("reviewed"), ("reviewed",))

    def make_records(self, *, queue_status: str = "succeeded"):
        grading_task = SimpleNamespace(
            retry_count=2,
            status="succeeded",
            error_code="OLD_FAILURE",
            error_message="旧错误",
            locked_by="old-worker",
            heartbeat_at="old-heartbeat",
            updated_at="old-updated",
        )
        run = SimpleNamespace(id="run-1", status="succeeded", finished_at="old-finished")
        batch = SimpleNamespace(id="batch-1", status="pending_review", updated_at=None, version=4)
        queue_task = SimpleNamespace(
            status=queue_status,
            stage="saving",
            progress_current=1,
            progress_total=1,
            error_code="OLD_FAILURE",
            error_message="旧错误",
            error_items_json='[{"code":"OLD_FAILURE"}]',
            started_at="old-started",
            finished_at="old-finished",
            worker_id="old-worker",
            heartbeat_at="old-heartbeat",
        )
        db = SimpleNamespace(scalar=lambda _statement: queue_task)
        return db, grading_task, run, batch, queue_task

    def test_single_task_rerun_clears_stale_progress_and_worker_markers(self) -> None:
        db, task, run, batch, queue_task = self.make_records()
        grading._queue_existing_task(db, task, run, batch, reset_retry=True)

        self.assertEqual(task.status, "queued")
        self.assertEqual(task.retry_count, 0)
        self.assertEqual(run.status, "queued")
        self.assertEqual(run.finished_at, None)
        self.assertEqual(batch.status, "grading")
        self.assertEqual(queue_task.status, "queued")
        self.assertEqual(queue_task.progress_current, 0)
        self.assertEqual(queue_task.progress_total, 1)
        self.assertEqual(queue_task.stage, "grading_queued")
        self.assertEqual(queue_task.error_items_json, "[]")
        self.assertIsNone(queue_task.started_at)
        self.assertIsNone(queue_task.finished_at)
        self.assertIsNone(queue_task.worker_id)
        self.assertIsNone(queue_task.heartbeat_at)

    def test_failed_question_can_be_requeued_independently(self) -> None:
        db, task, run, batch, queue_task = self.make_records()
        task.status = "failed"
        grading._queue_existing_task(db, task, run, batch, reset_retry=True)

        self.assertEqual(task.status, "queued")
        self.assertEqual(queue_task.status, "queued")
        self.assertEqual(run.status, "queued")
        self.assertEqual(queue_task.progress_current, 0)
        self.assertEqual(queue_task.progress_total, 1)

    def test_cannot_add_a_single_task_to_an_already_running_queue(self) -> None:
        db, task, run, batch, _queue_task = self.make_records(queue_status="running")
        with self.assertRaises(ApiError) as raised:
            grading._queue_existing_task(db, task, run, batch, reset_retry=True)
        self.assertEqual(raised.exception.code, "GRADING_RUN_ALREADY_ACTIVE")
        self.assertEqual(task.status, "succeeded")
        self.assertEqual(run.status, "succeeded")

    def test_worker_claim_marks_run_as_running(self) -> None:
        task = SimpleNamespace(
            id="task-1",
            resource_id="run-1",
            status="queued",
            stage="grading_queued",
            started_at=None,
            worker_id=None,
            heartbeat_at=None,
        )
        run = SimpleNamespace(status="queued")
        db = SimpleNamespace(
            scalar=lambda _statement: task,
            get=lambda _model, _id: run,
            commit=lambda: None,
            rollback=lambda: None,
            close=lambda: None,
        )
        with patch("app.grading_worker.SessionLocal", return_value=db):
            claimed_id = _claim_task("grading-worker:test:123")

        self.assertEqual(claimed_id, "task-1")
        self.assertEqual(task.status, "running")
        self.assertEqual(run.status, "running")
        self.assertEqual(task.worker_id, "grading-worker:test:123")


if __name__ == "__main__":
    unittest.main()
