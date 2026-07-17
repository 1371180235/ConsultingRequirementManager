from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select

from .config import Settings, get_settings
from .models import Project, ProjectAccess, ROLE_VALUES, User, UserSession, utcnow
from .schemas import LoginIn, PasswordChangeIn, PasswordResetIn, UserCreate, UserPatch
from .security import (
    Db,
    csrf_for_session_token,
    get_current_session,
    hash_password,
    new_session,
    password_policy_error,
    require_roles,
    verify_password,
    write_audit,
)
from .services import bad_request


router = APIRouter(prefix="/api")


def validate_password_strength(value: str) -> None:
    if error := password_policy_error(value):
        raise bad_request("WEAK_PASSWORD", error)


def user_data(db: Db, user: User) -> dict:
    project_ids = db.scalars(select(ProjectAccess.project_id).where(ProjectAccess.user_id == user.id)).all()
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "locked_until": user.locked_until,
        "last_login_at": user.last_login_at,
        "project_ids": project_ids,
        "created_at": user.created_at,
    }


@router.post("/auth/login", summary="登录（不开放注册）")
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    db: Db,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    username = payload.username.strip().casefold()
    # Serialize all mutable authentication state and single-session replacement
    # for an existing account in one transaction.
    user = db.scalar(
        select(User).where(User.username == username).with_for_update()
    )
    now = utcnow()
    if user and user.locked_until and user.locked_until > now:
        write_audit(
            db,
            request,
            user.id,
            "login_blocked",
            "authentication",
            user.id,
            after={"username": username, "locked_until": user.locked_until},
        )
        db.commit()
        raise HTTPException(
            status_code=423,
            detail={"code": "ACCOUNT_LOCKED", "message": "登录失败次数过多，请稍后再试"},
        )
    valid = bool(user and user.is_active and verify_password(payload.password, user.password_hash))
    if not valid:
        if user and user.is_active:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.max_login_failures:
                user.locked_until = now + timedelta(minutes=settings.lock_minutes)
                user.failed_login_count = 0
        write_audit(
            db,
            request,
            user.id if user else None,
            "login_failed",
            "authentication",
            user.id if user else None,
            after={
                "username": username,
                "account_exists": user is not None,
                "account_active": bool(user and user.is_active),
                "locked_until": user.locked_until if user else None,
            },
        )
        db.commit()
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_CREDENTIALS", "message": "用户名或密码错误"},
        )
    assert user is not None
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    token, csrf, session = new_session(db, user, request, settings)
    write_audit(db, request, user.id, "login", "session", user.id)
    db.commit()
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        domain=settings.cookie_domain,
        path="/",
    )
    return {"user": user_data(db, user), "csrf_token": csrf, "expires_at": session.expires_at}


@router.get("/auth/me", summary="当前用户与新 CSRF 令牌")
def me(
    request: Request,
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session)],
    db: Db,
) -> dict:
    user, session = auth
    raw_token = request.cookies[get_settings().cookie_name]
    csrf = csrf_for_session_token(raw_token)
    return {"user": user_data(db, user), "csrf_token": csrf, "expires_at": session.expires_at}


@router.post("/auth/logout", summary="退出当前会话")
def logout(
    request: Request,
    response: Response,
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session)],
    db: Db,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    user, session = auth
    db.delete(session)
    write_audit(db, request, user.id, "logout", "session", user.id)
    db.commit()
    response.delete_cookie(settings.cookie_name, domain=settings.cookie_domain, path="/")
    return {"ok": True}


