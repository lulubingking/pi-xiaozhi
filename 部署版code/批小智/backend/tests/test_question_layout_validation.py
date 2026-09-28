"""Regression checks for reusable OCR answer-region safety."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.question_layout_validation import question_layout_region_issues


def region(question_id: str, question_no: str, x1: float, y1: float, x2: float, y2: float, page_id: str = "page-1"):
    return {
        "question_id": question_id,
        "question_no": question_no,
        "source_page_id": page_id,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
    }


class QuestionLayoutValidationTests(unittest.TestCase):
    def test_separate_question_regions_are_allowed(self) -> None:
        regions = [region("q1", "1", 0.05, 0.1, 0.48, 0.4), region("q2", "2", 0.52, 0.1, 0.95, 0.4)]
        self.assertEqual(question_layout_region_issues(regions), [])

    def test_substantially_overlapping_regions_for_different_questions_are_rejected(self) -> None:
        regions = [region("q1", "1", 0.03, 0.05, 0.97, 0.5), region("q2", "2", 0.04, 0.35, 0.96, 0.9)]
        issues = question_layout_region_issues(regions)
        self.assertEqual(issues[0]["code"], "QUESTION_LAYOUT_REGIONS_OVERLAP")
        self.assertEqual(issues[0]["question_nos"], ["1", "2"])

    def test_multiple_boxes_for_one_question_must_not_duplicate_the_same_area(self) -> None:
        regions = [region("q1", "1", 0.1, 0.1, 0.5, 0.5), region("q1", "1", 0.2, 0.2, 0.6, 0.6)]
        self.assertEqual(question_layout_region_issues(regions)[0]["code"], "QUESTION_LAYOUT_REGIONS_OVERLAP")

    def test_equal_boxes_on_different_pages_do_not_conflict(self) -> None:
        regions = [region("q1", "1", 0.1, 0.1, 0.9, 0.9, "page-1"), region("q2", "2", 0.1, 0.1, 0.9, 0.9, "page-2")]
        self.assertEqual(question_layout_region_issues(regions), [])

    def test_legacy_out_of_bounds_region_is_reported(self) -> None:
        regions = [region("q1", "1", -0.1, 0.1, 0.9, 0.9)]
        self.assertEqual(question_layout_region_issues(regions)[0]["code"], "QUESTION_LAYOUT_REGION_BOUNDS_INVALID")


if __name__ == "__main__":
    unittest.main()
