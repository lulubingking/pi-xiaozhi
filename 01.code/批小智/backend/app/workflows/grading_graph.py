"""逐题批改 LangGraph 工作流。"""

from __future__ import annotations

from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph


class GradingGraphState(TypedDict, total=False):
    load_batch_config: Callable[[], Any]
    validate_batch_config: Callable[[Any], None]
    load_confirmed_answers: Callable[[], tuple[Any, Any]]
    objective_rule_score: Callable[[Any, Any], None]
    subjective_llm_score: Callable[[Any, Any], None]
    question: Any
    answer: Any
    answer_version: Any
    route: str
    trace: list[str]
    stage: str


def _step(state: GradingGraphState, name: str, **updates: Any) -> dict[str, Any]:
    return {"trace": [*state.get("trace", []), name], "stage": name, **updates}


def _load_batch_config(state: GradingGraphState) -> dict[str, Any]:
    question = state["load_batch_config"]()
    return _step(state, "load_batch_config", question=question)


def _validate_batch_config(state: GradingGraphState) -> dict[str, Any]:
    state["validate_batch_config"](state["question"])
    return _step(state, "validate_batch_config")


def _load_confirmed_answers(state: GradingGraphState) -> dict[str, Any]:
    answer, answer_version = state["load_confirmed_answers"]()
    return _step(state, "load_confirmed_answers", answer=answer, answer_version=answer_version)


def _dispatch_question_type(state: GradingGraphState) -> dict[str, Any]:
    question_type = str(getattr(state["question"], "question_type", ""))
    if question_type not in {"objective", "subjective"}:
        raise ValueError(f"题型无法分派：{question_type or '未设置'}")
    return _step(state, "dispatch_question_type", route=question_type)


def _objective_rule_score(state: GradingGraphState) -> dict[str, Any]:
    state["objective_rule_score"](state["answer"], state["answer_version"])
    return _step(state, "objective_rule_score")


def _subjective_llm_score(state: GradingGraphState) -> dict[str, Any]:
    state["subjective_llm_score"](state["answer"], state["answer_version"])
    return _step(state, "subjective_llm_score")


def _validate_score_schema(state: GradingGraphState) -> dict[str, Any]:
    return _step(state, "validate_score_schema")


def _detect_anomalies(state: GradingGraphState) -> dict[str, Any]:
    return _step(state, "detect_anomalies")


def _persist_suggested_result(state: GradingGraphState) -> dict[str, Any]:
    return _step(state, "persist_suggested_result")


def _aggregate_student_status(state: GradingGraphState) -> dict[str, Any]:
    return _step(state, "aggregate_student_status")


def _enqueue_teacher_review(state: GradingGraphState) -> dict[str, Any]:
    return _step(state, "enqueue_teacher_review")


def _route(state: GradingGraphState) -> str:
    return state["route"]


def build_grading_graph():
    graph = StateGraph(GradingGraphState)
    graph.add_node("load_batch_config", _load_batch_config)
    graph.add_node("validate_batch_config", _validate_batch_config)
    graph.add_node("load_confirmed_answers", _load_confirmed_answers)
    graph.add_node("dispatch_question_type", _dispatch_question_type)
    graph.add_node("objective_rule_score", _objective_rule_score)
    graph.add_node("subjective_llm_score", _subjective_llm_score)
    graph.add_node("validate_score_schema", _validate_score_schema)
    graph.add_node("detect_anomalies", _detect_anomalies)
    graph.add_node("persist_suggested_result", _persist_suggested_result)
    graph.add_node("aggregate_student_status", _aggregate_student_status)
    graph.add_node("enqueue_teacher_review", _enqueue_teacher_review)
    graph.add_edge(START, "load_batch_config")
    graph.add_edge("load_batch_config", "validate_batch_config")
    graph.add_edge("validate_batch_config", "load_confirmed_answers")
    graph.add_edge("load_confirmed_answers", "dispatch_question_type")
    graph.add_conditional_edges(
        "dispatch_question_type",
        _route,
        {"objective": "objective_rule_score", "subjective": "subjective_llm_score"},
    )
    graph.add_edge("objective_rule_score", "validate_score_schema")
    graph.add_edge("subjective_llm_score", "validate_score_schema")
    graph.add_edge("validate_score_schema", "detect_anomalies")
    graph.add_edge("detect_anomalies", "persist_suggested_result")
    graph.add_edge("persist_suggested_result", "aggregate_student_status")
    graph.add_edge("aggregate_student_status", "enqueue_teacher_review")
    graph.add_edge("enqueue_teacher_review", END)
    return graph.compile()


GRADING_GRAPH = build_grading_graph()


def run_grading_workflow(
    *,
    load_batch_config: Callable[[], Any],
    validate_batch_config: Callable[[Any], None],
    load_confirmed_answers: Callable[[], tuple[Any, Any]],
    objective_rule_score: Callable[[Any, Any], None],
    subjective_llm_score: Callable[[Any, Any], None],
) -> GradingGraphState:
    return GRADING_GRAPH.invoke(
        {
            "load_batch_config": load_batch_config,
            "validate_batch_config": validate_batch_config,
            "load_confirmed_answers": load_confirmed_answers,
            "objective_rule_score": objective_rule_score,
            "subjective_llm_score": subjective_llm_score,
            "trace": [],
        }
    )
