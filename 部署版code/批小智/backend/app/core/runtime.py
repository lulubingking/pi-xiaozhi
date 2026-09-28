"""启动时的运行目录和健康检查辅助函数。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def sqlite_path(database_url: str) -> Path | None:
    """将当前支持的 SQLite URL 转换为本地路径。"""

    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw_path = database_url[len(prefix) :]
    if raw_path == ":memory:":
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else Path.cwd() / path


def ensure_runtime_directories(database_url: str, storage_root: str) -> None:
    """创建本地运行需要的目录，不修改原始作业文件。"""

    storage_path = Path(storage_root)
    storage_path.mkdir(parents=True, exist_ok=True)
    db_path = sqlite_path(database_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)


def check_sqlite(database_url: str) -> bool:
    """执行最小 SQLite 可用性检查。"""

    try:
        connection = sqlite3.connect(":memory:" if database_url.endswith(":memory:") else sqlite_path(database_url))
        try:
            connection.execute("SELECT 1")
        finally:
            connection.close()
        return True
    except (OSError, sqlite3.Error, TypeError):
        return False
