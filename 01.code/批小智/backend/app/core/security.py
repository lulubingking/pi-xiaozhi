"""Small, dependency-free safeguards against accidental secret disclosure."""

from __future__ import annotations

import logging
import re
from typing import Any


_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[^\s,;\"']+")
_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|secret|password|token)\b\s*[:=]\s*)[^\s,;\"']+"
)
_PROVIDER_KEY_RE = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b|\bAKIA[0-9A-Z]{16}\b")


def redact_sensitive_text(value: Any) -> str:
    """Return text safe for API errors and logs without exposing secret values."""

    text = str(value)
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    return _PROVIDER_KEY_RE.sub("[REDACTED]", text)


def apply_security_headers(response: Any, path: str) -> Any:
    """Apply headers that prevent browser caching and framing of sensitive data."""

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


class SecretRedactionFilter(logging.Filter):
    """Redact common credential shapes from both log messages and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_sensitive_text(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        key: redact_sensitive_text(value) if isinstance(value, (str, bytes)) else value
                        for key, value in record.args.items()
                    }
                else:
                    record.args = tuple(
                        redact_sensitive_text(value) if isinstance(value, (str, bytes)) else value
                        for value in record.args
                    )
        except Exception:
            # Logging must never break a request because a third-party value is unusual.
            pass
        return True


def install_log_redaction() -> None:
    redactor = SecretRedactionFilter()
    root = logging.getLogger()
    root.addFilter(redactor)
    for handler in root.handlers:
        handler.addFilter(redactor)
