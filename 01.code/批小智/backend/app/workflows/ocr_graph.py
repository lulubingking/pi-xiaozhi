"""OCR 识别工作流。

该模块只负责编排，不替换已经在 OCR 专用环境中验证过的模型执行器。
实际识别节点会调用 PaddleOCR/OpenCV/PIL、TrOCR 或 TexTeller 运行时，
并把每个节点的阶段写入结果快照，便于任务追踪和失败定位。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph


class OcrGraphState(TypedDict, total=False):
    profile: Any
    source_path: str
    processed_path: str
    raw_path: str
    subject: str
    page_index: int
    execute_engine: Callable[[], dict[str, Any]]
    result: dict[str, Any]
    anomalies: list[dict[str, Any]]
    trace: list[str]
    stage: str
    awaiting_teacher_correction: bool


def _step(state: OcrGraphState, name: str, **updates: Any) -> dict[str, Any]:
    return {
        "trace": [*state.get("trace", []), name],
        "stage": name,
        **updates,
    }


def _load_file(state: OcrGraphState) -> dict[str, Any]:
    source = Path(state["source_path"])
    if not source.is_file():
        raise FileNotFoundError(f"OCR 输入文件不存在：{source}")
    return _step(state, "load_file")


def _validate_file(state: OcrGraphState) -> dict[str, Any]:
    source = Path(state["source_path"])
    supported = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    if source.suffix.lower() not in supported:
        raise ValueError(f"OCR 页面必须是图片格式：{source.suffix or '无扩展名'}")
    return _step(state, "validate_file")


def _split_pages(state: OcrGraphState) -> dict[str, Any]:
    # Worker 已经按页创建 SourcePage；此处保留文档要求的页面拆分节点，
    # 并将当前页写入 trace，避免把多页 PDF 误当成单页模型输入。
    return _step(state, "split_pages", page_index=int(state.get("page_index", 1)))


def _preprocess_page(state: OcrGraphState) -> dict[str, Any]:
    # OpenCV/PIL 的实际预处理在 OCR 专用 runtime 中执行；节点边界在这里固定。
    return _step(state, "preprocess_page", preprocess_started=True)


def _suggest_grouping(state: OcrGraphState) -> dict[str, Any]:
    # 学生页归属仍需教师确认，工作流只提供可追踪的人工确认边界。
    return _step(
        state,
        "suggest_grouping",
        grouping_suggestion={"strategy": "teacher_confirmation", "confidence": None},
    )


def _run_paddleocr(state: OcrGraphState) -> dict[str, Any]:
    result = state["execute_engine"]()
    if not isinstance(result, dict):
        raise TypeError("OCR 执行器必须返回 JSON 对象")
    return _step(state, "run_paddleocr", result=result)


def _persist_raw_ocr(state: OcrGraphState) -> dict[str, Any]:
    raw_path = Path(state["raw_path"])
    if not raw_path.is_file():
        raise FileNotFoundError(f"OCR 原始结果未生成：{raw_path}")
    return _step(state, "persist_raw_ocr")


def _detect_ocr_anomaly(state: OcrGraphState) -> dict[str, Any]:
    result = state.get("result", {})
    blocks = result.get("blocks") if isinstance(result, dict) else None
    anomalies: list[dict[str, Any]] = []
    if not isinstance(blocks, list) or not blocks:
        anomalies.append({"code": "OCR_EMPTY_RESULT", "severity": "blocking"})
    invalid_blocks = [
        item
        for item in blocks or []
        if not isinstance(item, dict) or not str(item.get("text_raw") or "").strip()
    ]
    if invalid_blocks:
        anomalies.append({"code": "OCR_EMPTY_BLOCK", "severity": "warning", "count": len(invalid_blocks)})
    return _step(state, "detect_ocr_anomaly", anomalies=anomalies)


def _persist_page_state(state: OcrGraphState) -> dict[str, Any]:
    # 数据库状态由 Worker 在图完成后以事务方式保存；此节点明确记录目标状态。
    return _step(state, "persist_page_state", page_state="ocr_ready")


def _wait_for_teacher_correction(state: OcrGraphState) -> dict[str, Any]:
    return _step(state, "wait_for_teacher_correction", awaiting_teacher_correction=True)


def build_ocr_graph():
    graph = StateGraph(OcrGraphState)
    graph.add_node("load_file", _load_file)
    graph.add_node("validate_file", _validate_file)
    graph.add_node("split_pages", _split_pages)
    graph.add_node("preprocess_page", _preprocess_page)
    graph.add_node("suggest_grouping", _suggest_grouping)
    graph.add_node("run_paddleocr", _run_paddleocr)
    graph.add_node("persist_raw_ocr", _persist_raw_ocr)
    graph.add_node("detect_ocr_anomaly", _detect_ocr_anomaly)
    graph.add_node("persist_page_state", _persist_page_state)
    graph.add_node("wait_for_teacher_correction", _wait_for_teacher_correction)
    graph.add_edge(START, "load_file")
    graph.add_edge("load_file", "validate_file")
    graph.add_edge("validate_file", "split_pages")
    graph.add_edge("split_pages", "preprocess_page")
    graph.add_edge("preprocess_page", "suggest_grouping")
    graph.add_edge("suggest_grouping", "run_paddleocr")
    graph.add_edge("run_paddleocr", "persist_raw_ocr")
    graph.add_edge("persist_raw_ocr", "detect_ocr_anomaly")
    graph.add_edge("detect_ocr_anomaly", "persist_page_state")
    graph.add_edge("persist_page_state", "wait_for_teacher_correction")
    graph.add_edge("wait_for_teacher_correction", END)
    return graph.compile()


OCR_GRAPH = build_ocr_graph()


def run_ocr_workflow(
    *,
    profile: Any,
    source_path: Path,
    processed_path: Path,
    raw_path: Path,
    subject: str,
    page_index: int,
    execute_engine: Callable[[], dict[str, Any]],
) -> OcrGraphState:
    """运行单页 OCR 图并返回可持久化的工作流状态。"""

    return OCR_GRAPH.invoke(
        {
            "profile": profile,
            "source_path": str(source_path),
            "processed_path": str(processed_path),
            "raw_path": str(raw_path),
            "subject": subject,
            "page_index": page_index,
            "execute_engine": execute_engine,
            "trace": [],
        }
    )
