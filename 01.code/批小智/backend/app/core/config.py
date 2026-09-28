"""应用运行配置。

配置通过环境变量注入，避免把数据库、文件存储和外部模型密钥写死在代码中。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_URL = f"sqlite:///{(PROJECT_ROOT / 'backend' / 'data' / 'pixiaozhi.db').as_posix()}"
DEFAULT_STORAGE_ROOT = str(PROJECT_ROOT / 'backend' / 'data' / 'uploads')


def _as_int(name: str, default: int) -> int:
    """读取整数环境变量，非法值回退到安全默认值。"""

    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class Settings:
    """服务启动所需的最小配置。"""

    app_env: str
    app_name: str
    app_version: str
    database_url: str
    storage_root: str
    cors_origins: tuple[str, ...]
    max_file_size: int
    max_file_count: int
    worker_concurrency: int


def get_settings() -> Settings:
    """从环境变量读取配置并返回不可变配置对象。"""

    app_env = os.getenv("APP_ENV", "development").strip().lower() or "development"
    origins = tuple(
        item.strip()
        for item in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if item.strip()
    )
    if app_env not in {"development", "test"} and (not origins or "*" in origins):
        raise ValueError("非开发环境必须显式配置 CORS_ORIGINS，不能使用空值或通配符。")
    return Settings(
        app_env=app_env,
        app_name=os.getenv("APP_NAME", "批小智 API"),
        app_version=os.getenv("APP_VERSION", "0.1.0"),
        # 默认路径锚定到项目根目录，避免从 backend 子目录启动时误建空 SQLite 库。
        database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        storage_root=os.getenv("STORAGE_ROOT", DEFAULT_STORAGE_ROOT),
        cors_origins=origins,
        max_file_size=_as_int("MAX_FILE_SIZE", 50 * 1024 * 1024),
        max_file_count=_as_int("MAX_FILE_COUNT", 100),
        worker_concurrency=_as_int("WORKER_CONCURRENCY", 4),
    )
