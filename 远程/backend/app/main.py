from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from . import models  # noqa: F401 - registers SQLAlchemy metadata
from .auth_api import router as auth_router
from .config import get_settings
from .core_api import router as core_router
from .database import Base, SessionLocal, engine
from .models import UserSession, utcnow
from .security import digest, write_audit
from .seed import seed_database
from .support_api import router as support_router


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_tables:
        Base.metadata.create_all(engine)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.auto_seed:
        with SessionLocal() as db:
            seed_database(db, settings)
    yield


app = FastAPI(
    title=settings.app_name + " API",
    version="1.0.0",
    description=(
        "Linux 远程版服务端 API。浏览器仅使用 HttpOnly 会话 Cookie，"
        "数据库账号和密码仅保存在服务器环境变量。除登录外，所有写请求必须携带 X-CSRF-Token。"
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
)

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if response.status_code == status.HTTP_403_FORBIDDEN:
        try:
            with SessionLocal() as db:
                actor_id = None
                raw_token = request.cookies.get(settings.cookie_name)
                if raw_token:
                    session = db.scalar(
                        select(UserSession).where(UserSession.token_hash == digest(raw_token))
                    )
                    actor_id = session.user_id if session else None
                write_audit(
                    db,
                    request,
                    actor_id,
                    "permission_denied",
                    "http_request",
                    request.url.path,
                    after={"method": request.method, "status": response.status_code},
                )
                db.commit()
        except Exception:
            # A failed audit write must not replace the original authorization response.
            pass
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "VALIDATION_ERROR", "message": "请求参数校验失败", "errors": jsonable_encoder(exc.errors())}},
    )


def integrity_conflict_detail(exc: IntegrityError) -> dict[str, str]:
    message = f"{exc.orig} {exc}".lower()
    conflicts = (
        (
            ("uq_requirement_version_stable_key", "requirements.version_id, requirements.stable_key"),
            "STABLE_KEY_EXISTS",
            "同一版本内的需求稳定标识必须唯一",
        ),
        (
            ("ix_requirements_code", "requirements.code"),
            "REQUIREMENT_CODE_EXISTS",
            "需求编码已存在",
        ),
        (
            ("uq_plan_version_code", "delivery_versions.annual_plan_id, delivery_versions.code"),
            "VERSION_CODE_EXISTS",
            "当前年度的版本编码已存在",
        ),
        (
            ("uq_project_year", "annual_plans.project_id, annual_plans.year"),
            "PLAN_EXISTS",
            "该项目已存在同年度计划",
        ),
        (
            ("ix_projects_code", "projects.code"),
            "PROJECT_CODE_EXISTS",
            "项目编码已存在",
        ),
    )
    for markers, code, conflict_message in conflicts:
        if any(marker in message for marker in markers):
            return {"code": code, "message": conflict_message}
    return {"code": "DATA_CONFLICT", "message": "数据冲突或仍被其他业务引用"}


@app.exception_handler(IntegrityError)
async def integrity_error(_: Request, exc: IntegrityError):
    return JSONResponse(
        status_code=409,
        content={"detail": integrity_conflict_detail(exc)},
    )


app.include_router(auth_router, tags=["authentication and users"])
app.include_router(core_router, tags=["projects, versions and requirements"])
app.include_router(support_router, tags=["funds, artifacts and operations"])


@app.get("/health", tags=["system"], summary="服务和数据库健康检查")
def health(response: Response) -> dict:
    database = "ok"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    status_code = "ok" if database == "ok" else "degraded"
    if database != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": status_code, "database": database, "timestamp": utcnow()}


@app.get("/api", tags=["system"], summary="API 入口")
def api_root() -> dict:
    return {"name": settings.app_name, "version": app.version, "docs": "/api/docs", "health": "/health"}
