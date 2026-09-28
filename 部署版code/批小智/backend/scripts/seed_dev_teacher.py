"""创建或更新本地开发用虚拟教师账号。

脚本只允许在 ``APP_ENV=development`` 下运行，不会在正式环境自动植入账号。
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.utils import hash_password, new_id, utc_now
from app.db.session import SessionLocal
from app.models import AppSetting, User


DEV_EMAIL = os.getenv("DEV_TEACHER_EMAIL", "teacher.demo@pixiaozhi.local").strip().lower()
DEV_PASSWORD = os.getenv("DEV_TEACHER_PASSWORD", "").strip()
DEV_DISPLAY_NAME = "演示教师"


def main() -> None:
    settings = get_settings()
    if settings.app_env != "development":
        raise SystemExit("拒绝执行：seed_dev_teacher.py 只允许 APP_ENV=development。")

    db = SessionLocal()
    try:
        now = utc_now()
        user = db.scalar(select(User).where(User.email == DEV_EMAIL))
        if user is None:
            if not DEV_PASSWORD:
                raise SystemExit(
                    "首次创建开发教师账号需要设置 DEV_TEACHER_PASSWORD；"
                    "请在当前 PowerShell 会话中设置后再启动，密码不会写入代码或日志。"
                )
            user = User(
                id=new_id("user"),
                email=DEV_EMAIL,
                display_name=DEV_DISPLAY_NAME,
                password_hash=hash_password(DEV_PASSWORD),
                role="teacher",
                status="active",
                failed_login_count=0,
                locked_until=None,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
        else:
            user.display_name = DEV_DISPLAY_NAME
            user.role = "teacher"
            user.status = "active"
            user.failed_login_count = 0
            user.locked_until = None
            user.updated_at = now
            if DEV_PASSWORD:
                user.password_hash = hash_password(DEV_PASSWORD)

        db.flush()
        app_setting = db.scalar(select(AppSetting).where(AppSetting.user_id == user.id))
        if app_setting is None:
            db.add(
                AppSetting(
                    id=new_id("setting"),
                    user_id=user.id,
                    teacher_name=DEV_DISPLAY_NAME,
                    model_provider="DeepSeek",
                    model_name="deepseek-flash",
                    model_base_url=None,
                    api_key_ref=None,
                    storage_root=settings.storage_root,
                    retention_days=None,
                    queue_limit=20,
                    worker_concurrency=settings.worker_concurrency,
                    updated_at=now,
                )
            )
        else:
            app_setting.teacher_name = DEV_DISPLAY_NAME
            app_setting.updated_at = now

        db.commit()
        print(f"开发教师账号已就绪：{DEV_EMAIL}（密码不会输出；如需修改请设置 DEV_TEACHER_PASSWORD）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
