"""API 依赖：数据库会话和当前登录用户。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.utils import hash_token, parse_utc, utc_now
from app.db.session import get_db
from app.models import User, UserSession

SESSION_COOKIE_NAME = "pixiaozhi_session"


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """校验 HttpOnly 会话 Cookie，并把用户隔离边界固定在依赖层。"""

    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise ApiError(401, "AUTH_REQUIRED", "当前请求未登录，请先登录后再继续。")

    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(raw_token)))
    now = datetime.now(timezone.utc)
    if not session or session.revoked_at or (parse_utc(session.expires_at) and parse_utc(session.expires_at) <= now):
        raise ApiError(401, "AUTH_SESSION_EXPIRED", "登录会话已失效，请重新登录。")

    user = db.get(User, session.user_id)
    if not user or user.status != "active":
        raise ApiError(401, "AUTH_ACCOUNT_DISABLED", "当前账号不可用，请联系管理员处理。")

    session.last_seen_at = utc_now()
    db.commit()
    request.state.user = user
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        return None
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(raw_token)))
    if not session or session.revoked_at:
        return None
    expires_at = parse_utc(session.expires_at)
    if expires_at and expires_at <= datetime.now(timezone.utc):
        return None
    user = db.get(User, session.user_id)
    if not user or user.status != "active":
        return None
    return user
