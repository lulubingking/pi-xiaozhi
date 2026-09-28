"""批改任务 Worker。

Worker 从持久化队列领取一个批改运行，逐题生成规则结果或进入异常队列。
主观题只使用已从上传试卷 OCR 页面确认并保存的题干快照；题干缺失时安全失败并转人工，
不会把参考答案当作题干发送给外部模型。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

# 支持从项目根目录执行 ``python backend/app/grading_worker.py``。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.model_config import get_model_config
from app.core.utils import new_id, utc_now
from app.db.session import SessionLocal
from app.models import (
    Anomaly,
    AnswerVersion,
    AssignmentBatch,
    BatchQuestion,
    GradingPointResult,
    GradingResult,
    GradingRun,
    GradingTask,
    QuestionAnswer,
    RubricExample,
    RubricPoint,
    RubricVersion,
    Task,
    User,
)
from app.scoring.llm_adapter import (
    AdapterError,
    PROMPT_VERSION,
    RubricPointSpec,
    SubjectiveGradingInput,
    grade_subjective,
)
from app.scoring.objective_rules import ObjectiveRuleError, score_objective
from app.workflows.grading_graph import run_grading_workflow


class WorkerFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, anomaly_type: str = "model_failure"):
        self.code = code
        self.message = message
        self.anomaly_type = anomaly_type
        super().__init__(message)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _record_anomaly(
    db: Session,
    *,
    batch_id: str,
    group_id: str | None,
    question_id: str | None,
    anomaly_type: str,
    severity: str,
    evidence: dict,
) -> None:
    existing = db.scalar(
        select(Anomaly).where(
            Anomaly.batch_id == batch_id,
            Anomaly.assignment_group_id == group_id,
            Anomaly.question_id == question_id,
            Anomaly.anomaly_type == anomaly_type,
            Anomaly.status == "open",
        )
    )
    if existing:
        existing.evidence_json = _json(evidence)
        return
    db.add(
        Anomaly(
            id=new_id("anomaly"),
            batch_id=batch_id,
            assignment_group_id=group_id,
            source_page_id=None,
            question_id=question_id,
            anomaly_type=anomaly_type,
            severity=severity,
            evidence_json=_json(evidence),
            status="open",
            resolution=None,
            created_at=utc_now(),
            resolved_at=None,
        )
    )


def _current_answer(db: Session, task: GradingTask) -> tuple[QuestionAnswer, AnswerVersion]:
    answer = db.scalar(
        select(QuestionAnswer).where(
            QuestionAnswer.assignment_group_id == task.assignment_group_id,
            QuestionAnswer.question_id == task.question_id,
        )
    )
    if not answer:
        raise WorkerFailure("ANSWER_COVERAGE_MISSING", "批改任务缺少当前答案记录。", anomaly_type="answer_missing")
    version = db.get(AnswerVersion, answer.current_version_id)
    if not version:
        raise WorkerFailure("ANSWER_VERSION_MISSING", "批改任务缺少当前答案版本。", anomaly_type="answer_missing")
    if answer.coverage_status not in {"complete", "reviewed"}:
        raise WorkerFailure("ANSWER_COVERAGE_NOT_CONFIRMED", "当前答案尚未完成教师确认。", anomaly_type="answer_missing")
    return answer, version


def _latest_result(db: Session, task_id: str) -> GradingResult | None:
    return db.scalar(
        select(GradingResult)
        .where(GradingResult.grading_task_id == task_id)
        .order_by(GradingResult.updated_at.desc(), GradingResult.created_at.desc())
        .limit(1)
    )


def _result_record(
    db: Session,
    *,
    grading_task: GradingTask,
    answer_version: AnswerVersion,
    rubric_version_id: str | None,
    result_status: str,
    suggested_score: Decimal | None,
    ai_comment: str | None,
    evidence: dict | list | None,
    deduction: dict | list | None,
    model_name: str | None,
    prompt_version: str | None,
    rule_version: str | None,
    input_hash: str | None = None,
) -> GradingResult:
    now = utc_now()
    result = _latest_result(db, grading_task.id)
    if result and result.result_status == "reviewed":
        raise WorkerFailure(
            "REVIEWED_RESULT_RERUN_REQUIRES_CONFIRMATION",
            "已复核结果不能被 Worker 直接覆盖，请先完成重跑确认。",
            anomaly_type="model_failure",
        )
    if result is None:
        result = GradingResult(
            id=new_id("grading_result"),
            grading_task_id=grading_task.id,
            assignment_group_id=grading_task.assignment_group_id,
            question_id=grading_task.question_id,
            answer_version_id=answer_version.id,
            rubric_version_id=rubric_version_id,
            result_status=result_status,
            suggested_score=suggested_score,
            teacher_score=None,
            ai_comment=ai_comment,
            teacher_comment=None,
            evidence_json=_json(evidence) if evidence is not None else None,
            deduction_json=_json(deduction) if deduction is not None else None,
            model_name=model_name,
            prompt_version=prompt_version,
            rule_version=rule_version,
            input_hash=input_hash or grading_task.input_hash,
            created_at=now,
            updated_at=now,
        )
        db.add(result)
        # GradingPointResult.grading_result_id 是外键，且当前模型未声明 ORM relationship。
        # 先把结果行刷入数据库，避免 SQLAlchemy 在批量 flush 时先插入评分点结果。
        db.flush()
        return result
    db.execute(delete(GradingPointResult).where(GradingPointResult.grading_result_id == result.id))
    result.answer_version_id = answer_version.id
    result.rubric_version_id = rubric_version_id
    result.result_status = result_status
    result.suggested_score = suggested_score
    result.teacher_score = None
    result.ai_comment = ai_comment
    result.teacher_comment = None
    result.evidence_json = _json(evidence) if evidence is not None else None
    result.deduction_json = _json(deduction) if deduction is not None else None
    result.model_name = model_name
    result.prompt_version = prompt_version
    result.rule_version = rule_version
    result.input_hash = input_hash or grading_task.input_hash
    result.updated_at = now
    return result


def _process_objective(
    db: Session,
    run: GradingRun,
    grading_task: GradingTask,
    question: BatchQuestion,
    answer: QuestionAnswer,
    answer_version: AnswerVersion,
) -> None:
    try:
        scored = score_objective(
            answer_text=answer.answer_text_joined if answer.answer_text_joined is not None else answer_version.answer_text,
            reference_answer=question.reference_answer,
            objective_rule_json=question.objective_rule_json,
            max_score=float(question.max_score),
            blank_confirmed=bool(answer_version.is_blank_confirmed),
        )
    except ObjectiveRuleError as exc:
        raise WorkerFailure(exc.code, exc.message, anomaly_type="unmatched_answer") from exc

    evidence = {
        "answer_raw": answer.answer_text_joined if answer.answer_text_joined is not None else answer_version.answer_text,
        "answer_normalized": scored.normalized_answer,
        "accepted_answers_normalized": scored.normalized_accepted_answers,
        "matched_answer": scored.matched_answer,
        "rule_version": scored.rule_version,
    }
    deduction = None if scored.matched else [{"reason": scored.reason}]
    _result_record(
        db,
        grading_task=grading_task,
        answer_version=answer_version,
        rubric_version_id=None,
        result_status="pending_review",
        suggested_score=Decimal(str(scored.suggested_score)).quantize(Decimal("0.01")),
        ai_comment=f"规则判分：{scored.reason}。结果需教师复核后生效。",
        evidence=evidence,
        deduction=deduction,
        model_name=None,
        prompt_version=None,
        rule_version=scored.rule_version,
    )
    if not scored.matched:
        _record_anomaly(
            db,
            batch_id=run.batch_id,
            group_id=grading_task.assignment_group_id,
            question_id=grading_task.question_id,
            anomaly_type="unmatched_answer",
            severity="warning",
            evidence=evidence,
        )
    if not scored.normalized_answer and not answer_version.is_blank_confirmed:
        _record_anomaly(
            db,
            batch_id=run.batch_id,
            group_id=grading_task.assignment_group_id,
            question_id=grading_task.question_id,
            anomaly_type="suspected_blank",
            severity="warning",
            evidence=evidence,
        )


def _process_subjective(
    db: Session,
    run: GradingRun,
    grading_task: GradingTask,
    question: BatchQuestion,
    answer: QuestionAnswer,
    answer_version: AnswerVersion,
) -> None:
    rubric_version = db.get(RubricVersion, question.rubric_version_id) if question.rubric_version_id else None
    if not rubric_version:
        raise WorkerFailure("SUBJECTIVE_RUBRIC_INVALID", "主观题评分标准版本不可用。", anomaly_type="model_failure")

    # 题干必须来自已确认的上传试卷快照，不能把参考答案当成题目要求发送给模型。
    if not (question.question_prompt or "").strip():
        missing_prompt_message = "主观题缺少题目要求字段，暂不能安全调用模型，请转人工或补充题干来源。"
        _result_record(
            db,
            grading_task=grading_task,
            answer_version=answer_version,
            rubric_version_id=question.rubric_version_id,
            result_status="failed",
            suggested_score=None,
            ai_comment=missing_prompt_message,
            evidence={"code": "QUESTION_PROMPT_MISSING"},
            deduction=None,
            model_name=run.model_name,
            prompt_version=PROMPT_VERSION,
            rule_version=run.rule_version,
        )
        raise WorkerFailure(
            "QUESTION_PROMPT_MISSING",
            missing_prompt_message,
            anomaly_type="model_failure",
        )

    rubric_points = list(
        db.scalars(select(RubricPoint).where(RubricPoint.rubric_version_id == rubric_version.id).order_by(RubricPoint.sort_order))
    )
    examples = list(db.scalars(select(RubricExample.content).where(RubricExample.rubric_version_id == rubric_version.id)))
    user = db.get(User, run.created_by)
    if not user:
        raise WorkerFailure("GRADING_USER_NOT_FOUND", "批改运行创建人不存在，无法读取模型配置。", anomaly_type="model_failure")
    model_input = SubjectiveGradingInput(
        question_id=question.id,
        question_text=question.question_prompt,
        answer_text=answer.answer_text_joined if answer.answer_text_joined is not None else answer_version.answer_text,
        reference_answer=question.reference_answer,
        max_score=question.max_score,
        rubric_version_id=rubric_version.id,
        rubric_points=[
            RubricPointSpec(point_id=point.id, label=point.label, max_score=point.max_score)
            for point in rubric_points
        ],
        examples=examples,
    )
    try:
        adapter_result = grade_subjective(get_model_config(db, user), model_input)
    except AdapterError as exc:
        _result_record(
            db,
            grading_task=grading_task,
            answer_version=answer_version,
            rubric_version_id=question.rubric_version_id,
            result_status="failed",
            suggested_score=None,
            ai_comment=f"主观题模型调用失败：{exc.message}",
            evidence={"code": exc.code, "retryable": exc.retryable, "attempts": exc.attempts},
            deduction=None,
            model_name=run.model_name,
            prompt_version=PROMPT_VERSION,
            rule_version=run.rule_version,
        )
        raise

    output = adapter_result.output
    evidence = {
        "response_digest": adapter_result.response_digest,
        "review_required": output.review_required,
        "structured_result": {
            "score": format(output.score, "f"),
            "max_score": format(question.max_score, "f"),
            "comment": output.comment,
        },
        "points": [
            {
                "point_id": point.point_id,
                "score": format(point.score, "f"),
                "max_score": format(point.max_score, "f"),
                "evidence": point.evidence,
                "deduction_reason": point.deduction_reason,
            }
            for point in output.points
        ],
    }
    deduction = [
        {"point_id": point.point_id, "reason": point.deduction_reason}
        for point in output.points
        if point.deduction_reason
    ] or None
    result = _result_record(
        db,
        grading_task=grading_task,
        answer_version=answer_version,
        rubric_version_id=question.rubric_version_id,
        result_status="pending_review",
        suggested_score=output.score,
        ai_comment=output.comment,
        evidence=evidence,
        deduction=deduction,
        model_name=adapter_result.model_name,
        prompt_version=adapter_result.prompt_version,
        rule_version=run.rule_version,
        input_hash=adapter_result.input_hash,
    )
    db.add_all(
        [
            GradingPointResult(
                id=new_id("grading_point_result"),
                grading_result_id=result.id,
                rubric_point_id=point.point_id,
                suggested_score=point.score,
                teacher_score=None,
                evidence=point.evidence,
                deduction_reason=point.deduction_reason,
                created_at=utc_now(),
            )
            for point in output.points
        ]
    )


def _process_one(db: Session, run: GradingRun, grading_task: GradingTask) -> None:
    def load_batch_config() -> BatchQuestion:
        question = db.get(BatchQuestion, grading_task.question_id)
        if not question or question.batch_id != run.batch_id:
            raise WorkerFailure("QUESTION_NOT_FOUND", "批改任务关联的题目不存在。", anomaly_type="model_failure")
        return question

    def validate_batch_config(question: BatchQuestion) -> None:
        if question.question_type not in {"objective", "subjective"}:
            raise WorkerFailure("QUESTION_TYPE_UNSUPPORTED", "题型无法分派到规则引擎或主观题流程。", anomaly_type="model_failure")

    def load_confirmed_answers() -> tuple[QuestionAnswer, AnswerVersion]:
        return _current_answer(db, grading_task)

    def objective_rule_score(answer: QuestionAnswer, answer_version: AnswerVersion) -> None:
        _process_objective(db, run, grading_task, load_batch_config(), answer, answer_version)

    def subjective_llm_score(answer: QuestionAnswer, answer_version: AnswerVersion) -> None:
        _process_subjective(db, run, grading_task, load_batch_config(), answer, answer_version)

    run_grading_workflow(
        load_batch_config=load_batch_config,
        validate_batch_config=validate_batch_config,
        load_confirmed_answers=load_confirmed_answers,
        objective_rule_score=objective_rule_score,
        subjective_llm_score=subjective_llm_score,
    )


def _claim_task(worker_id: str) -> str | None:
    db = SessionLocal()
    try:
        task = db.scalar(
            select(Task)
            .where(Task.task_type == "grading", Task.status == "queued")
            .order_by(Task.created_at, Task.id)
            .with_for_update()
        )
        if task is None:
            db.rollback()
            return None
        now = utc_now()
        task.status = "running"
        task.stage = "grading"
        task.started_at = task.started_at or now
        task.worker_id = worker_id
        task.heartbeat_at = now
        run = db.get(GradingRun, task.resource_id) if task.resource_id else None
        if run:
            run.status = "running"
        db.commit()
        return task.id
    finally:
        db.close()


def _finalize_task(
    db: Session,
    task: Task,
    run: GradingRun,
    batch: AssignmentBatch,
    successes: int,
    failures: list[dict[str, str]],
) -> None:
    now = utc_now()
    task.progress_current = successes + len(failures)
    task.progress_total = max(task.progress_total, task.progress_current)
    task.error_items_json = _json(failures)
    task.heartbeat_at = now
    task.finished_at = now

    # The generic Task represents this execution (which may be a single-task retry),
    # while GradingRun represents every student/question task in the grading run.
    # Derive run and batch state from all child tasks so a one-item retry cannot
    # overwrite the aggregate state of the whole batch.
    child_statuses = list(
        db.scalars(select(GradingTask.status).where(GradingTask.grading_run_id == run.id))
    )
    succeeded_count = child_statuses.count("succeeded")
    failed_count = child_statuses.count("failed")
    active_count = sum(child_status in {"queued", "running"} for child_status in child_statuses)

    if failures and successes:
        task.status = "partial_failed"
        task.error_code = "GRADING_PARTIAL_FAILED"
        task.error_message = "部分题目评分失败，请查看异常并单独重试。"
    elif failures:
        task.status = "failed"
        task.error_code = failures[0].get("code") or "GRADING_FAILED"
        task.error_message = failures[0].get("message") or "批改任务失败。"
    else:
        task.status = "succeeded"
        task.error_code = None
        task.error_message = None

    if active_count:
        run.status = "running"
        run.finished_at = None
        batch.status = "grading"
    elif failed_count and succeeded_count:
        run.status = "partial_failed"
        run.finished_at = now
        batch.status = "partial_failure"
    elif failed_count:
        run.status = "failed"
        run.finished_at = now
        batch.status = "partial_failure"
    else:
        run.status = "succeeded"
        run.finished_at = now
        batch.status = "pending_review"
    task.stage = "saving"
    batch.updated_at = now
    batch.version += 1


def _process_task(task_id: str, worker_id: str) -> None:
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task or not task.resource_id:
            return
        run = db.get(GradingRun, task.resource_id)
        batch = db.get(AssignmentBatch, task.batch_id) if task.batch_id else None
        if not run or not batch:
            task.status = "failed"
            task.stage = "saving"
            task.error_code = "GRADING_RUN_NOT_FOUND"
            task.error_message = "批改运行或批次不存在。"
            task.finished_at = utc_now()
            db.commit()
            return

        grading_tasks = list(
            db.scalars(
                select(GradingTask)
                .where(GradingTask.grading_run_id == run.id, GradingTask.status == "queued")
                .order_by(GradingTask.created_at, GradingTask.id)
            )
        )
        task.progress_total = len(grading_tasks)
        successes = 0
        failures: list[dict[str, str]] = []
        for index, grading_task in enumerate(grading_tasks, start=1):
            now = utc_now()
            grading_task.status = "running"
            grading_task.locked_by = worker_id
            grading_task.heartbeat_at = now
            grading_task.updated_at = now
            task.stage = "scoring"
            task.progress_current = index - 1
            task.heartbeat_at = now
            db.commit()
            try:
                _process_one(db, run, grading_task)
                grading_task.status = "succeeded"
                grading_task.error_code = None
                grading_task.error_message = None
                successes += 1
            except (WorkerFailure, AdapterError) as exc:
                code = getattr(exc, "code", "GRADING_FAILED")
                message = getattr(exc, "message", str(exc))
                anomaly_type = getattr(exc, "anomaly_type", "model_failure")
                grading_task.status = "failed"
                grading_task.retry_count += 1
                grading_task.error_code = code
                grading_task.error_message = message
                _record_anomaly(
                    db,
                    batch_id=batch.id,
                    group_id=grading_task.assignment_group_id,
                    question_id=grading_task.question_id,
                    anomaly_type=anomaly_type,
                    severity="blocking",
                    evidence={"code": code, "message": message},
                )
                failures.append({"grading_task_id": grading_task.id, "code": code, "message": message})
            except Exception as exc:  # noqa: BLE001 - Worker 必须持久化未知失败
                code = "GRADING_WORKER_UNEXPECTED_ERROR"
                message = f"{type(exc).__name__}: {exc}"
                grading_task.status = "failed"
                grading_task.retry_count += 1
                grading_task.error_code = code
                grading_task.error_message = message
                _record_anomaly(
                    db,
                    batch_id=batch.id,
                    group_id=grading_task.assignment_group_id,
                    question_id=grading_task.question_id,
                    anomaly_type="model_failure",
                    severity="blocking",
                    evidence={"code": code, "message": message},
                )
                failures.append({"grading_task_id": grading_task.id, "code": code, "message": message})
            grading_task.locked_by = None
            grading_task.heartbeat_at = utc_now()
            grading_task.updated_at = utc_now()
            task.progress_current = index
            task.heartbeat_at = utc_now()
            task.error_items_json = _json(failures)
            db.commit()

        task = db.get(Task, task_id)
        run = db.get(GradingRun, run.id)
        batch = db.get(AssignmentBatch, batch.id)
        if task and run and batch:
            _finalize_task(db, task, run, batch, successes, failures)
            db.commit()
    finally:
        db.close()


def run_worker(
    *,
    stop_event: threading.Event | None = None,
    poll_interval: float = 2.0,
    once: bool = False,
    worker_id: str | None = None,
) -> None:
    """运行批改队列。

    API 服务会在应用生命周期内启动一个同进程 Worker，确保网页端输入的
    运行时 API Key 能被主观题批改使用；命令行模式仍可单独运行 Worker。
    """

    resolved_worker_id = worker_id or os.getenv("GRADING_WORKER_ID") or f"grading-worker:{socket.gethostname()}:{os.getpid()}"
    interval = max(0.2, poll_interval)
    while stop_event is None or not stop_event.is_set():
        task_id = _claim_task(resolved_worker_id)
        if task_id is None:
            if once:
                return
            if stop_event is not None:
                stop_event.wait(interval)
            else:
                time.sleep(interval)
            continue
        _process_task(task_id, resolved_worker_id)
        if once:
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="领取并处理一个 queued 批改运行后退出")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="轮询间隔（秒），默认 2")
    args = parser.parse_args()
    run_worker(poll_interval=args.poll_interval, once=args.once)


if __name__ == "__main__":
    main()
