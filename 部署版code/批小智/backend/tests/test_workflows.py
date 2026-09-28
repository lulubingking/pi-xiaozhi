"""LangGraph 核心节点的无数据库冒烟测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.workflows.grading_graph import run_grading_workflow
from app.workflows.ocr_graph import run_ocr_workflow


class OcrGraphTests(unittest.TestCase):
    def test_ocr_graph_reaches_teacher_correction_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "page.png"
            raw = root / "raw.json"
            source.write_bytes(b"test")

            state = run_ocr_workflow(
                profile="printed",
                source_path=source,
                processed_path=root / "processed.png",
                raw_path=raw,
                subject="chinese",
                page_index=1,
                execute_engine=lambda: (raw.touch(), {"status": "succeeded", "blocks": [{"text_raw": "答案"}]})[1],
            )

            self.assertEqual(state["stage"], "wait_for_teacher_correction")
            self.assertTrue(state["awaiting_teacher_correction"])
            self.assertIn("run_paddleocr", state["trace"])


class GradingGraphTests(unittest.TestCase):
    def test_grading_graph_dispatches_objective_rule(self) -> None:
        events: list[str] = []
        question = type("Question", (), {"question_type": "objective"})()
        state = run_grading_workflow(
            load_batch_config=lambda: question,
            validate_batch_config=lambda value: None,
            load_confirmed_answers=lambda: ("answer", "version"),
            objective_rule_score=lambda answer, version: events.append("objective"),
            subjective_llm_score=lambda answer, version: events.append("subjective"),
        )

        self.assertEqual(events, ["objective"])
        self.assertEqual(state["stage"], "enqueue_teacher_review")
        self.assertIn("dispatch_question_type", state["trace"])


if __name__ == "__main__":
    unittest.main()
