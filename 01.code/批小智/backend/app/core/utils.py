"""应用层通用工具：时间、不可预测 ID、密码和会话令牌。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone


def utc_now() -> str:
    """返回带 UTC 时区的 ISO 8601 时间，统一写入数据库。"""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def new_id(prefix: str) -> str:
    """生成 API 对外使用的不可预测业务 ID。"""

    return f"{prefix}_{secrets.token_urlsafe(16).replace('-', '').replace('_', '')}"


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """使用标准库 PBKDF2 保存密码，避免明文密码进入数据库或日志。"""

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return f"pbkdf2_sha256$240000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds_text, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), rounds)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (TypeError, ValueError):
        return False
