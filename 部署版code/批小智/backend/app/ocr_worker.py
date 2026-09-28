"""持久化 OCR Worker。

HTTP 接口只负责把 OCR 任务和 ``OcrRun`` 记录写入数据库；本模块负责领取
``queued`` 任务，在已经确认的 OCR 专用环境中执行“预处理 → 区域提取 →
模型识别”，并把结果写回 ``ocr_blocks``、页面状态和原始结果文件。

默认按本地 Windows 单 Worker 运行。SQLite 不提供跨进程的行级锁，因此生产
部署切换到并发 Worker 前，必须按技术文档迁移到支持行锁的数据库并重新验证
任务领取语义。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from PIL import Image

# 允许从项目根目录直接执行 backend/app/ocr_worker.py。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.ocr import OCR_PROFILES, OcrProfile
from app.core.config import get_settings
from app.core.utils import new_id, parse_utc, utc_now
from app.db.session import SessionLocal
from app.models import AnswerPhoto, AssignmentBatch, OcrBlock, OcrRun, SourceFile, SourcePage, Task
from app.ocr_reading_order import sort_ocr_blocks_reading_order
from app.question_layout_validation import question_layout_region_issues
from app.workflows.ocr_graph import run_ocr_workflow


settings = get_settings()
logger = logging.getLogger("pixiaozhi.ocr_worker")
STALE_TASK_AFTER_SECONDS = 300


class WorkerError(RuntimeError):
    """可持久化到任务和 OcrRun 的受控错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RuntimePaths:
    paddle_python: Path
    trocr_python: Path
    texteller_python: Path
    runtime_script: Path
    trocr_model: Path
    texteller_model: Path


def _tool_root() -> Path:
    configured = os.getenv("OCR_TOOL_ROOT")
    return Path(configured).expanduser().resolve() if configured else PROJECT_ROOT / "OCR工具测试"


def _runtime_paths() -> RuntimePaths:
    root = _tool_root()
    return RuntimePaths(
        paddle_python=Path(os.getenv("OCR_PADDLE_PYTHON", str(root / ".venv" / "Scripts" / "python.exe"))).resolve(),
        trocr_python=Path(os.getenv("OCR_TROCR_PYTHON", str(root / ".venv_trocr" / "Scripts" / "python.exe"))).resolve(),
        texteller_python=Path(os.getenv("OCR_TEXTELLER_PYTHON", str(root / ".conda_texteller" / "python.exe"))).resolve(),
        runtime_script=(PROJECT_ROOT / "backend" / "scripts" / "ocr_runtime.py").resolve(),
        trocr_model=Path(
            os.getenv("OCR_TROCR_MODEL", str(root / "models" / "trocr-large-handwritten"))
        ).resolve(),
        texteller_model=Path(os.getenv("OCR_TEXTELLER_MODEL", str(root / "texteller_model"))).resolve(),
    )


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _storage_root() -> Path:
    root = Path(settings.storage_root)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path_for_storage_key(storage_key: str) -> Path:
    root = _storage_root()
    candidate = (root / storage_key).resolve()
    if root != candidate and root not in candidate.parents:
        raise WorkerError("INVALID_STORAGE_KEY", "页面存储路径不在配置的存储根目录内")
    return candidate


def _parse_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _tail(value: str, limit: int = 2000) -> str:
    compact = (value or "").strip()
    return compact[-limit:] if compact else "无额外输出"


def _validate_profile_runtime(profile: OcrProfile, paths: RuntimePaths) -> None:
    interpreter = {
        "paddleocr": paths.paddle_python,
        "trocr": paths.trocr_python,
        "texteller": paths.texteller_python,
    }[profile.engine_name]
    if not interpreter.is_file():
        raise WorkerError("OCR_RUNTIME_NOT_FOUND", f"未找到 {profile.engine_name} 运行环境：{interpreter}")
    if not paths.runtime_script.is_file():
        raise WorkerError("OCR_RUNTIME_SCRIPT_NOT_FOUND", f"未找到 OCR 运行脚本：{paths.runtime_script}")
    if profile.mode == "english_handwriting" and not paths.trocr_model.is_dir():
        raise WorkerError("OCR_MODEL_NOT_FOUND", f"未找到 TrOCR large 模型目录：{paths.trocr_model}")
    if profile.mode == "math_handwriting" and not paths.texteller_model.is_dir():
        raise WorkerError("OCR_MODEL_NOT_FOUND", f"未找到 TexTeller 模型目录：{paths.texteller_model}")


