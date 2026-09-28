"""单教师运行设置接口。

模型配置保持 OpenAI 兼容接口的通用形态，DeepSeek 只是默认配置，不把供应商
写死在 API 契约中。网页端提交的 API Key 只保存在当前后端进程内，不写入数据库。
同时保留 env:变量名兼容方式，方便已有部署继续使用。
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, Request as FastAPIRequest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.errors import ApiError, success_payload
from app.core.config import get_settings
from app.core.model_config import get_model_config, has_runtime_api_key, resolve_api_key, set_runtime_api_key
from app.core.utils import new_id, utc_now
from app.db.session import get_db
from app.models import AppSetting, AuditLog, User
from app.schemas import SettingsModelPatch, SettingsProfilePatch, SettingsStoragePatch

router = APIRouter(tags=["settings"])
runtime_settings = get_settings()


def _get_setting(db: Session, user: User) -> AppSetting | None:
    return db.scalar(select(AppSetting).where(AppSetting.user_id == user.id))


def _effective_values(db: Session, user: User) -> dict:
    setting = _get_setting(db, user)
    model = get_model_config(db, user)
    return {
        "setting": setting,
        "teacher_name": setting.teacher_name if setting else user.display_name,
        "model_provider": model.provider_name,
        "model_name": model.model_name,
        "model_base_url": model.base_url,
        "api_key_ref": model.api_key_ref,
        "api_key_available": bool(resolve_api_key(model.api_key_ref, user.id)),
        "api_key_source": "网页临时输入" if has_runtime_api_key(user.id) else ("服务端环境变量" if resolve_api_key(model.api_key_ref) else None),
        "storage_root": setting.storage_root if setting else runtime_settings.storage_root,
        "retention_days": setting.retention_days if setting else None,
        "queue_limit": setting.queue_limit if setting else 20,
        "worker_concurrency": setting.worker_concurrency if setting else runtime_settings.worker_concurrency,
    }


def _ref_hint(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("env:"):
        return value
    return "已配置"


def _payload(values: dict) -> dict:
    return {
        "id": values["setting"].id if values["setting"] else None,
        "teacher_name": values["teacher_name"],
        "model_provider": values["model_provider"],
        "model_name": values["model_name"],
        "model_base_url": values["model_base_url"],
        "api_key_configured": values["api_key_available"],
        "api_key_ref_hint": values["api_key_source"] or _ref_hint(values["api_key_ref"]),
        "storage_root": values["storage_root"],
        "retention_days": values["retention_days"],
        "queue_limit": values["queue_limit"],
        "worker_concurrency": values["worker_concurrency"],
        "updated_at": values["setting"].updated_at if values["setting"] else None,
    }


def _ensure_setting(db: Session, user: User) -> AppSetting:
    setting = _get_setting(db, user)
    if setting:
        return setting
    now = utc_now()
    setting = AppSetting(
        id=new_id("setting"),
        user_id=user.id,
        teacher_name=user.display_name,
        model_provider="DeepSeek",
        model_name="deepseek-flash",
        model_base_url=None,
        api_key_ref=None,
        storage_root=runtime_settings.storage_root,
        retention_days=None,
        queue_limit=20,
        worker_concurrency=runtime_settings.worker_concurrency,
        updated_at=now,
    )
    db.add(setting)
    db.flush()
    return setting


def _audit(db: Session, user: User, event_name: str, setting_id: str, metadata: dict) -> None:
    db.add(
        AuditLog(
            id=new_id("audit"),
            user_id=user.id,
            event_name=event_name,
            entity_type="app_setting",
            entity_id=setting_id,
            request_id=None,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
            created_at=utc_now(),
        )
    )


@router.get("/settings")
def get_settings_endpoint(
    request: FastAPIRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return success_payload(request, _payload(_effective_values(db, user)))


@router.patch("/settings/profile")
def patch_profile(
    payload: SettingsProfilePatch,
    request: FastAPIRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    setting = _ensure_setting(db, user)
    setting.teacher_name = payload.teacher_name
    setting.updated_at = utc_now()
    _audit(db, user, "settings.profile.updated", setting.id, {"fields": ["teacher_name"]})
    db.commit()
    return success_payload(request, _payload(_effective_values(db, user)))


@router.patch("/settings/model")
def patch_model(
    payload: SettingsModelPatch,
    request: FastAPIRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    setting = _ensure_setting(db, user)
    if payload.clear_api_key:
        setting.api_key_ref = None
        set_runtime_api_key(user.id, None)
    elif payload.api_key is not None:
        # 明文只在请求体中经过一次，随后进入当前进程内存，不落库。
        set_runtime_api_key(user.id, payload.api_key)
        setting.api_key_ref = None
    elif payload.api_key_ref is not None:
        setting.api_key_ref = payload.api_key_ref
    setting.model_provider = payload.provider_name
    setting.model_name = payload.model_name
    setting.model_base_url = payload.model_base_url
    setting.updated_at = utc_now()
    _audit(
        db,
        user,
        "settings.model.updated",
        setting.id,
        {
            "provider": setting.model_provider,
            "model_name": setting.model_name,
            "has_api_key": bool(resolve_api_key(setting.api_key_ref, user.id)),
        },
    )
    db.commit()
    return success_payload(request, _payload(_effective_values(db, user)))


@router.post("/settings/model/test")
def test_model(
    request: FastAPIRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    values = _effective_values(db, user)
    base_url = values["model_base_url"]
    api_key = resolve_api_key(values["api_key_ref"], user.id)
    if not base_url or not api_key:
        raise ApiError(409, "MODEL_CONFIG_INCOMPLETE", "请先配置服务商、模型、Base URL 和网页端 API Key。")

    probe_url = f"{base_url.rstrip('/')}/models"
    probe = Request(
        probe_url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urlopen(probe, timeout=10) as response:
            status_code = int(response.status)
    except HTTPError as exc:
        raise ApiError(502, "MODEL_CONNECTION_FAILED", f"服务商返回 HTTP {exc.code}，请检查 Base URL、Key 和权限。") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ApiError(502, "MODEL_CONNECTION_FAILED", f"无法连接到服务商，请检查网络、Base URL 和代理配置：{exc.reason if isinstance(exc, URLError) else exc}") from exc

    return success_payload(
        request,
        {
            "connected": 200 <= status_code < 300,
            "provider_name": values["model_provider"],
            "model_name": values["model_name"],
            "status_code": status_code,
            "probe": "models",
        },
    )


@router.patch("/settings/storage")
def patch_storage(
    payload: SettingsStoragePatch,
    request: FastAPIRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    setting = _ensure_setting(db, user)
    setting.storage_root = payload.storage_root
    setting.retention_days = payload.retention_days
    setting.queue_limit = payload.queue_limit
    setting.worker_concurrency = payload.worker_concurrency
    setting.updated_at = utc_now()
    _audit(db, user, "settings.storage.updated", setting.id, {"fields": ["storage_root", "retention_days", "queue_limit", "worker_concurrency"]})
    db.commit()
    return success_payload(request, _payload(_effective_values(db, user)))
