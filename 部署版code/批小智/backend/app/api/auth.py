"""注册、登录、会话查询和注销接口。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import SESSION_COOKIE_NAME, get_optional_user
from app.api.errors import ApiError, success_payload
from app.core.config import get_settings
from app.core.utils import hash_password, hash_token, new_id, new_session_token, parse_utc, utc_now, verify_password
from app.db.session import get_db
from app.models import AuditLog, User, UserSession
from app.schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
SESSION_MAX_AGE = 30 * 24 * 60 * 60
SESSION_DEFAULT_AGE = 8 * 60 * 60
MAX_FAILED_LOGIN_COUNT = 5
LOCK_MINUTES = 15


def user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
    }


def write_audit(db: Session, request: Request, user_id: str, event_name: str, entity_id: str, entity_type: str = "user_session") -> None:
    db.add(
        AuditLog(
            id=new_id("audit"),
            user_id=user_id,
            event_name=event_name,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=getattr(request.state, "request_id", None),
            metadata_json=json.dumps({}, ensure_ascii=False),
            created_at=utc_now(),
        )
    )


def _set_session_cookie(response: Response, raw_token: str, expires: datetime, remember_me: bool) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=SESSION_MAX_AGE if remember_me else SESSION_DEFAULT_AGE,
        expires=expires,
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
        path="/",
    )


def _create_session(db: Session, request: Request, user: User, remember_me: bool, event_name: str = "auth.login") -> tuple[UserSession, str]:
    now = datetime.now(timezone.utc)
    raw_token = new_session_token()
    expires = now + timedelta(seconds=SESSION_MAX_AGE if remember_me else SESSION_DEFAULT_AGE)
    session = UserSession(
        id=new_id("sess"),
        user_id=user.id,
        token_hash=hash_token(raw_token),
        remember_me=1 if remember_me else 0,
        expires_at=expires.isoformat(timespec="seconds"),
        revoked_at=None,
        created_at=utc_now(),
        last_seen_at=utc_now(),
    )
    db.add(session)
    write_audit(db, request, user.id, event_name, session.id)
    return session, raw_token


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise ApiError(409, "AUTH_EMAIL_EXISTS", "该邮箱已经注册，请直接登录或更换邮箱。")

    now = utc_now()
    user = User(
        id=new_id("user"),
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        role="teacher",
        status="active",
        failed_login_count=0,
        locked_until=None,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        db.flush()
        session, raw_token = _create_session(db, request, user, remember_me=False, event_name="auth.register")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "AUTH_EMAIL_EXISTS", "该邮箱已经注册，请直接登录或更换邮箱。") from exc

    _set_session_cookie(response, raw_token, datetime.fromisoformat(session.expires_at), remember_me=False)
    return success_payload(request, {"user": user_payload(user), "expires_at": session.expires_at})


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email))
    now = datetime.now(timezone.utc)

    if user:
        locked_until = parse_utc(user.locked_until)
        if locked_until and locked_until > now:
            raise ApiError(401, "AUTH_ACCOUNT_LOCKED", "登录失败次数过多，账号暂时锁定，请稍后重试。")

    if not user or not verify_password(payload.password, user.password_hash):
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGIN_COUNT:
                user.locked_until = (now + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
                user.failed_login_count = 0
            user.updated_at = utc_now()
            db.commit()
        raise ApiError(401, "AUTH_INVALID_CREDENTIALS", "邮箱或密码错误，请检查后重试。")

    if user.status != "active":
        raise ApiError(401, "AUTH_ACCOUNT_DISABLED", "当前账号不可用，请联系管理员处理。")

    user.failed_login_count = 0
    user.locked_until = None
    user.updated_at = utc_now()
    session, raw_token = _create_session(db, request, user, remember_me=payload.remember_me)
    db.commit()
    _set_session_cookie(response, raw_token, datetime.fromisoformat(session.expires_at), remember_me=payload.remember_me)
    return success_payload(request, {"user": user_payload(user), "expires_at": session.expires_at})


@router.get("/session")
def session_info(request: Request, user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not user or not raw_token:
        return success_payload(request, {"authenticated": False, "user": None, "expires_at": None})
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(raw_token)))
    return success_payload(
        request,
        {"authenticated": True, "user": user_payload(user), "expires_at": session.expires_at if session else None},
    )


@router.post("/logout")
def logout(request: Request, response: Response, user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(raw_token)))
        if session and not session.revoked_at:
            session.revoked_at = utc_now()
            if user:
                write_audit(db, request, user.id, "auth.logout", session.id)
            db.commit()
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return success_payload(request, {"logged_out": True})