def _run_runtime(command: list[str], output_path: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PADDLE_PDX_MODEL_SOURCE": "BOS",
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
            "OCR_TOOL_ROOT": str(_tool_root()),
        }
    )
    timeout = _positive_int_env("OCR_WORKER_TIMEOUT_SECONDS", 900)
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerError("OCR_TIMEOUT", f"OCR 运行超过 {timeout} 秒，已终止当前页面识别") from exc
    except OSError as exc:
        raise WorkerError("OCR_PROCESS_START_FAILED", f"无法启动 OCR 运行环境：{exc}") from exc
    if completed.returncode != 0:
        details = _tail(completed.stderr or completed.stdout)
        raise WorkerError("OCR_RUNTIME_FAILED", f"OCR 运行失败（退出码 {completed.returncode}）：{details}")
    if not output_path.is_file():
        raise WorkerError("OCR_OUTPUT_MISSING", f"OCR 运行结束但未生成结果文件：{output_path}")
    try:
        result = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerError("OCR_OUTPUT_INVALID", f"OCR 结果文件无法解析：{output_path}") from exc
    if not isinstance(result, dict):
        raise WorkerError("OCR_OUTPUT_INVALID", "OCR 结果必须是 JSON 对象")
    return result


def _command_base(interpreter: Path, runtime_script: Path) -> list[str]:
    return [str(interpreter), "-u", str(runtime_script)]


def _execute_run(
    profile: OcrProfile,
    source_path: Path,
    processed_path: Path,
    raw_path: Path,
    subject: str,
    paths: RuntimePaths,
    objective_choice: bool = False,
) -> dict[str, Any]:
    _validate_profile_runtime(profile, paths)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if profile.mode in {"printed", "chinese_handwriting"}:
        command = _command_base(paths.paddle_python, paths.runtime_script) + [
            "--mode",
            profile.mode,
            "--input",
            str(source_path),
            "--processed",
            str(processed_path),
            "--output",
            str(raw_path),
            "--subject",
            subject,
        ]
        if objective_choice:
            command.append("--objective-choice")
        return _run_runtime(command, raw_path)

    if profile.mode == "english_handwriting":
        command = _command_base(paths.trocr_python, paths.runtime_script) + [
            "--mode",
            profile.mode,
            "--input",
            str(source_path),
            "--processed",
            str(processed_path),
            "--output",
            str(raw_path),
            "--model-dir",
            str(paths.trocr_model),
        ]
        return _run_runtime(command, raw_path)

    manifest_path = raw_path.parent / "candidate_manifest.json"
    candidate_command = _command_base(paths.paddle_python, paths.runtime_script) + [
        "--mode",
        "math_candidates",
        "--input",
        str(source_path),
        "--processed",
        str(processed_path),
        "--output",
        str(manifest_path),
        "--subject",
        subject,
    ]
    manifest = _run_runtime(candidate_command, manifest_path)
    if not manifest.get("candidates"):
        text_blocks = list(manifest.get("text_blocks") or [])
        if text_blocks:
            fallback = {
                "status": "succeeded",
                "engine_name": "paddleocr_text_fallback",
                "engine_version": "PP-OCRv5_server_det+PP-OCRv5_server_rec",
                "parameters": {"language_scope": "zh+en", "formula_candidates": 0},
                "blocks": text_blocks,
                "text_blocks": text_blocks,
                "formula_blocks": [],
                "candidate_extraction": {
                    "engine_name": manifest.get("engine_name"),
                    "preprocess": manifest.get("preprocess"),
                    "candidate_count": 0,
                },
            }
            raw_path.write_text(json.dumps(fallback, ensure_ascii=False, indent=2), encoding="utf-8")
            return fallback
        raise WorkerError("OCR_NO_FORMULA_CANDIDATES", "页面未提取到可送入 TexTeller 的公式候选区域")
    recognize_command = _command_base(paths.texteller_python, paths.runtime_script) + [
        "--mode",
        "math_recognize",
        "--manifest",
        str(manifest_path),
        "--output",
        str(raw_path),
        "--model-dir",
        str(paths.texteller_model),
    ]
    try:
        result = _run_runtime(recognize_command, raw_path)
    except WorkerError as exc:
        text_blocks = list(manifest.get("text_blocks") or [])
        if not text_blocks:
            raise
        # TexTeller 不可用时仍保留中文/数字题干，不让整页 OCR 一起失败。
        result = {
            "status": "succeeded",
            "engine_name": "paddleocr_text_fallback",
            "engine_version": "PP-OCRv5_server_det+PP-OCRv5_server_rec",
            "parameters": {"language_scope": "zh+en", "formula_fallback_error": str(exc)},
            "blocks": text_blocks,
            "text_blocks": text_blocks,
            "formula_blocks": [],
            "candidate_extraction": {
                "engine_name": manifest.get("engine_name"),
                "preprocess": manifest.get("preprocess"),
                "candidate_count": manifest.get("candidate_count", 0),
            },
        }
        raw_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["candidate_extraction"] = {
        "engine_name": manifest.get("engine_name"),
        "preprocess": manifest.get("preprocess"),
        "candidate_count": manifest.get("candidate_count", 0),
    }
    _write_json(raw_path, result)
    return result


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _box_coordinates(box: Any) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    if not isinstance(box, (list, tuple)) or len(box) < 4:
        return None, None, None, None
    return _decimal(box[0]), _decimal(box[1]), _decimal(box[2]), _decimal(box[3])


