"""在已确认的 OCR 专用环境中执行单页识别。

该脚本由 ``app.ocr_worker`` 通过目标解释器启动。它只读原始页面，把预处理
图、候选裁剪和 JSON 结果写入 Worker 指定的临时目录；不修改原图、模型权重
或 OCR 工具测试目录中的历史评测产物。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ocr_reading_order import sort_ocr_blocks_reading_order


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ocr_tool_root() -> Path:
    value = os.getenv("OCR_TOOL_ROOT")
    return Path(value).expanduser().resolve() if value else _project_root() / "OCR工具测试"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    return str(value)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")


def _load_paddle_helpers():
    tool_root = _ocr_tool_root()
    if str(tool_root) not in sys.path:
        sys.path.insert(0, str(tool_root))
    from chinese_postprocess import order_result_by_rows, suppress_red_ink
    from recognition_quality import collapse_formula_candidates, formula_candidate_indexes, select_candidates, union_formula_boxes
    from test_ocr import (
        PaddleOCR,
        build_question_segments,
        clean_and_sort_result,
        crop_bounds,
        find_text_dense_crop,
        prepare_image,
        read_result_data,
        run_prediction,
        trim_formula_crop,
    )

    return {
        "PaddleOCR": PaddleOCR,
        "build_question_segments": build_question_segments,
        "clean_and_sort_result": clean_and_sort_result,
        "crop_bounds": crop_bounds,
        "find_text_dense_crop": find_text_dense_crop,
        "prepare_image": prepare_image,
        "read_result_data": read_result_data,
        "run_prediction": run_prediction,
        "trim_formula_crop": trim_formula_crop,
        "order_result_by_rows": order_result_by_rows,
        "suppress_red_ink": suppress_red_ink,
        "collapse_formula_candidates": collapse_formula_candidates,
        "formula_candidate_indexes": formula_candidate_indexes,
        "select_candidates": select_candidates,
        "union_formula_boxes": union_formula_boxes,
    }


def _normalize_box(box: Any) -> list[float] | None:
    try:
        values = [float(value) for value in list(box)[:4]]
    except (TypeError, ValueError):
        return None
    return values if len(values) == 4 else None


def _recognized_text_blocks(texts: list[Any], scores: list[Any], boxes: list[Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        value = str(text or "").strip()
        if not value:
            continue
        blocks.append(
            {
                "block_index": len(blocks) + 1,
                "text_raw": value,
                "confidence": float(scores[index]) if index < len(scores) else None,
                "box": _normalize_box(boxes[index]) if index < len(boxes) else None,
            }
        )
    return blocks


def objective_choice_candidate_windows(
    blocks: list[dict[str, Any]], image_width: int, image_height: int
) -> list[dict[str, Any]]:
    """Crop the answer cell immediately after an OCR-detected opening parenthesis."""

    windows: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for block in blocks:
        text = str(block.get("text_raw") or "").strip()
        if not re.search(r"[（(]\s*$", text):
            continue
        box = _normalize_box(block.get("box"))
        if box is None:
            continue
        _x1, y1, x2, y2 = box
        line_height = max(1.0, y2 - y1)
        left = max(0, round(x2 - line_height * 0.08))
        top = max(0, round(y1 - line_height * 0.38))
        right = min(image_width, round(x2 + line_height * 2.86))
        bottom = min(image_height, round(y2 + line_height * 0.63))
        candidate = (left, top, right, bottom)
        if right - left < 24 or bottom - top < 24 or candidate in seen:
            continue
        seen.add(candidate)
        windows.append(
            {
                "box": list(candidate),
                "anchor_text": text,
            }
        )
    return windows


def _objective_choice_label(text: str) -> str:
    value = re.sub(r"\s+", "", str(text or "").upper())
    match = re.fullmatch(r"(?:答案[:：]?)?[（(\[【]?([A-H])[）)\]】。，,.]?", value)
    return match.group(1) if match else ""


def _paddle_json_value(result: Any) -> dict[str, Any]:
    value = getattr(result, "json", result)
    if callable(value):
        value = value()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _recognize_objective_choice(
    image: Any,
    detected_blocks: list[dict[str, Any]],
    processed_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return only a unique A-H mark, with a focused recognition pass after "("."""

    import cv2
    from paddleocr import TextRecognition

    height, width = image.shape[:2]
    windows = objective_choice_candidate_windows(detected_blocks, width, height)
    candidates: list[dict[str, Any]] = []
    for block in detected_blocks:
        label = _objective_choice_label(block.get("text_raw", ""))
        if label:
            candidates.append(
                {
                    "text_raw": label,
                    "confidence": float(block.get("confidence") or 0),
                    "box": block.get("box"),
                    "source": "standalone_ocr_block",
                }
            )

    extraction: dict[str, Any] = {
        "method": "standalone_choice_or_open_parenthesis_crop_v1",
        "candidate_windows": [],
    }
    if windows:
        recognizer = TextRecognition(
            model_name="PP-OCRv5_server_rec",
            device="gpu:0",
        )
        for index, window in enumerate(windows, start=1):
            left, top, right, bottom = window["box"]
            crop = image[top:bottom, left:right]
            crop_path = processed_path.parent / f"objective_choice_{index:02d}.png"
            if crop.size == 0 or not cv2.imwrite(str(crop_path), crop):
                continue
            raw_results = list(recognizer.predict(input=str(crop_path), batch_size=1))
            raw_value = _paddle_json_value(raw_results[0]) if raw_results else {}
            value = raw_value.get("res", raw_value)
            if not isinstance(value, dict):
                value = {}
            raw_text = str(value.get("rec_text") or "").strip()
            try:
                confidence = float(value.get("rec_score") or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            label = _objective_choice_label(raw_text)
            extraction["candidate_windows"].append(
                {
                    "box": window["box"],
                    "anchor_text": window["anchor_text"],
                    "text": raw_text,
                    "confidence": confidence,
                    "choice": label or None,
                }
            )
            if label and confidence >= 0.70:
                candidates.append(
                    {
                        "text_raw": label,
                        "confidence": confidence,
                        "box": window["box"],
                        "source": "focused_objective_choice_recognition",
                    }
                )

    best_by_label: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        label = candidate["text_raw"]
        if label not in best_by_label or candidate["confidence"] > best_by_label[label]["confidence"]:
            best_by_label[label] = candidate
    blocks = [
        {
            "block_index": index,
            "text_raw": candidate["text_raw"],
            "confidence": candidate["confidence"],
            "box": candidate["box"],
            "source": candidate["source"],
        }
        for index, candidate in enumerate(best_by_label.values(), start=1)
    ]
    extraction["recognized_choices"] = [block["text_raw"] for block in blocks]
    return blocks, extraction


def _box_overlap_ratio(left_box: Any, right_box: Any) -> float:
    left = _normalize_box(left_box)
    right = _normalize_box(right_box)
    if left is None or right is None:
        return 0.0
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    smallest_area = min(left_area, right_area)
    return intersection / smallest_area if smallest_area else 0.0


def _merge_math_blocks(text_blocks: list[dict[str, Any]], formula_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并中文题干和公式结果，避免数学模式只留下 TexTeller 公式。"""

    formula_boxes = [item.get("box") for item in formula_blocks if item.get("box")]
    merged: list[dict[str, Any]] = []
    for block in text_blocks:
        text = str(block.get("text_raw") or "").strip()
        if not text:
            continue
        has_chinese = any("\u3400" <= char <= "\u9fff" for char in text)
        # 公式候选与纯公式 OCR 块高度重叠时用 TexTeller 替换；
        # 含中文的混合行必须保留，避免“设 x 为……”中的中文被一起删掉。
        if not has_chinese and any(_box_overlap_ratio(block.get("box"), box) >= 0.75 for box in formula_boxes):
            continue
        merged.append({**block, "source": "paddleocr_text"})
    merged.extend({**block, "source": "texteller_formula"} for block in formula_blocks if str(block.get("text_raw") or "").strip())
    merged = sort_ocr_blocks_reading_order(merged)
    return [{**item, "block_index": index} for index, item in enumerate(merged, start=1)]


def _paddle_engine(
    mode: str,
    input_path: Path,
    processed_path: Path,
    output_path: Path,
    subject: str,
    objective_choice: bool = False,
) -> dict[str, Any]:
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    import cv2
    import paddle

    helpers = _load_paddle_helpers()
    image = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"图片无法读取：{input_path}")
    prepared, preprocess_info = helpers["prepare_image"](image, False)
    if mode == "chinese_handwriting":
        prepared, red_info = helpers["suppress_red_ink"](prepared, adaptive=True)
        preprocess_info["red_annotation"] = red_info
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(processed_path), prepared):
        raise RuntimeError(f"预处理图片保存失败：{processed_path}")

    # 印刷体题目可能同时包含中文和英文。PP-OCR 的中文模型覆盖中文、英文、
    # 数字和常见符号；英语学科不能再把整页切成 en，否则中文说明会直接丢失。
    language = "ch"
    ocr = helpers["PaddleOCR"](
        ocr_version="PP-OCRv5",
        lang=language,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="gpu",
    )
    result, _raw = helpers["run_prediction"](ocr, processed_path)
    ordering_info: dict[str, Any] | None = None
    if mode == "chinese_handwriting":
        result, ordering_info = helpers["order_result_by_rows"](result)
    texts = result.get("rec_texts", [])
    scores = result.get("rec_scores", [])
    boxes = result.get("rec_boxes_prepared", result.get("rec_boxes", []))
    blocks = _recognized_text_blocks(texts, scores, boxes)
    choice_extraction = None
    if objective_choice:
        blocks, choice_extraction = _recognize_objective_choice(prepared, blocks, processed_path)
    return {
        "status": "succeeded" if blocks else "empty",
        "engine_name": "paddleocr",
        "preprocess": preprocess_info,
        "ordering": ordering_info,
        "device": paddle.device.get_device(),
        "blocks": blocks,
        "objective_choice_extraction": choice_extraction,
    }


def _math_candidates(input_path: Path, processed_path: Path, output_path: Path, subject: str) -> dict[str, Any]:
    """用 PaddleOCR 做页面预处理和公式候选裁剪，不把整页送入 TexTeller。"""

    import cv2

    helpers = _load_paddle_helpers()
    image = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"图片无法读取：{input_path}")
    prepared, preprocess_info = helpers["prepare_image"](image, False)
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(processed_path), prepared):
        raise RuntimeError(f"预处理图片保存失败：{processed_path}")
    language = "ch"
    ocr = helpers["PaddleOCR"](
        ocr_version="PP-OCRv5",
        lang=language,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="gpu",
    )
    result, _raw = helpers["run_prediction"](ocr, processed_path)
    texts = list(result.get("rec_texts", []))
    scores = list(result.get("rec_scores", []))
    boxes = list(result.get("rec_boxes", []))
    text_blocks = _recognized_text_blocks(texts, scores, boxes)
    assignments = helpers["build_question_segments"](texts, scores, boxes, prepared.shape[1])[1]
    indexes, sources = helpers["formula_candidate_indexes"](texts, scores, boxes, policy="relaxed")
    representatives, members, representative_sources = helpers["collapse_formula_candidates"](
        indexes, boxes, scores=scores, sources=sources
    )
    selected = helpers["select_candidates"](texts, representatives, assignments, limit=64)
    candidate_root = processed_path.parent / "formula_candidates"
    candidate_root.mkdir(parents=True, exist_ok=True)
    candidates = []
    for rank, index in enumerate(selected, start=1):
        member_indexes = members.get(index, [index])
        candidate_box = helpers["union_formula_boxes"](member_indexes, boxes)
        if not candidate_box:
            continue
        left, top, right, bottom = helpers["crop_bounds"](
            candidate_box, boxes, prepared.shape[1], prepared.shape[0], merge_same_line=False
        )
        crop = prepared[top:bottom, left:right]
        crop, trim_info = helpers["trim_formula_crop"](crop, texts[index] if index < len(texts) else "")
        if crop.size == 0:
            continue
        crop_path = candidate_root / f"candidate_{rank:03d}.png"
        if not cv2.imwrite(str(crop_path), crop):
            continue
        candidates.append(
            {
                "candidate_index": rank,
                "source_index": index,
                "source_indexes": member_indexes,
                "candidate_source": representative_sources.get(index, "unknown"),
                "ocr_text_hint": texts[index] if index < len(texts) else "",
                "ocr_confidence": float(scores[index]) if index < len(scores) else None,
                "box": [left, top, right, bottom],
                "trim": trim_info,
                "crop_path": str(crop_path.resolve()),
                "question_hint": assignments.get(index, {}).get("question_id"),
            }
        )
    result = {
        "status": "succeeded" if candidates or text_blocks else "empty",
        "engine_name": "paddleocr_candidate_extractor",
        "preprocess": preprocess_info,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "text_blocks": text_blocks,
    }
    _write_json(output_path, result)
    return result


def _runaway(text: str, threshold: int = 600) -> bool:
    value = str(text or "").strip()
    grams = Counter(value[index:index + 4] for index in range(max(0, len(value) - 3)))
    return bool(
        value
        and (
            len(value) >= threshold
            or max(grams.values(), default=0) >= 20
            or value.count("\\begin{array") >= 3
            or value.count("\\begin { array") >= 3
            or value.count("\\text") >= 40
        )
    )


def _math_recognize(manifest_path: Path, output_path: Path, model_dir: Path) -> dict[str, Any]:
    import cv2
    import torch
    from texteller import img2latex, load_model, load_tokenizer

    if not torch.cuda.is_available():
        raise RuntimeError("TexTeller profile requires CUDA, but CUDA is unavailable")
    device = torch.device("cuda")
    model = load_model(str(model_dir.resolve()), use_onnx=False).to(device)
    tokenizer = load_tokenizer(str(model_dir.resolve()))
    model.eval()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    predictions = []
    for item in manifest.get("candidates", []):
        image = cv2.imread(str(item["crop_path"]), cv2.IMREAD_COLOR)
        if image is None:
            predictions.append({**item, "prediction": "", "status": "failed", "error": "CROP_READ_FAILED"})
            continue
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        attempts = []
        try:
            with torch.inference_mode():
                prediction = img2latex(
                    model,
                    tokenizer,
                    [image],
                    device=device,
                    out_format="latex",
                    keep_style=False,
                    max_tokens=256,
                    num_beams=3,
                    no_repeat_ngram_size=0,
                )[0].strip()
            attempts.append({"max_tokens": 256, "num_beams": 3, "no_repeat_ngram_size": 0, "prediction": prediction})
            if _runaway(prediction):
                with torch.inference_mode():
                    retry = img2latex(
                        model,
                        tokenizer,
                        [image],
                        device=device,
                        out_format="latex",
                        keep_style=False,
                        max_tokens=256,
                        num_beams=3,
                        no_repeat_ngram_size=3,
                    )[0].strip()
                attempts.append({"max_tokens": 256, "num_beams": 3, "no_repeat_ngram_size": 3, "prediction": retry})
                prediction = retry
            status = "invalid_long_output" if _runaway(prediction) else ("ok" if prediction else "empty")
            predictions.append({**item, "prediction": prediction, "status": status, "attempts": attempts})
        except Exception as exc:
            predictions.append({**item, "prediction": "", "status": "failed", "attempts": attempts, "error": f"{type(exc).__name__}: {exc}"})
    blocks = [
        {
            "block_index": index,
            "text_raw": item["prediction"],
            "confidence": None,
            "box": item.get("box"),
            "question_hint": item.get("question_hint"),
            "candidate_index": item.get("candidate_index"),
        }
        for index, item in enumerate(predictions, start=1)
        if str(item.get("prediction", "")).strip() and item.get("status") not in {"failed", "invalid_long_output"}
    ]
    text_blocks = list(manifest.get("text_blocks") or [])
    merged_blocks = _merge_math_blocks(text_blocks, blocks)
    result = {
        "status": "succeeded" if merged_blocks else "empty",
        "engine_name": "texteller",
        "engine_version": "TexTeller:texteller_model",
        "parameters": {"device": "cuda", "max_tokens": 256, "num_beams": 3, "no_repeat_ngram_size": 0},
        "candidate_count": len(manifest.get("candidates", [])),
        "predictions": predictions,
        "text_blocks": text_blocks,
        "formula_blocks": blocks,
        "blocks": merged_blocks,
    }
    _write_json(output_path, result)
    return result


def _english(input_path: Path, processed_path: Path, output_path: Path, model_dir: Path) -> dict[str, Any]:
    tool_root = _ocr_tool_root()
    if str(tool_root) not in sys.path:
        sys.path.insert(0, str(tool_root))
    import cv2
    import torch
    from optimize_english_two_datasets_pycharm import prepare_optimized_crop
    from run_user_neat_english_optimized_pycharm import (
        choose_line_boxes,
        line_ink_ratio,
        recognize_conservative,
        safe_rectify,
        tighten_crop,
    )
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    image = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"图片无法读取：{input_path}")
    rectified, rectify_mode = safe_rectify(image)
    enhanced = __import__("run_user_neat_enhanced_first_pycharm", fromlist=["remove_ruling_lines"]).remove_ruling_lines(rectified)
    boxes, line_detection_mode = choose_line_boxes(enhanced)
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(processed_path), enhanced):
        raise RuntimeError(f"预处理图片保存失败：{processed_path}")
    crops = []
    saved_boxes = []
    for box in boxes:
        left, top, right, bottom = box
        crop = tighten_crop(enhanced[top:bottom, left:right])
        if crop.shape[1] < 30 or crop.shape[0] < 12 or line_ink_ratio(crop) < 0.003:
            continue
        saved_boxes.append(list(box))
        crops.append(prepare_optimized_crop(crop, tal_line=False))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = TrOCRProcessor.from_pretrained(str(model_dir.resolve()), local_files_only=True, use_fast=False)
    model = VisionEncoderDecoderModel.from_pretrained(str(model_dir.resolve()), local_files_only=True).to(device)
    model.eval()
    line_results = recognize_conservative(crops, processor, model, device)
    blocks = []
    for index, (box, item) in enumerate(zip(saved_boxes, line_results), start=1):
        text = str(item.get("raw_text", "")).strip()
        if not text:
            continue
        blocks.append(
            {
                "block_index": index,
                "text_raw": text,
                "confidence": item.get("mean_token_confidence"),
                "box": box,
                "conservative_text": item.get("conservative_text", ""),
                "ink_ratio": item.get("ink_ratio"),
            }
        )
    result = {
        "status": "succeeded" if blocks else "empty",
        "engine_name": "trocr",
        "engine_version": "microsoft/trocr-large-handwritten",
        "preprocess": {
            "rectify_mode": rectify_mode,
            "line_detection_mode": line_detection_mode,
            "line_count": len(saved_boxes),
            "batch_size": 1,
            "gray_clahe_to_rgb": True,
        },
        "device": device,
        "blocks": blocks,
    }
    _write_json(output_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("printed", "chinese_handwriting", "english_handwriting", "math_candidates", "math_recognize"), required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--processed", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subject", default="chinese")
    parser.add_argument("--objective-choice", action="store_true", help="对客观题答案括号执行单字母专项识别")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    if args.mode == "math_recognize":
        if not args.manifest or not args.model_dir:
            parser.error("math_recognize 需要 --manifest 和 --model-dir")
        result = _math_recognize(args.manifest.resolve(), args.output.resolve(), args.model_dir.resolve())
    else:
        if not args.input or not args.processed:
            parser.error("该识别模式需要 --input 和 --processed")
        input_path = args.input.resolve()
        processed_path = args.processed.resolve()
        output_path = args.output.resolve()
        if args.mode in {"printed", "chinese_handwriting"}:
            result = _paddle_engine(
                args.mode,
                input_path,
                processed_path,
                output_path,
                args.subject,
                objective_choice=args.objective_choice,
            )
            _write_json(output_path, result)
        elif args.mode == "math_candidates":
            result = _math_candidates(input_path, processed_path, output_path, args.subject)
        else:
            if not args.model_dir:
                parser.error("english_handwriting 需要 --model-dir")
            result = _english(input_path, processed_path, output_path, args.model_dir.resolve())
    print(json.dumps({"status": result.get("status"), "blocks": len(result.get("blocks", [])), "output": str(args.output.resolve())}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
