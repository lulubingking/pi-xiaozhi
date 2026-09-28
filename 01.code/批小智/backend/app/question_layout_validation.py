"""Validation shared by question-layout saving and the OCR worker."""

from __future__ import annotations

from typing import Any


MAX_CROSS_QUESTION_OVERLAP_RATIO = 0.15


def question_layout_region_issues(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return malformed or cross-question overlapping crop regions.

    Multiple crops for one question are allowed when they are disjoint. Any
    substantial overlap would either mix questions or duplicate one answer.
    """

    issues: list[dict[str, Any]] = []
    valid: list[tuple[int, dict[str, Any], float, float, float, float]] = []
    for index, region in enumerate(regions):
        try:
            x1, y1, x2, y2 = (float(region[key]) for key in ("x1", "y1", "x2", "y2"))
        except (KeyError, TypeError, ValueError):
            issues.append({"code": "QUESTION_LAYOUT_REGION_BOUNDS_INVALID", "region_index": index})
            continue
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            issues.append({"code": "QUESTION_LAYOUT_REGION_BOUNDS_INVALID", "region_index": index})
            continue
        valid.append((index, region, x1, y1, x2, y2))

    for left_index in range(len(valid)):
        first_i, first, ax1, ay1, ax2, ay2 = valid[left_index]
        for second_i, second, bx1, by1, bx2, by2 in valid[left_index + 1 :]:
            if first.get("source_page_id") != second.get("source_page_id"):
                continue
            overlap_width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
            overlap_height = max(0.0, min(ay2, by2) - max(ay1, by1))
            overlap_area = overlap_width * overlap_height
            smaller_area = min((ax2 - ax1) * (ay2 - ay1), (bx2 - bx1) * (by2 - by1))
            if smaller_area > 0 and overlap_area / smaller_area > MAX_CROSS_QUESTION_OVERLAP_RATIO:
                issues.append(
                    {
                        "code": "QUESTION_LAYOUT_REGIONS_OVERLAP",
                        "question_ids": [first.get("question_id"), second.get("question_id")],
                        "question_nos": [first.get("question_no"), second.get("question_no")],
                        "region_indexes": [first_i, second_i],
                        "overlap_ratio": round(overlap_area / smaller_area, 4),
                    }
                )
    return issues
