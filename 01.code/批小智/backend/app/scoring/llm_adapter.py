"""主观题通用模型适配器。

适配器只接受 OpenAI 兼容的聊天接口，向上层暴露统一的主观题评分协议。
它不保存明文 API Key、不返回原始模型响应，也不允许未经校验的模型分数进入正式结果。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.model_config import ModelConfig, get_model_config, resolve_api_key
from app.models import User

PROMPT_VERSION = "subjective-grading-prompt-v4-structured-result-comment"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class AdapterError(RuntimeError):
    """可持久化到任务异常字段的脱敏错误。"""

    def __init__(self, code: str, message: str, *, retryable: bool, attempts: int = 0):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.attempts = attempts
        super().__init__(message)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RubricPointSpec(StrictModel):
    point_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=20000)
    max_score: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class SubjectiveGradingInput(StrictModel):
    """模型输入不包含学生姓名、学号等身份字段。"""

    question_id: str = Field(min_length=1, max_length=64)
    question_text: str = Field(min_length=1, max_length=20000)
    answer_text: str | None = Field(default=None, max_length=50000)
    reference_answer: str | None = Field(default=None, max_length=20000)
    max_score: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    rubric_version_id: str = Field(min_length=1, max_length=64)
    rubric_points: list[RubricPointSpec] = Field(min_length=1, max_length=100)
    examples: list[str] = Field(default_factory=list, max_length=100)


class SubjectivePointOutput(StrictModel):
    point_id: str = Field(min_length=1, max_length=64)
    score: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    max_score: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    evidence: str = Field(default="", max_length=10000)
    deduction_reason: str = Field(default="", max_length=10000)


class SubjectiveGradingOutput(StrictModel):
    score: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    points: list[SubjectivePointOutput] = Field(min_length=1, max_length=100)
    comment: str = Field(min_length=1, max_length=10000)
    review_required: bool


@dataclass(frozen=True)
class AdapterResult:
    output: SubjectiveGradingOutput
    input_hash: str
    response_digest: str
    model_name: str
    prompt_version: str = PROMPT_VERSION


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _input_hash(data: SubjectiveGradingInput) -> str:
    canonical = json.dumps(data.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_messages(data: SubjectiveGradingInput) -> list[dict[str, str]]:
    rubric_lines = "\n".join(
        f"- point_id={point.point_id}; max_score={_decimal_text(point.max_score)}; natural_language_rubric={point.label}"
        for point in data.rubric_points
    )
    examples = "\n".join(f"- {item}" for item in data.examples) if data.examples else "无"
    user_content = (
        "请依据题目、参考答案和评分标准评估学生作答。\n"
        "批改完成后必须同时输出：结构化批改结果、逐评分点证据、总评语和教师复核标记。\n"
        "只输出一个合法 JSON 对象，不要输出 Markdown、解释文字或额外字段。\n\n"
        f"题目 ID：{data.question_id}\n"
        f"题目要求：{data.question_text}\n"
        f"参考答案或评分依据：{data.reference_answer or '无'}\n"
        f"题目满分（系统硬上限，输出总分必须在 0 到该分值之间）：{_decimal_text(data.max_score)}\n"
        f"评分标准版本：{data.rubric_version_id}\n"
        f"评分点：\n{rubric_lines}\n"
        f"示例作答：\n{examples}\n"
        f"学生作答：\n{data.answer_text or '学生未填写，按空白作答处理'}\n\n"
        "评分标准是教师提供的自然语言说明，请按整段语义理解，不要自行改写或把其中的数字当成系统约束。"
        "每个评分点的 max_score 是系统提供的权威满分，必须原样输出，不得根据文字内容自行推断或改写。"
        "请按每个 point_id 给出对应分项分数，并让分项分数合计等于你输出的总分。\n"
        "JSON 输出结构必须严格为："
        '{"score": 0, "points": [{"point_id": "评分点 ID", "score": 0, "max_score": 0, "evidence": "作答证据", "deduction_reason": "扣分原因"}], '
        '"comment": "完整评语", "review_required": true}。\n'
        "其中 score 是结构化批改总分；points 是结构化逐点评分结果；comment 必须包含总体结论、做得好的地方、存在的问题和改进建议；"
        "points 中每项必须包含 point_id、score、max_score、evidence、deduction_reason；"
        "所有评分点都必须出现，review_required 必须为 true；score 不得为负数、不得超过题目满分，且必须等于所有 points.score 之和。"
    )
    return [
        {
            "role": "system",
            "content": "你是教师批改辅助模型。你的输出只能作为 AI 初评建议，必须由教师复核后生效。",
        },
        {"role": "user", "content": user_content},
    ]


def _validate_input(data: SubjectiveGradingInput) -> None:
    point_ids = [item.point_id for item in data.rubric_points]
    if len(point_ids) != len(set(point_ids)):
        raise AdapterError("RUBRIC_INVALID", "评分标准包含重复评分点。", retryable=False)


def validate_output(data: SubjectiveGradingInput, output: SubjectiveGradingOutput) -> SubjectiveGradingOutput:
    """校验模型结果，返回可保存的统一结构。"""

    expected = {item.point_id: item for item in data.rubric_points}
    actual_ids = [item.point_id for item in output.points]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected):
        raise AdapterError("MODEL_OUTPUT_INVALID", "模型返回的评分点不完整或包含未知评分点。", retryable=True)

    # 评分点满分由教师保存的 rubric 决定。模型输出的 max_score 只是重复字段，
    # 不作为业务数据来源；将其规范回 rubric 的权威值，再校验模型实际给分。
    normalized_points = [
        item.model_copy(update={"max_score": expected[item.point_id].max_score})
        for item in output.points
    ]
    output = output.model_copy(update={"points": normalized_points})

    point_total = Decimal("0")
    for item in output.points:
        if item.score < 0:
            raise AdapterError("MODEL_SCORE_INVALID", "模型返回了负数评分。", retryable=True)
        expected_point = expected[item.point_id]
        if item.score > expected_point.max_score:
            raise AdapterError("MODEL_SCORE_INVALID", "模型返回的评分点分数超过该评分点满分。", retryable=True)
        point_total += item.score

    if output.score < 0 or output.score > data.max_score or output.score != point_total:
        raise AdapterError("MODEL_SCORE_INVALID", "模型总分必须在题目满分范围内且与分项分数一致。", retryable=True)
    if not output.review_required:
        raise AdapterError("MODEL_REVIEW_FLAG_INVALID", "模型结果未标记为必须教师复核。", retryable=True)
    return output


def _completion_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AdapterError("MODEL_CONFIG_INVALID", "模型 Base URL 必须是 http 或 https 地址。", retryable=False)
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        return base_url.rstrip("/")
    return f"{base_url.rstrip('/')}/chat/completions"


def _extract_content(payload: Any) -> str:
    try:
        choices = payload["choices"]
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AdapterError("MODEL_OUTPUT_INVALID", "模型响应缺少 choices.message.content。", retryable=True) from exc
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise AdapterError("MODEL_EMPTY_RESPONSE", "模型返回了空的评分结果。", retryable=True)
    return content.strip()


def _parse_json_content(content: str) -> SubjectiveGradingOutput:
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].strip().lower().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        value = json.loads(candidate)
        return SubjectiveGradingOutput.model_validate(value)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise AdapterError("MODEL_OUTPUT_INVALID", "模型返回内容不是约定的 JSON 评分结构。", retryable=True) from exc


def _read_response(response: Any) -> tuple[dict[str, Any], str]:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise AdapterError("MODEL_RESPONSE_TOO_LARGE", "模型响应超过允许大小。", retryable=False)
    digest = hashlib.sha256(body).hexdigest()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("MODEL_OUTPUT_INVALID", "模型响应不是合法 JSON。", retryable=True) from exc
    if not isinstance(payload, dict):
        raise AdapterError("MODEL_OUTPUT_INVALID", "模型响应顶层结构必须是 JSON 对象。", retryable=True)
    return payload, digest


def _request_once(config: ModelConfig, api_key: str, messages: list[dict[str, str]], timeout: int) -> tuple[dict[str, Any], str]:
    if not config.model_name:
        raise AdapterError("MODEL_CONFIG_INCOMPLETE", "未配置模型名。", retryable=False)
    url = _completion_url(config.base_url or "")
    body = json.dumps(
        {
            "model": config.model_name,
            "messages": messages,
            "temperature": 0,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return _read_response(response)


def grade_subjective(
    config: ModelConfig,
    data: SubjectiveGradingInput,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = 0.4,
) -> AdapterResult:
    """调用一个 OpenAI 兼容服务并返回通过校验的主观题初评。"""

    _validate_input(data)
    if not config.base_url or not config.model_name:
        raise AdapterError("MODEL_CONFIG_INCOMPLETE", "模型服务商、模型或 Base URL 未完整配置。", retryable=False)
    api_key = resolve_api_key(config.api_key_ref, config.user_id)
    if not api_key:
        raise AdapterError("MODEL_KEY_UNAVAILABLE", "当前后端进程中没有可用的模型 API Key。", retryable=False)
    if timeout_seconds <= 0 or max_attempts <= 0:
        raise AdapterError("MODEL_ADAPTER_CONFIG_INVALID", "适配器超时和重试参数必须为正数。", retryable=False)

    messages = _build_messages(data)
    last_error: AdapterError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            payload, response_digest = _request_once(config, api_key, messages, timeout_seconds)
            output = validate_output(data, _parse_json_content(_extract_content(payload)))
            return AdapterResult(
                output=output,
                input_hash=_input_hash(data),
                response_digest=response_digest,
                model_name=config.model_name,
            )
        except HTTPError as exc:
            retryable = exc.code == 429 or exc.code >= 500 or exc.code in {408, 409}
            last_error = AdapterError(
                "MODEL_RATE_LIMITED" if exc.code == 429 else "MODEL_REQUEST_FAILED",
                f"模型服务返回 HTTP {exc.code}。",
                retryable=retryable,
                attempts=attempt,
            )
        except (URLError, TimeoutError, OSError) as exc:
            last_error = AdapterError(
                "MODEL_TIMEOUT" if isinstance(exc, TimeoutError) else "MODEL_CONNECTION_FAILED",
                "无法连接模型服务，请检查 Base URL、网络和代理配置。",
                retryable=True,
                attempts=attempt,
            )
        except AdapterError as exc:
            exc.attempts = attempt
            last_error = exc
        if last_error is None or not last_error.retryable or attempt >= max_attempts:
            break
        time.sleep(max(0, backoff_seconds) * attempt)

    if last_error is not None:
        raise last_error
    raise AdapterError("MODEL_REQUEST_FAILED", "模型请求未返回结果。", retryable=True, attempts=max_attempts)


def grade_subjective_for_user(db: Any, user: User, data: SubjectiveGradingInput, **kwargs: Any) -> AdapterResult:
    """从当前账号设置读取配置，供评分 Worker 调用。"""

    return grade_subjective(get_model_config(db, user), data, **kwargs)
