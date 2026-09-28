"""首版认证、班级、批次和评分标准请求模型。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def non_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


class LoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)
    remember_me: bool = False

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("必须是有效邮箱")
        return value


class RegisterRequest(StrictModel):
    """教师自助注册请求；注册后由服务端建立教师会话。"""

    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("必须是有效邮箱")
        return value

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return non_blank(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("密码不能只包含空格")
        return value


class StudentInput(StrictModel):
    student_code: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("student_code", "display_name")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return non_blank(value)


class ClassCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    students: list[StudentInput] = Field(default_factory=list, max_length=1000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return non_blank(value)


class ClassPatchRequest(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "inactive"] | None = None
    version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        return non_blank(value) if value is not None else None


class StudentPatchRequest(StrictModel):
    student_code: str | None = Field(default=None, min_length=1, max_length=64)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "inactive"] | None = None

    @field_validator("student_code", "display_name")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return non_blank(value) if value is not None else None


FileRole = Literal["student_work", "question_paper"]


class PurgeFileRequest(StrictModel):
    """高风险撤销识别请求，必须回显原始文件名确认。"""

    confirm_name: str = Field(min_length=1, max_length=255)

    @field_validator("confirm_name")
    @classmethod
    def validate_confirm_name(cls, value: str) -> str:
        return non_blank(value)


class PurgeBatchRequest(StrictModel):
    """高风险撤销批次请求，必须回显完整批次名称确认。"""

    confirm_name: str = Field(min_length=1, max_length=200)

    @field_validator("confirm_name")
    @classmethod
    def validate_confirm_name(cls, value: str) -> str:
        return non_blank(value)


class DeleteReviewedBatchRequest(StrictModel):
    """已复核批次物理删除请求，必须同时验证当前账号密码和批次名称。"""

    password: str = Field(min_length=1, max_length=200)
    confirm_name: str = Field(min_length=1, max_length=200)

    @field_validator("password", "confirm_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return non_blank(value)


class BatchQuestionInput(StrictModel):
    question_no: str = Field(min_length=1, max_length=32)
    question_type: Literal["objective", "subjective"]
    max_score: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    question_prompt: str | None = Field(default=None, max_length=20000)
    reference_answer: str | None = None
    rubric_version_id: str | None = None

    @field_validator("question_no")
    @classmethod
    def validate_question_no(cls, value: str) -> str:
        return non_blank(value)

    @field_validator("question_prompt")
    @classmethod
    def validate_question_prompt(cls, value: str | None) -> str | None:
        return non_blank(value) if value is not None else None


class BatchCreateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    class_id: str = Field(min_length=1, max_length=64)
    subject: Literal["english", "chinese", "math"]
    total_score: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    questions: list[BatchQuestionInput] = Field(min_length=1, max_length=200)
    save_mode: Literal["draft", "upload"] = "draft"

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return non_blank(value)


class BatchPatchRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    subject: Literal["english", "chinese", "math"] | None = None
    total_score: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    class_id: str | None = Field(default=None, min_length=1, max_length=64)
    version: int = Field(ge=1)

    @field_validator("title")
    @classmethod
    def validate_optional_title(cls, value: str | None) -> str | None:
        return non_blank(value) if value is not None else None


class BatchQuestionsRequest(StrictModel):
    questions: list[BatchQuestionInput] = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    preserve_existing_structure: bool = False


class QuestionPromptRequest(StrictModel):
    question_prompt: str | None = Field(default=None, max_length=20000)
    reference_answer: str | None = Field(default=None, max_length=20000)
    source_page_id: str | None = Field(default=None, max_length=64)
    ocr_block_ids: list[str] = Field(default_factory=list, max_length=500)
    version: int = Field(ge=1)

    @field_validator("question_prompt", "reference_answer")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_content(self):
        if not (self.question_prompt or self.reference_answer):
            raise ValueError("题目文本和参考答案至少填写一项")
        return self


class QuestionLayoutPageInput(StrictModel):
    source_page_id: str = Field(min_length=1, max_length=64)
    page_index: int = Field(ge=1, le=10000)
    width: int = Field(gt=0, le=100000)
    height: int = Field(gt=0, le=100000)


class QuestionLayoutRegionInput(StrictModel):
    question_id: str = Field(min_length=1, max_length=64)
    question_no: str = Field(min_length=1, max_length=32)
    source_page_id: str = Field(min_length=1, max_length=64)
    page_index: int = Field(ge=1, le=10000)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    ocr_block_ids: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_region(self):
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("题目区域必须具有有效的宽高")
        return self


class QuestionLayoutRequest(StrictModel):
    source_page_id: str = Field(min_length=1, max_length=64)
    source_pages: list[QuestionLayoutPageInput] = Field(min_length=1, max_length=100)
    regions: list[QuestionLayoutRegionInput] = Field(min_length=1, max_length=500)
    version: int = Field(ge=0)


class ReportExportRequest(StrictModel):
    report_mode: Literal["reviewed", "ai_preview"] = "reviewed"
    format: Literal["pdf", "docx"]


RecognitionMode = Literal[
    "printed",
    "chinese_handwriting",
    "english_handwriting",
    "math_handwriting",
]


class OcrRunRequest(StrictModel):
    """启动 OCR 时必须显式选择识别类型，避免按学科静默误路由。"""

    recognition_mode: RecognitionMode


class SettingsProfilePatch(StrictModel):
    teacher_name: str = Field(min_length=1, max_length=120)

    @field_validator("teacher_name")
    @classmethod
    def validate_teacher_name(cls, value: str) -> str:
        return non_blank(value)


class SettingsModelPatch(StrictModel):
    """通用 OpenAI 兼容模型配置；网页端 Key 只进入后端运行时内存。"""

    provider_name: str = Field(min_length=1, max_length=120)
    model_name: str = Field(min_length=1, max_length=120)
    model_base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    api_key_ref: str | None = Field(default=None, max_length=255)
    clear_api_key: bool = False

    @field_validator("provider_name", "model_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return non_blank(value)

    @field_validator("model_base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("必须是 http 或 https 地址")
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("api_key_ref")
    @classmethod
    def validate_api_key_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not value.startswith("env:"):
            raise ValueError("密钥引用必须使用 env:变量名 格式，明文 Key 不通过该接口保存")
        env_name = value[4:].strip()
        if not env_name or not env_name.replace("_", "").isalnum() or not env_name[0].isalpha():
            raise ValueError("env: 后必须是合法的服务端环境变量名")
        return f"env:{env_name}"


class SettingsStoragePatch(StrictModel):
    storage_root: str = Field(min_length=1, max_length=500)
    retention_days: int | None = Field(default=None, ge=0)
    queue_limit: int = Field(default=20, ge=1, le=10000)
    worker_concurrency: int = Field(default=4, ge=1, le=64)

    @field_validator("storage_root")
    @classmethod
    def validate_storage_root(cls, value: str) -> str:
        return non_blank(value)


class GradingScopeRequest(StrictModel):
    assignment_group_ids: list[str] = Field(default_factory=list, max_length=1000)
    question_ids: list[str] = Field(default_factory=list, max_length=200)


class GradingRunRequest(StrictModel):
    scope: GradingScopeRequest
    force_rerun: bool = False


class ReviewPointScoreInput(StrictModel):
    point_id: str = Field(min_length=1, max_length=64)
    teacher_score: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class ReviewItemRequest(StrictModel):
    teacher_score: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    teacher_comment: str | None = Field(default=None, max_length=10000)
    anomaly_resolution: Literal["confirmed", "ignored", "resolved"] | None = None
    reason: str | None = Field(default=None, max_length=1000)
    point_scores: list[ReviewPointScoreInput] = Field(default_factory=list, max_length=100)
    version: int = Field(ge=1)

    @field_validator("teacher_comment", "reason")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return non_blank(value) if value is not None else None


class PageGroupingRequest(StrictModel):
    student_id: str | None = None
    assignment_group_id: str | None = None
    page_sequence: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=500)
    version: int = Field(ge=1)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return non_blank(value)


class OcrCorrectionAnswerInput(StrictModel):
    question_id: str = Field(min_length=1, max_length=64)
    present_on_page: bool
    answer_text: str | None = Field(default=None, max_length=20000)
    is_blank_confirmed: bool = False
    source_type: Literal["ocr", "manual", "teacher_corrected"]
    answer_photo_id: str | None = Field(default=None, max_length=64)


class OcrCorrectionRequest(StrictModel):
    answers: list[OcrCorrectionAnswerInput] = Field(max_length=200)
    save_mode: Literal["draft", "confirm"] = "draft"
    version: int = Field(ge=1)


class PageConfirmRequest(StrictModel):
    version: int = Field(ge=1)


class RubricPointInput(StrictModel):
    label: str = Field(min_length=1, max_length=20000)
    max_score: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    sort_order: int = Field(ge=1)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        return non_blank(value)


class RubricExampleInput(StrictModel):
    content: str = Field(min_length=1, max_length=5000)
    rubric_point_id: str | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return non_blank(value)


class RubricCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    subject: Literal["english", "chinese", "math"]
    question_type: Literal["subjective"]
    total_score: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    points: list[RubricPointInput] = Field(min_length=1, max_length=100)
    examples: list[RubricExampleInput] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return non_blank(value)


class RubricPatchRequest(RubricCreateRequest):
    version: int = Field(ge=1)
