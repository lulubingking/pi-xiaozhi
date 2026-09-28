"""当前用户的外部评分模型配置读取与运行时密钥解析。

API Key 可以由网页端提交到当前后端进程中使用，但不会写入数据库，也不会
通过设置接口返回。服务重启后需要在网页中重新输入一次。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSetting, User


# 仅保存在当前后端进程内。不要把这个映射写入数据库、日志或响应。
_RUNTIME_API_KEYS: dict[str, str] = {}


@dataclass(frozen=True)
class ModelConfig:
    """模型调用所需的配置快照；api_key 只在服务端运行时短暂存在。"""

    provider_name: str
    model_name: str | None
    base_url: str | None
    api_key_ref: str | None
    user_id: str | None = None


def get_model_config(db: Session, user: User) -> ModelConfig:
    setting = db.scalar(select(AppSetting).where(AppSetting.user_id == user.id))
    return ModelConfig(
        provider_name=setting.model_provider if setting else "DeepSeek",
        model_name=setting.model_name if setting else "deepseek-flash",
        base_url=setting.model_base_url if setting else None,
        api_key_ref=setting.api_key_ref if setting else None,
        user_id=user.id,
    )


def set_runtime_api_key(user_id: str, api_key: str | None) -> None:
    """设置或清除当前进程内的网页端 API Key。"""

    if api_key:
        _RUNTIME_API_KEYS[user_id] = api_key
    else:
        _RUNTIME_API_KEYS.pop(user_id, None)


def has_runtime_api_key(user_id: str) -> bool:
    return bool(_RUNTIME_API_KEYS.get(user_id))


def resolve_api_key(api_key_ref: str | None, user_id: str | None = None) -> str | None:
    """优先使用网页端运行时 Key，其次兼容旧的 env:变量名配置。"""

    if user_id:
        runtime_key = _RUNTIME_API_KEYS.get(user_id)
        if runtime_key:
            return runtime_key

    if not api_key_ref or not api_key_ref.startswith("env:"):
        return None
    return os.getenv(api_key_ref[4:].strip()) or None
