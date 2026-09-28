"""服务健康检查接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.core.runtime import check_sqlite

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live(request: Request) -> dict[str, str]:
    """检查进程是否仍能响应请求。"""

    settings = request.app.state.settings
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@router.get("/ready")
def ready(request: Request) -> dict[str, str | bool]:
    """检查数据库和文件存储是否满足接收业务请求的条件。"""

    settings = request.app.state.settings
    storage_ready = request.app.state.storage_root.is_dir()
    database_ready = check_sqlite(settings.database_url)
    if not storage_ready or not database_ready:
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "database": database_ready, "storage": storage_ready},
        )
    return {"status": "ready", "database": True, "storage": True}
