"""客观题严格规范化匹配规则。"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from typing import Any


RULE_VERSION = "objective-exact-normalized-v1"


class ObjectiveRuleError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ObjectiveScore:
    matched: bool
    suggested_score: float
    normalized_answer: str
    normalized_accepted_answers: tuple[str, ...]
    matched_answer: str | None
    rule_version: str
    reason: str


def normalize_answer(value: str | None) -> str:
    """按原型已明确的 exact-normalized 口径处理答案，不做模糊匹配。"""

    return "".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


def _rule_values(reference_answer: str, rule_json: str | None) -> tuple[list[str], str]:
    if rule_json:
        try:
            raw: Any = json.loads(rule_json)
        except json.JSONDecodeError as exc:
            raise ObjectiveRuleError("OBJECTIVE_RULE_INVALID", "客观题容错规则不是合法 JSON。") from exc
        if not isinstance(raw, dict):
            raise ObjectiveRuleError("OBJECTIVE_RULE_INVALID", "客观题容错规则必须是 JSON 对象。")
        accepted = raw.get("accepted_answers")
        if not isinstance(accepted, list) or not accepted or not all(isinstance(item, str) and item.strip() for item in accepted):
            raise ObjectiveRuleError("OBJECTIVE_RULE_INVALID", "客观题容错规则缺少有效的 accepted_answers。")
        return accepted, str(raw.get("version") or RULE_VERSION)

    if not reference_answer or not reference_answer.strip():
        raise ObjectiveRuleError("OBJECTIVE_REFERENCE_MISSING", "客观题缺少参考答案。")
    alternatives = [item.strip() for item in reference_answer.split("|") if item.strip()]
    if not alternatives:
        raise ObjectiveRuleError("OBJECTIVE_REFERENCE_MISSING", "客观题缺少有效参考答案。")
    return alternatives, RULE_VERSION


def score_objective(
    *,
    answer_text: str | None,
    reference_answer: str | None,
    objective_rule_json: str | None,
    max_score: float,
    blank_confirmed: bool = False,
) -> ObjectiveScore:
    accepted, rule_version = _rule_values(reference_answer or "", objective_rule_json)
    normalized_answer = normalize_answer(answer_text)
    normalized_accepted = tuple(normalize_answer(item) for item in accepted)
    matched_answer = next(
        (raw for raw, normalized in zip(accepted, normalized_accepted) if normalized_answer and normalized_answer == normalized),
        None,
    )
    matched = matched_answer is not None
    reason = "与规范化后的参考答案匹配" if matched else "未匹配规范化后的参考答案"
    if blank_confirmed and not normalized_answer:
        reason = "教师已确认学生未作答"
    return ObjectiveScore(
        matched=matched,
        suggested_score=float(max_score) if matched else 0.0,
        normalized_answer=normalized_answer,
        normalized_accepted_answers=normalized_accepted,
        matched_answer=matched_answer,
        rule_version=rule_version,
        reason=reason,
    )