@router.post("/auth/change-password", summary="首登或日常修改密码")
def change_password(
    payload: PasswordChangeIn,
    request: Request,
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session)],
    db: Db,
) -> dict:
    user, _ = auth
    if not verify_password(payload.current_password, user.password_hash):
        raise bad_request("CURRENT_PASSWORD_INVALID", "当前密码错误")
    if verify_password(payload.new_password, user.password_hash):
        raise bad_request("PASSWORD_REUSED", "新密码不能与当前密码相同")
    validate_password_strength(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    write_audit(db, request, user.id, "change_password", "user", user.id)
    db.commit()
    return {"ok": True, "must_change_password": False}


Admin = Annotated[User, Depends(require_roles("admin"))]


@router.get("/users", summary="用户、角色和客户项目白名单")
def list_users(db: Db, _: Admin) -> list[dict]:
    return [user_data(db, item) for item in db.scalars(select(User).order_by(User.id)).all()]


def replace_access(db: Db, user: User, project_ids: list[int]) -> None:
    existing = set(db.scalars(select(Project.id).where(Project.id.in_(project_ids))).all()) if project_ids else set()
    if existing != set(project_ids):
        raise bad_request("PROJECT_NOT_FOUND", "白名单中包含不存在的项目")
    db.execute(delete(ProjectAccess).where(ProjectAccess.user_id == user.id))
    if user.role == "customer":
        db.add_all(ProjectAccess(user_id=user.id, project_id=project_id) for project_id in sorted(existing))


@router.post("/users", status_code=201, summary="管理员预建账号")
def create_user(payload: UserCreate, request: Request, db: Db, admin: Admin) -> dict:
    username = payload.username.strip().casefold()
    if db.scalar(select(User.id).where(User.username == username)):
        raise bad_request("USERNAME_EXISTS", "用户名已存在", 409)
    validate_password_strength(payload.initial_password)
    user = User(
        username=username,
        full_name=payload.full_name,
        role=payload.role,
        password_hash=hash_password(payload.initial_password),
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    replace_access(db, user, payload.project_ids)
    write_audit(db, request, admin.id, "create", "user", user.id, after={"role": user.role})
    db.commit()
    return user_data(db, user)


def ensure_admin_remains(db: Db, target: User, next_role: str | None = None, active: bool | None = None) -> None:
    removing = target.role == "admin" and ((next_role is not None and next_role != "admin") or active is False)
    if removing:
        # Lock the active administrator rows so two concurrent requests cannot both remove the last admins.
        active_admin_ids = db.scalars(
            select(User.id).where(User.role == "admin", User.is_active.is_(True)).with_for_update()
        ).all()
        if len(active_admin_ids) <= 1:
            raise bad_request("LAST_ADMIN", "系统必须保留至少一个可用管理员", 409)


@router.patch("/users/{user_id}", summary="角色、启停和客户项目授权")
def patch_user(user_id: int, payload: UserPatch, request: Request, db: Db, admin: Admin) -> dict:
    target = db.get(User, user_id)
    if not target:
        raise bad_request("USER_NOT_FOUND", "用户不存在", 404)
    if target.id == admin.id and payload.is_active is False:
        raise bad_request("SELF_DISABLE", "不能停用当前登录账号")
    if target.id == admin.id and payload.role is not None:
        raise bad_request("SELF_ROLE_CHANGE", "当前管理员不能修改自己的角色", 409)
    ensure_admin_remains(db, target, payload.role, payload.is_active)
    before = {"full_name": target.full_name, "role": target.role, "is_active": target.is_active}
    role_changed = payload.role is not None and payload.role != target.role
    for key in ("full_name", "role", "is_active"):
        value = getattr(payload, key)
        if value is not None:
            setattr(target, key, value)
    if payload.project_ids is not None:
        replace_access(db, target, payload.project_ids)
    elif payload.role is not None and payload.role != "customer":
        replace_access(db, target, [])
    if payload.is_active is False or role_changed:
        db.execute(delete(UserSession).where(UserSession.user_id == target.id))
    write_audit(db, request, admin.id, "update", "user", target.id, before=before, after={"role": target.role, "is_active": target.is_active})
    db.commit()
    return user_data(db, target)


@router.delete("/users/{user_id}", summary="停用账号（保留历史数据）")
def disable_user(user_id: int, request: Request, db: Db, admin: Admin) -> dict:
    target = db.get(User, user_id)
    if not target:
        raise bad_request("USER_NOT_FOUND", "用户不存在", 404)
    if target.id == admin.id:
        raise bad_request("SELF_DISABLE", "不能停用当前登录账号")
    ensure_admin_remains(db, target, active=False)
    target.is_active = False
    db.execute(delete(UserSession).where(UserSession.user_id == target.id))
    write_audit(db, request, admin.id, "disable", "user", target.id)
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/reset-password", summary="管理员重置密码")
def reset_password(user_id: int, payload: PasswordResetIn, request: Request, db: Db, admin: Admin) -> dict:
    target = db.get(User, user_id)
    if not target:
        raise bad_request("USER_NOT_FOUND", "用户不存在", 404)
    if target.id == admin.id:
        raise bad_request("ADMIN_SELF_RESET", "当前管理员不能重置自己的密码；忘记密码时必须由另一名管理员处理", 409)
    validate_password_strength(payload.new_password)
    target.password_hash = hash_password(payload.new_password)
    target.must_change_password = True
    target.failed_login_count = 0
    target.locked_until = None
    db.execute(delete(UserSession).where(UserSession.user_id == target.id))
    write_audit(db, request, admin.id, "reset_password", "user", target.id)
    db.commit()
    return {"ok": True, "must_change_password": True}
