"""统一 API 错误格式。"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.security import apply_security_headers, redact_sensitive_text
from app.core.utils import new_id


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []
        super().__init__(message)


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        return request_id
    request_id = request.headers.get("X-Request-ID") or new_id("req")
    request.state.request_id = request_id
    return request_id


def success_payload(request: Request, data: Any, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    response_meta = {"request_id": get_request_id(request)}
    if meta:
        response_meta.update(meta)
    return {"data": data, "meta": response_meta}


def error_payload(request: Request, code: str, message: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "request_id": get_request_id(request),
        }
    }


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    response = JSONResponse(status_code=exc.status_code, content=error_payload(request, exc.code, redact_sensitive_text(exc.message), exc.details))
    return apply_security_headers(response, request.url.path)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = []
    for item in exc.errors():
        details.append({"field": ".".join(str(part) for part in item.get("loc", [])), "message": item.get("msg", "invalid value")})
    response = JSONResponse(status_code=422, content=error_payload(request, "INVALID_REQUEST", "请求参数校验失败，请根据 details 修正后重试。", details))
    return apply_security_headers(response, request.url.path)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        code = "NOT_FOUND"
        message = "请求的资源不存在，请检查接口路径后重试。"
    elif exc.status_code == 405:
        code = "METHOD_NOT_ALLOWED"
        message = "当前接口不支持该请求方法，请检查接口定义。"
    else:
        code = "HTTP_ERROR"
        message = str(exc.detail) if exc.detail else "请求未完成，请稍后重试。"
    response = JSONResponse(status_code=exc.status_code, content=error_payload(request, code, redact_sensitive_text(message)))
    return apply_security_headers(response, request.url.path)
