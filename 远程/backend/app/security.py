from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .models import AuditLog, User, UserSession, utcnow


def password_policy_error(password: str) -> str | None:
    checks = (
        any(char.islower() for char in password),
        any(char.isupper() for char in password),
        any(char.isdigit() for char in password),
        any(not char.isalnum() for char in password),
    )
    if len(password) < 10 or not all(checks):
        return "密码至少10位，且必须包含大写字母、小写字母、数字和特殊字符"
    if len(password.encode("utf-8")) > 72:
        return "密码的 UTF-8 编码不能超过72字节"
    return None


def hash_password(password: str) -> str:
    rounds = 4 if get_settings().app_env == "test" else 12
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_for_session_token(token: str) -> str:
    return digest(f"csrf:{token}")


def new_session(db: Session, user: User, request: Request, settings: Settings) -> tuple[str, str, UserSession]:
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    token = secrets.token_urlsafe(48)
    csrf = csrf_for_session_token(token)
    session = UserSession(
        user_id=user.id,
        token_hash=digest(token),
        csrf_hash=digest(csrf),
        expires_at=utcnow() + timedelta(hours=settings.session_hours),
        user_agent=request.headers.get("user-agent", "")[:500],
        ip_address=request.client.host if request.client else None,
    )
    db.add(session)
    return token, csrf, session


def write_audit(
    db: Session,
    request: Request,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before_data=jsonable_encoder(before) if before is not None else None,
            after_data=jsonable_encoder(after) if after is not None else None,
            ip_address=request.client.host if request.client else None,
        )
    )


def _auth_error(code: str = "UNAUTHENTICATED", detail: str = "登录已失效，请重新登录") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": code, "message": detail})


def get_current_session(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> tuple[User, UserSession]:
    raw_token = request.cookies.get(settings.cookie_name)
    if not raw_token:
        raise _auth_error()
    session = db.scalar(select(UserSession).where(UserSession.token_hash == digest(raw_token)))
    if not session or session.expires_at <= utcnow():
        if session:
            db.delete(session)
            db.commit()
        raise _auth_error()
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        raise _auth_error("ACCOUNT_DISABLED", "账号已停用或不存在")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not secrets.compare_digest(digest(csrf), session.csrf_hash):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "CSRF_FAILED", "message": "请求安全校验失败，请刷新页面后重试"},
            )
    return user, session


def get_current_user(auth: Annotated[tuple[User, UserSession], Depends(get_current_session)]) -> User:
    return auth[0]


def get_ready_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PASSWORD_CHANGE_REQUIRED", "message": "首次登录必须修改初始密码"},
        )
    return user


def require_roles(*allowed: str):
    def dependency(user: Annotated[User, Depends(get_ready_user)]) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": "当前角色无权执行此操作"},
            )
        return user

    return dependency


Db = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
ReadyUser = Annotated[User, Depends(get_ready_user)]
