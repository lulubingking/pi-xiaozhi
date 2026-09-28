"""Geometry based OCR reading order shared by the worker and correction API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _rect(item: Any) -> tuple[float, float, float, float] | None:
    box = _value(item, "box")
    if isinstance(box, Sequence) and not isinstance(box, (str, bytes)) and len(box) >= 4:
        numbers = [_number(value) for value in box[:4]]
    else:
        numbers = [_number(_value(item, key)) for key in ("x1", "y1", "x2", "y2")]
    if any(value is None for value in numbers):
        return None
    x1, y1, x2, y2 = (float(value) for value in numbers)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _fallback_key(record: tuple[Any, int, tuple[float, float, float, float] | None]) -> tuple[float, int]:
    item, original_index, _ = record
    index = _number(_value(item, "block_index"))
    return (index if index is not None else float(original_index), original_index)


def _row_order(
    records: list[tuple[Any, int, tuple[float, float, float, float]]],
) -> list[tuple[Any, int, tuple[float, float, float, float]]]:
    if not records:
        return []
    heights = [max(1.0, box[3] - box[1]) for _, _, box in records]
    row_tolerance = max(1.0, median(heights) * 0.42)
    by_y = sorted(records, key=lambda record: ((record[2][1] + record[2][3]) / 2, record[2][0], record[1]))
    rows: list[list[tuple[Any, int, tuple[float, float, float, float]]]] = []
    row_centers: list[float] = []
    for record in by_y:
        _, _, box = record
        center_y = (box[1] + box[3]) / 2
        eligible = [index for index, center in enumerate(row_centers) if abs(center_y - center) <= row_tolerance]
        if eligible:
            row_index = min(eligible, key=lambda index: abs(center_y - row_centers[index]))
            rows[row_index].append(record)
            row_centers[row_index] = median([(item[2][1] + item[2][3]) / 2 for item in rows[row_index]])
        else:
            rows.append([record])
            row_centers.append(center_y)

    ordered: list[tuple[Any, int, tuple[float, float, float, float]]] = []
    for _, row in sorted(zip(row_centers, rows), key=lambda pair: pair[0]):
        ordered.extend(sorted(row, key=lambda record: (record[2][0], record[2][1], record[1])))
    return ordered


def _column_split(
    records: list[tuple[Any, int, tuple[float, float, float, float]]],
) -> tuple[float, list[tuple[Any, int, tuple[float, float, float, float]]]] | None:
    if len(records) < 4:
        return None
    min_x = min(record[2][0] for record in records)
    max_x = max(record[2][2] for record in records)
    x_span = max_x - min_x
    if x_span <= 1:
        return None

    # Ignore title/heading blocks that span both columns when locating the gutter.
    candidates = [record for record in records if record[2][2] - record[2][0] < x_span * 0.72]
    if len(candidates) < 4:
        return None
    centers = sorted((record[2][0] + record[2][2]) / 2 for record in candidates)
    cuts = [
        (centers[index + 1] - centers[index], index)
        for index in range(1, len(centers) - 2)
    ]
    if not cuts:
        return None
    _, cut_index = max(cuts)
    split = (centers[cut_index] + centers[cut_index + 1]) / 2
    left = [record for record in candidates if (record[2][0] + record[2][2]) / 2 < split]
    right = [record for record in candidates if (record[2][0] + record[2][2]) / 2 >= split]
    if len(left) < 2 or len(right) < 2:
        return None

    gutter = min(record[2][0] for record in right) - max(record[2][2] for record in left)
    median_width = median(record[2][2] - record[2][0] for record in candidates)
    if gutter < max(x_span * 0.07, median_width * 0.18):
        return None

    # All boxes crossing the empty gutter are full-width headings. Keep them as
    # separators while reading each column top-to-bottom.
    spans = [record for record in records if record[2][0] < split < record[2][2]]
    left = [record for record in records if record not in spans and (record[2][0] + record[2][2]) / 2 < split]
    right = [record for record in records if record not in spans and (record[2][0] + record[2][2]) / 2 >= split]
    if len(left) < 2 or len(right) < 2:
        return None
    return split, spans


def sort_ocr_blocks_reading_order(blocks: Sequence[Any]) -> list[Any]:
    """Return OCR blocks in visual order, honoring rows and clear column gutters.

    The model's block index is used only when any box is missing. This avoids
    partially reordering an incomplete layout based on unreliable coordinates.
    """

    records = [(item, index, _rect(item)) for index, item in enumerate(blocks)]
    if not records:
        return []
    if any(box is None for _, _, box in records):
        return [item for item, _, _ in sorted(records, key=_fallback_key)]

    positioned = [(item, index, box) for item, index, box in records if box is not None]
    column = _column_split(positioned)
    if column is None:
        return [item for item, _, _ in _row_order(positioned)]

    split, spans = column
    span_rows = _row_order(spans)
    column_records = [record for record in positioned if record not in spans]
    left = [record for record in column_records if (record[2][0] + record[2][2]) / 2 < split]
    right = [record for record in column_records if (record[2][0] + record[2][2]) / 2 >= split]
    span_centers: list[float] = []
    for record in span_rows:
        center = (record[2][1] + record[2][3]) / 2
        if not span_centers or abs(center - span_centers[-1]) > max(1.0, median([r[2][3] - r[2][1] for r in span_rows]) * 0.42):
            span_centers.append(center)

    ordered: list[tuple[Any, int, tuple[float, float, float, float]]] = []
    previous_center = float("-inf")
    for center in span_centers:
        for column_records_for_side in (left, right):
            band = [record for record in column_records_for_side if previous_center <= (record[2][1] + record[2][3]) / 2 < center]
            ordered.extend(_row_order(band))
        band_spans = [record for record in span_rows if abs((record[2][1] + record[2][3]) / 2 - center) <= max(1.0, median([r[2][3] - r[2][1] for r in span_rows]) * 0.42)]
        ordered.extend(_row_order(band_spans))
        previous_center = center
    for column_records_for_side in (left, right):
        band = [record for record in column_records_for_side if (record[2][1] + record[2][3]) / 2 >= previous_center]
        ordered.extend(_row_order(band))
    return [item for item, _, _ in ordered]