def _pdf_renderer() -> Path | None:
    configured = os.getenv("PDF_POPPLER_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_dir():
            configured_path = configured_path / ("pdftoppm.exe" if os.name == "nt" else "pdftoppm")
        if configured_path.is_file():
            return configured_path.resolve()
        return None
    discovered = shutil.which("pdftoppm")
    return Path(discovered).resolve() if discovered else None


def _render_pdf_page(source_path: Path, page: SourcePage, source_file: SourceFile, output_path: Path) -> tuple[Path, dict[str, Any]]:
    if page.page_index < 1 or page.page_index > source_file.page_count:
        raise WorkerError(
            "PDF_RENDER_INVALID_PAGE",
            f"PDF 页面序号无效：第 {page.page_index} 页，文件共 {source_file.page_count} 页",
        )
    renderer = _pdf_renderer()
    if renderer is None:
        raise WorkerError(
            "PDF_RENDER_UNAVAILABLE",
            "未找到 pdftoppm；请安装 Poppler，或通过 PDF_POPPLER_PATH 配置其目录后重试",
        )
    dpi = _positive_int_env("OCR_PDF_DPI", 150)
    timeout = _positive_int_env("OCR_PDF_RENDER_TIMEOUT_SECONDS", 120)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    generated_path = output_path.with_suffix(".png")
    generated_path.unlink(missing_ok=True)
    command = [
        str(renderer),
        "-png",
        "-r",
        str(dpi),
        "-f",
        str(page.page_index),
        "-l",
        str(page.page_index),
        "-singlefile",
        str(source_path),
        str(output_path.with_suffix("")),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerError("PDF_RENDER_TIMEOUT", f"PDF 第 {page.page_index} 页栅格化超过 {timeout} 秒") from exc
    except OSError as exc:
        raise WorkerError("PDF_RENDER_START_FAILED", f"无法启动 PDF 页面栅格化工具：{exc}") from exc
    if completed.returncode != 0:
        details = _tail(completed.stderr or completed.stdout)
        raise WorkerError("PDF_RENDER_FAILED", f"PDF 第 {page.page_index} 页栅格化失败（退出码 {completed.returncode}）：{details}")
    if not generated_path.is_file():
        raise WorkerError("PDF_RENDER_OUTPUT_MISSING", f"PDF 栅格化结束但未生成页面图片：{generated_path}")
    with Image.open(generated_path) as rendered:
        width, height = rendered.size
    return generated_path, {
        "input_type": "pdf",
        "page_index": page.page_index,
        "dpi": dpi,
        "renderer": "pdftoppm",
        "width": width,
        "height": height,
    }


def _source_path(page: SourcePage, source_file: SourceFile, rendered_path: Path) -> tuple[Path, dict[str, Any]]:
    path = _path_for_storage_key(page.original_storage_key)
    if not path.is_file():
        raise WorkerError("SOURCE_FILE_NOT_FOUND", f"原始页面文件不存在：{path}")
    if source_file.extension.lower() == ".pdf" or source_file.mime_type == "application/pdf":
        return _render_pdf_page(path, page, source_file, rendered_path)
    with Image.open(path) as image:
        width, height = image.size
    return path, {"input_type": "image", "width": width, "height": height}


def _save_blocks(
    db: Session,
    run: OcrRun,
    page: SourcePage,
    blocks: Any,
    now: str,
    *,
    question_id: str | None = None,
    block_offset: int = 0,
) -> int:
    if not isinstance(blocks, list):
        return 0
    saved = 0
    for item in sort_ocr_blocks_reading_order(blocks):
        if not isinstance(item, dict):
            continue
        text_raw = str(item.get("text_raw") or "").strip()
        if not text_raw:
            continue
        x1, y1, x2, y2 = _box_coordinates(item.get("box"))
        db.add(
            OcrBlock(
                id=new_id("ocr_block"),
                ocr_run_id=run.id,
                source_page_id=page.id,
                block_index=block_offset + saved + 1,
                text_raw=text_raw,
                confidence=_decimal(item.get("confidence")),
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                question_id=question_id,
                created_at=now,
            )
        )
        saved += 1
    return saved


def _adjust_region_blocks(blocks: Any, left: int, top: int) -> list[dict[str, Any]]:
    """将区域 OCR 返回的局部坐标还原为原页面坐标。"""

    adjusted: list[dict[str, Any]] = []
    for item in blocks if isinstance(blocks, list) else []:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        box = item.get("box")
        if isinstance(box, (list, tuple)) and len(box) >= 4:
            try:
                copied["box"] = [float(box[0]) + left, float(box[1]) + top, float(box[2]) + left, float(box[3]) + top]
            except (TypeError, ValueError):
                copied["box"] = None
        adjusted.append(copied)
    return adjusted


def _process_question_layout_regions(
    *,
    db: Session,
    run: OcrRun,
    page: SourcePage,
    profile: OcrProfile,
    source_path: Path,
    raw_path: Path,
    processed_path: Path,
    subject: str,
    layout: dict[str, Any],
    question_types: dict[str, str],
    paths: RuntimePaths,
) -> tuple[dict[str, Any], int]:
    """按已确认题目模板逐区域识别学生页，并把 block 绑定到题目。"""

    regions = [
        item
        for item in layout.get("regions", [])
        if isinstance(item, dict) and int(item.get("page_index") or 0) == page.page_index
    ]
    if not regions:
        raise WorkerError("QUESTION_LAYOUT_PAGE_NOT_FOUND", f"学生第 {page.page_index} 页没有对应的题目版式区域")
    try:
        source_image = Image.open(source_path).convert("RGB")
    except (OSError, ValueError) as exc:
        raise WorkerError("QUESTION_LAYOUT_IMAGE_INVALID", "学生页面无法读取，不能按题目区域识别") from exc

    width, height = source_image.size
    region_results: list[dict[str, Any]] = []
    all_blocks: list[dict[str, Any]] = []
    blocks_saved = 0
    try:
        for index, region in enumerate(regions, start=1):
            left = max(0, min(width - 1, round(float(region.get("x1", 0)) * width)))
            top = max(0, min(height - 1, round(float(region.get("y1", 0)) * height)))
            right = max(left + 1, min(width, round(float(region.get("x2", 1)) * width)))
            bottom = max(top + 1, min(height, round(float(region.get("y2", 1)) * height)))
            region_dir = raw_path.parent / "regions" / f"region_{index:03d}"
            region_input = region_dir / "input.png"
            region_processed = region_dir / "processed.png"
            region_raw = region_dir / "raw.json"
            region_dir.mkdir(parents=True, exist_ok=True)
            source_image.crop((left, top, right, bottom)).save(region_input, format="PNG")
            question_id = str(region.get("question_id") or "")
            is_objective = question_types.get(question_id) == "objective"
            region_profile = OCR_PROFILES["chinese_handwriting"] if is_objective else profile
            workflow_state = run_ocr_workflow(
                profile=region_profile,
                source_path=region_input,
                processed_path=region_processed,
                raw_path=region_raw,
                subject=subject,
                page_index=page.page_index,
                execute_engine=lambda: _execute_run(
                    profile,
                    region_input,
                    region_processed,
                    region_raw,
                    subject,
                    paths,
                    objective_choice=is_objective,
                ),
            )
            result = workflow_state["result"]
            adjusted_blocks = sort_ocr_blocks_reading_order(_adjust_region_blocks(result.get("blocks"), left, top))
            all_blocks.extend(adjusted_blocks)
            saved = _save_blocks(
                db,
                run,
                page,
                adjusted_blocks,
                utc_now(),
                question_id=question_id or None,
                block_offset=blocks_saved,
            )
            blocks_saved += saved
            region_results.append(
                {
                    "question_id": region.get("question_id"),
                    "question_no": region.get("question_no"),
                    "question_type": "objective" if is_objective else "subjective",
                    "recognition_mode": region_profile.mode,
                    "box": [left, top, right, bottom],
                    "blocks": saved,
                    "objective_choice_extraction": result.get("objective_choice_extraction"),
                    "workflow": {
                        "trace": workflow_state.get("trace", []),
                        "stage": workflow_state.get("stage"),
                        "anomalies": workflow_state.get("anomalies", []),
                    },
                }
            )
    finally:
        source_image.close()
    aggregate = {
        "status": "succeeded",
        "engine_name": profile.engine_name,
        "engine_version": profile.engine_version,
        "device": None,
        "blocks": all_blocks,
        "layout_first": {
            "layout_id": layout.get("id"),
            "layout_version": layout.get("version"),
            "region_count": len(regions),
            "page_width": width,
            "page_height": height,
            "regions": [
                {
                    "question_id": item.get("question_id"),
                    "question_no": item.get("question_no"),
                    "box": item.get("box"),
                    "blocks": item.get("blocks"),
                }
                for item in region_results
            ],
            "empty_answer_regions": [
                {"question_id": item.get("question_id"), "question_no": item.get("question_no")}
                for item in region_results
                if item.get("blocks") == 0
            ],
        },
        "region_results": region_results,
    }
    _write_json(raw_path, aggregate)
    return aggregate, blocks_saved


def _transform_payload(
    profile: OcrProfile,
    result: dict[str, Any],
    source_render: dict[str, Any],
    workflow_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "recognition_mode": profile.mode,
        "engine_name": profile.engine_name,
        "engine_version": profile.engine_version,
        "preprocess_version": profile.preprocess_version,
        "parameters": profile.parameters,
        "runtime_device": result.get("device"),
        "preprocess": result.get("preprocess"),
        "candidate_extraction": result.get("candidate_extraction"),
        "layout_first": result.get("layout_first"),
        "source_render": source_render,
        "workflow": {
            "name": "ocr_pipeline",
            "engine": "LangGraph",
            "trace": (workflow_state or {}).get("trace", []),
            "stage": (workflow_state or {}).get("stage"),
            "awaiting_teacher_correction": (workflow_state or {}).get("awaiting_teacher_correction", False),
            "anomalies": (workflow_state or {}).get("anomalies", []),
        },
    }


def _process_run(db: Session, run: OcrRun, task: Task, batch: AssignmentBatch, mode: str) -> tuple[bool, dict[str, str]]:
    profile = OCR_PROFILES.get(mode)
    if profile is None:
        return False, {"ocr_run_id": run.id, "code": "OCR_PROFILE_NOT_FOUND", "message": f"未找到识别模式：{mode}"}
    page = db.get(SourcePage, run.source_page_id)
    source_file = db.get(SourceFile, page.source_file_id) if page else None
    if page is None or source_file is None:
        return False, {"ocr_run_id": run.id, "code": "OCR_PAGE_NOT_FOUND", "message": "OCR 运行关联的页面或源文件不存在"}

    now = utc_now()
    run.status = "running"
    run.started_at = run.started_at or now
    run.error_code = None
    page.status = "ocr_processing"
    page.updated_at = now
    db.commit()

    raw_key = f"ocr/{run.id}/raw.json"
    source_input_key = f"ocr/{run.id}/source.png"
    processed_key = f"ocr/{run.id}/processed.png"
    raw_path = _path_for_storage_key(raw_key)
    source_input_path = _path_for_storage_key(source_input_key)
    processed_path = _path_for_storage_key(processed_key)
    task_options = _parse_json(task.options_json, {})
    layout = task_options.get("question_layout") if isinstance(task_options, dict) else None
    if not isinstance(layout, dict) or layout.get("status") != "confirmed":
        layout = None
    try:
        if layout is not None:
            layout_issues = question_layout_region_issues(layout.get("regions", []))
            if layout_issues:
                raise WorkerError(
                    "QUESTION_LAYOUT_REGIONS_OVERLAP",
                    "已保存的题目区域彼此重叠，系统已停止本页识别以防其他题答案串入；请修正模板后重新识别。",
                )
        source_path, source_render = _source_path(page, source_file, source_input_path)
        if layout is not None and task_options.get("file_role") == "student_work":
            result, blocks_saved = _process_question_layout_regions(
                db=db,
                run=run,
                page=page,
                profile=profile,
                source_path=source_path,
                raw_path=raw_path,
                processed_path=processed_path,
                subject=batch.subject,
                layout=layout,
                question_types={question.id: question.question_type for question in batch.questions},
                paths=_runtime_paths(),
            )
            workflow_state = {
                "trace": ["layout_first_recognition", "persist_page_state"],
                "stage": "persist_page_state",
                "awaiting_teacher_correction": True,
                "anomalies": [],
            }
        else:
            workflow_state = run_ocr_workflow(
                profile=profile,
                source_path=source_path,
                processed_path=processed_path,
                raw_path=raw_path,
                subject=batch.subject,
                page_index=page.page_index,
                execute_engine=lambda: _execute_run(
                    profile,
                    source_path,
                    processed_path,
                    raw_path,
                    batch.subject,
                    _runtime_paths(),
                ),
            )
            result = workflow_state["result"]
            blocks_saved = _save_blocks(db, run, page, result.get("blocks"), utc_now())
        if result.get("status") != "succeeded":
            raise WorkerError("OCR_EMPTY_RESULT", "模型完成运行，但没有可保存的识别结果")
        has_layout_first_result = isinstance(result.get("layout_first"), dict)
        if blocks_saved == 0 and not has_layout_first_result:
            raise WorkerError("OCR_EMPTY_RESULT", "模型完成运行，但没有可保存的非空 OCR block")
        now = utc_now()
        run.status = "succeeded"
        run.raw_output_storage_key = raw_key
        run.error_code = None
        run.finished_at = now
        page.processed_storage_key = processed_key if processed_path.is_file() else None
        page.transform_json = json.dumps(
            _transform_payload(profile, result, source_render, workflow_state),
            ensure_ascii=False,
        )
        page.status = "ocr_ready"
        page.updated_at = now
        db.commit()
        return True, {"ocr_run_id": run.id, "code": "", "message": f"保存 {blocks_saved} 个 OCR block"}
    except WorkerError as exc:
        db.rollback()
        run = db.get(OcrRun, run.id)
        page = db.get(SourcePage, page.id)
        if run is not None:
            run.status = "failed"
            run.error_code = exc.code
            run.finished_at = utc_now()
        if page is not None:
            page.status = "failed"
            page.updated_at = utc_now()
            page.transform_json = json.dumps(
                {
                    "recognition_mode": profile.mode,
                    "engine_name": profile.engine_name,
                    "engine_version": profile.engine_version,
                    "preprocess_version": profile.preprocess_version,
                    "parameters": profile.parameters,
                    "error_code": exc.code,
                },
                ensure_ascii=False,
            )
        db.commit()
        return False, {"ocr_run_id": run.id, "code": exc.code, "message": exc.message}
    except Exception as exc:  # noqa: BLE001 - Worker 必须将未预期错误持久化
        db.rollback()
        run = db.get(OcrRun, run.id)
        page = db.get(SourcePage, page.id)
        code = "OCR_WORKER_UNEXPECTED_ERROR"
        message = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "OCR page failed unexpectedly: batch_id=%s page_id=%s run_id=%s mode=%s",
            batch.id,
            page.id if page is not None else None,
            run.id if run is not None else None,
            mode,
        )
        if run is not None:
            run.status = "failed"
            run.error_code = code
            run.finished_at = utc_now()
        if page is not None:
            page.status = "failed"
            page.updated_at = utc_now()
        db.commit()
        return False, {"ocr_run_id": run.id, "code": code, "message": message}


def _process_answer_photo_task(db: Session, task: Task, batch: AssignmentBatch, worker_id: str) -> None:
    """处理题目级答案照片，不创建新的学生作业页。"""

    photo = db.get(AnswerPhoto, task.resource_id) if task.resource_id else None
    options = _parse_json(task.options_json, {})
    mode = options.get("recognition_mode") if isinstance(options, dict) else None
    profile = OCR_PROFILES.get(mode) if isinstance(mode, str) else None
    if photo is None or profile is None:
        task.status = "failed"
        task.stage = "saving"
        task.error_code = "ANSWER_PHOTO_TASK_INVALID"
        task.error_message = "答案照片任务缺少有效照片或识别模式"
        task.finished_at = utc_now()
        db.commit()
        return

    now = utc_now()
    task.status = "running"
    task.stage = "preparing"
    task.started_at = task.started_at or now
    task.worker_id = worker_id
    task.heartbeat_at = now
    photo.status = "processing"
    photo.error_code = None
    photo.error_message = None
    photo.updated_at = now
    db.commit()

    raw_key = f"answer-photos/{photo.id}/raw.json"
    processed_key = f"answer-photos/{photo.id}/processed.png"
    raw_path = _path_for_storage_key(raw_key)
    processed_path = _path_for_storage_key(processed_key)
    source_path = _path_for_storage_key(photo.original_storage_key)
    try:
        if not source_path.is_file():
            raise WorkerError("ANSWER_PHOTO_NOT_FOUND", f"答案照片原图不存在：{source_path}")
        workflow_state = run_ocr_workflow(
            profile=profile,
            source_path=source_path,
            processed_path=processed_path,
            raw_path=raw_path,
            subject=batch.subject,
            page_index=1,
            execute_engine=lambda: _execute_run(
                profile,
                source_path,
                processed_path,
                raw_path,
                batch.subject,
                _runtime_paths(),
            ),
        )
        result = workflow_state["result"]
        blocks = result.get("blocks") if isinstance(result, dict) else None
        valid_blocks = [item for item in blocks or [] if isinstance(item, dict) and str(item.get("text_raw") or "").strip()]
        if result.get("status") != "succeeded" or not valid_blocks:
            raise WorkerError("OCR_EMPTY_RESULT", "答案照片识别完成，但没有可回填的非空文本")
        answer_text = "\n".join(str(item.get("text_raw") or "").strip() for item in valid_blocks).strip()
        confidences = [_decimal(item.get("confidence")) for item in valid_blocks]
        confidences = [value for value in confidences if value is not None]
        average_confidence = sum(confidences, Decimal("0")) / Decimal(len(confidences)) if confidences else None
        photo.status = "succeeded"
        photo.answer_text = answer_text
        photo.confidence = average_confidence
        photo.processed_storage_key = processed_key if processed_path.is_file() else None
        photo.raw_output_storage_key = raw_key
        photo.workflow_json = json.dumps(
            _transform_payload(profile, result, {"input_type": "image"}, workflow_state),
            ensure_ascii=False,
        )
        photo.updated_at = utc_now()
        task.status = "succeeded"
        task.stage = "saving"
        task.progress_current = 1
        task.progress_total = 1
        task.finished_at = photo.updated_at
        task.heartbeat_at = task.finished_at
        task.error_code = None
        task.error_message = None
        db.commit()
    except WorkerError as exc:
        db.rollback()
        photo = db.get(AnswerPhoto, photo.id)
        task = db.get(Task, task.id)
        if photo is not None:
            photo.status = "failed"
            photo.error_code = exc.code
            photo.error_message = exc.message
            photo.updated_at = utc_now()
        if task is not None:
            task.status = "failed"
            task.stage = "saving"
            task.error_code = exc.code
            task.error_message = exc.message
            task.finished_at = utc_now()
            task.heartbeat_at = task.finished_at
        db.commit()
    except Exception as exc:  # noqa: BLE001 - Worker 必须持久化未知失败
        db.rollback()
        photo = db.get(AnswerPhoto, photo.id)
        task = db.get(Task, task.id)
        message = f"{type(exc).__name__}: {exc}"
        if photo is not None:
            photo.status = "failed"
            photo.error_code = "ANSWER_PHOTO_OCR_UNEXPECTED_ERROR"
            photo.error_message = message
            photo.updated_at = utc_now()
        if task is not None:
            task.status = "failed"
            task.stage = "saving"
            task.error_code = "ANSWER_PHOTO_OCR_UNEXPECTED_ERROR"
            task.error_message = message
            task.finished_at = utc_now()
            task.heartbeat_at = task.finished_at
        db.commit()


def _claim_task(worker_id: str) -> str | None:
    db = SessionLocal()
    try:
        # A killed worker can leave a task in ``running`` forever. Requeue only
        # tasks whose heartbeat is clearly stale; active workers heartbeat at
        # every page boundary, so a long OCR page is not interrupted here.
        now = utc_now()
        now_dt = parse_utc(now)
        if now_dt is not None:
            running_tasks = list(db.scalars(
                select(Task).where(
                    Task.task_type.in_(["ocr", "answer_photo_ocr"]),
                    Task.status == "running",
                    Task.heartbeat_at.is_not(None),
                )
            ))
            recovered = False
            for stale in running_tasks:
                heartbeat = parse_utc(stale.heartbeat_at)
                if heartbeat is None or now_dt - heartbeat <= timedelta(seconds=STALE_TASK_AFTER_SECONDS):
                    continue
                stale.status = "queued"
                stale.stage = "queued"
                stale.worker_id = None
                stale.started_at = None
                stale.heartbeat_at = None
                stale.error_code = None
                stale.error_message = None
                recovered = True
            if recovered:
                db.commit()
        task = db.scalar(
            select(Task)
            .where(Task.task_type.in_(["ocr", "answer_photo_ocr"]), Task.status == "queued")
            .order_by(Task.created_at, Task.id)
            .with_for_update()
        )
        if task is None:
            db.rollback()
            return None
        now = utc_now()
        task.status = "running"
        task.stage = "preparing"
        task.started_at = task.started_at or now
        task.worker_id = worker_id
        task.heartbeat_at = now
        db.commit()
        return task.id
    finally:
        db.close()


def _finalize_task(db: Session, task: Task, batch: AssignmentBatch, successes: int, failures: list[dict[str, str]]) -> None:
    task.progress_current = successes + len(failures)
    task.progress_total = max(task.progress_total, task.progress_current)
    task.error_items_json = json.dumps(failures, ensure_ascii=False)
    task.heartbeat_at = utc_now()
    task.finished_at = utc_now()
    if failures and successes:
        task.status = "partial_failed"
        task.error_code = "OCR_PARTIAL_FAILED"
        task.error_message = f"{len(failures)} 个页面识别失败，请在校对页重试或转人工。"
    elif failures:
        task.status = "failed"
        task.error_code = failures[0].get("code") or "OCR_FAILED"
        task.error_message = failures[0].get("message") or "OCR 任务失败。"
    else:
        task.status = "succeeded"
        task.error_code = None
        task.error_message = None
    task.stage = "saving"
    page_statuses = list(db.scalars(select(SourcePage.status).where(SourcePage.batch_id == batch.id)))
    batch.status = "pending_correction" if any(value == "ocr_ready" for value in page_statuses) else "partial_failure"
    batch.updated_at = utc_now()
    batch.version += 1


def _process_task(task_id: str, worker_id: str) -> None:
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        batch = db.get(AssignmentBatch, task.batch_id) if task.batch_id else None
        if batch is None:
            task.status = "failed"
            task.stage = "saving"
            task.error_code = "OCR_BATCH_NOT_FOUND"
            task.error_message = "OCR 任务关联的批次不存在"
            task.finished_at = utc_now()
            db.commit()
            return
        if task.task_type == "answer_photo_ocr":
            _process_answer_photo_task(db, task, batch, worker_id)
            return
        options = _parse_json(task.options_json, {})
        mode = options.get("recognition_mode") if isinstance(options, dict) else None
        run_ids = options.get("ocr_run_ids") if isinstance(options, dict) else None
        if not isinstance(run_ids, list) or not run_ids:
            run_ids = [task.resource_id] if task.resource_id else []
        runs = [db.get(OcrRun, run_id) for run_id in run_ids]
        runs = [run for run in runs if run is not None and run.batch_id == batch.id]
        if not mode or not runs:
            _finalize_task(
                db,
                task,
                batch,
                0,
                [{"ocr_run_id": task.resource_id or "", "code": "OCR_TASK_INVALID", "message": "任务缺少有效识别模式或 OCR 运行记录"}],
            )
            db.commit()
            return

        successes = 0
        failures: list[dict[str, str]] = []
        task.progress_total = max(task.progress_total, len(runs))
        for index, run in enumerate(runs, start=1):
            task.stage = "recognizing"
            task.heartbeat_at = utc_now()
            db.commit()
            succeeded, failure = _process_run(db, run, task, batch, mode)
            if succeeded:
                successes += 1
            else:
                failures.append(failure)
            task = db.get(Task, task_id)
            if task is None:
                return
            task.progress_current = index
            task.progress_total = len(runs)
            task.heartbeat_at = utc_now()
            task.worker_id = worker_id
            task.error_items_json = json.dumps(failures, ensure_ascii=False)
            db.commit()
        task = db.get(Task, task_id)
        if task is not None:
            _finalize_task(db, task, batch, successes, failures)
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
    """运行 OCR 队列。

    API 服务会在应用生命周期内启动一个同进程 OCR Worker，避免网页端已经
    成功入队后仍停留在 ``queued``。命令行模式仍然可以独立运行 Worker。
    """

    resolved_worker_id = worker_id or os.getenv("OCR_WORKER_ID") or f"ocr-worker:{socket.gethostname()}:{os.getpid()}"
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
    parser.add_argument("--once", action="store_true", help="领取并处理一个 queued OCR 任务后退出")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="轮询间隔（秒），默认 2")
    args = parser.parse_args()
    run_worker(poll_interval=args.poll_interval, once=args.once)


if __name__ == "__main__":
    main()
