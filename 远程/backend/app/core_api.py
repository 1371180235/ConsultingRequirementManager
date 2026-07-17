from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from .config import get_settings
from .models import (
    AnnualPlan,
    ArtifactChangeUpload,
    AuditLog,
    BudgetEntry,
    ChangeRequest,
    Deliverable,
    DeliveryVersion,
    FundingApplication,
    OperationFeedback,
    Project,
    ProjectAccess,
    Requirement,
    RequirementStatusHistory,
    RequirementTag,
    RoleDashboardLayout,
    Tag,
    User,
    VersionBaseline,
    DEFAULT_DASHBOARD_LAYOUTS,
    REQUIREMENT_STATES,
    REQUIREMENT_TRANSITIONS,
    ROLE_VALUES,
    utcnow,
)
from .schemas import (
    ChangeCreate,
    ChangeRequestCreate,
    DashboardLayoutPatch,
    DecisionIn,
    PlanCreate,
    PlanPatch,
    ProjectCreate,
    ProjectPatch,
    RequirementCreate,
    RequirementPatch,
    TagCreate,
    TransitionIn,
    VersionCreate,
    VersionPatch,
    WorkHoursIn,
)
from .security import Db, ReadyUser, require_roles, write_audit
from .services import (
    bad_request,
    ensure_project_access,
    ensure_requirement_access,
    money,
    project_ids_for,
    requirement_dict,
    validate_hierarchy,
)


router = APIRouter(prefix="/api")
MONEY_VISIBLE_ROLES = {"admin", "sales", "manager", "leader"}


def require_user_role(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise bad_request("FORBIDDEN", "当前角色无权执行此操作", 403)


def project_data(item: Project, include_money: bool = True) -> dict:
    data = {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "description": item.description,
        "status": item.status,
        "current_stage": item.current_stage,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_money:
        data["total_budget"] = money(item.total_budget)
    return data


def plan_data(item: AnnualPlan, include_money: bool = True) -> dict:
    data = {
        "id": item.id,
        "project_id": item.project_id,
        "year": item.year,
        "name": item.name,
        "target": item.target,
        "pain_points": item.pain_points,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_money:
        data["budget"] = money(item.budget)
    return data


def version_data(item: DeliveryVersion, plan: AnnualPlan | None = None, include_money: bool = True) -> dict:
    data = {
        "id": item.id,
        "annual_plan_id": item.annual_plan_id,
        "project_id": plan.project_id if plan else None,
        "year": plan.year if plan else None,
        "code": item.code,
        "name": item.name,
        "target": item.target,
        "status": item.status,
        "frozen_at": item.frozen_at,
        "released_at": item.released_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_money:
        data["budget"] = money(item.budget)
    return data


def budget_sum(db: Db, column, *criteria) -> Decimal:
    model = column.class_
    values = db.scalars(
        select(column)
        .where(*criteria)
        .order_by(model.id)
        .with_for_update()
    ).all()
    return sum((Decimal(value or 0) for value in values), Decimal(0))


def locked_child_exists(db: Db, model, *criteria) -> bool:
    return (
        db.scalar(
            select(model.id)
            .where(*criteria)
            .order_by(model.id)
            .limit(1)
            .with_for_update()
        )
        is not None
    )


def ensure_project_budget_capacity(db: Db, project: Project, candidate_plan_budget: Decimal, exclude_plan_id: int | None = None) -> None:
    criteria = [AnnualPlan.project_id == project.id]
    if exclude_plan_id is not None:
        criteria.append(AnnualPlan.id != exclude_plan_id)
    projected = budget_sum(db, AnnualPlan.budget, *criteria) + Decimal(candidate_plan_budget)
    if projected > Decimal(project.total_budget or 0):
        raise bad_request(
            "PROJECT_BUDGET_EXCEEDED",
            f"年度预算合计 {money(projected)} 不能超过项目总预算 {money(project.total_budget)}",
            409,
        )


def ensure_plan_budget_capacity(db: Db, plan: AnnualPlan, candidate_version_budget: Decimal, exclude_version_id: int | None = None) -> None:
    criteria = [DeliveryVersion.annual_plan_id == plan.id]
    if exclude_version_id is not None:
        criteria.append(DeliveryVersion.id != exclude_version_id)
    projected = budget_sum(db, DeliveryVersion.budget, *criteria) + Decimal(candidate_version_budget)
    if projected > Decimal(plan.budget or 0):
        raise bad_request(
            "PLAN_BUDGET_EXCEEDED",
            f"版本预算合计 {money(projected)} 不能超过年度预算 {money(plan.budget)}",
            409,
        )


def tag_ids_by_requirement(db: Db, ids: list[int]) -> dict[int, list[int]]:
    result = {item_id: [] for item_id in ids}
    if ids:
        for requirement_id, tag_id in db.execute(
            select(RequirementTag.requirement_id, RequirementTag.tag_id).where(RequirementTag.requirement_id.in_(ids))
        ):
            result[requirement_id].append(tag_id)
    return result


@router.get("/context", summary="全局项目/年度/版本联动上下文")
def context(db: Db, user: ReadyUser) -> dict:
    allowed = project_ids_for(db, user)
    projects_stmt = select(Project).where(Project.status != "deleted").order_by(Project.id)
    if allowed is not None:
        projects_stmt = projects_stmt.where(Project.id.in_(allowed or {-1}))
    projects = db.scalars(projects_stmt).all()
    project_ids = [item.id for item in projects]
    plans = db.scalars(select(AnnualPlan).where(AnnualPlan.project_id.in_(project_ids or [-1])).order_by(AnnualPlan.year)).all()
    plan_ids = [item.id for item in plans]
    plan_map = {item.id: item for item in plans}
    versions = db.scalars(
        select(DeliveryVersion).where(DeliveryVersion.annual_plan_id.in_(plan_ids or [-1])).order_by(DeliveryVersion.id)
    ).all()
    return {
        "projects": [project_data(item, user.role in MONEY_VISIBLE_ROLES) for item in projects],
        "plans": [plan_data(item, user.role in MONEY_VISIBLE_ROLES) for item in plans],
        "versions": [version_data(item, plan_map.get(item.annual_plan_id), user.role in MONEY_VISIBLE_ROLES) for item in versions],
        "tags": [
            {"id": item.id, "name": item.name, "color": item.color}
            for item in db.scalars(select(Tag).order_by(Tag.name)).all()
        ],
        "roles": ["admin", "customer", "sales", "manager", "developer", "operator", "leader"],
        "requirement_states": list(REQUIREMENT_STATES),
        "stages": [
            {"id": 1, "name": "宏观规划"},
            {"id": 2, "name": "规划细化"},
            {"id": 3, "name": "建设落地"},
            {"id": 4, "name": "招投标"},
            {"id": 5, "name": "项目交付验收"},
            {"id": 6, "name": "运维运营"},
        ],
    }


def dashboard_layout_data(role: str, item: RoleDashboardLayout | None) -> dict:
    return {
        "role": role,
        "component_keys": list(
            item.component_keys if item else DEFAULT_DASHBOARD_LAYOUTS[role]
        ),
        "updated_by": item.updated_by if item else None,
        "updated_at": item.updated_at if item else None,
        "is_custom": item is not None,
    }


@router.get("/dashboard-layout", summary="读取当前角色仪表盘布局")
def get_dashboard_layout(db: Db, user: ReadyUser) -> dict:
    item = db.scalar(
        select(RoleDashboardLayout).where(RoleDashboardLayout.role == user.role)
    )
    return dashboard_layout_data(user.role, item)


@router.get("/dashboard-layouts", summary="读取全部角色仪表盘布局")
def list_dashboard_layouts(db: Db, user: ReadyUser) -> list[dict]:
    require_user_role(user, "admin")
    items = {
        item.role: item for item in db.scalars(select(RoleDashboardLayout)).all()
    }
    return [dashboard_layout_data(role, items.get(role)) for role in ROLE_VALUES]


@router.patch("/dashboard-layouts/{role}", summary="保存角色仪表盘布局")
def patch_dashboard_layout(
    role: str,
    payload: DashboardLayoutPatch,
    request: Request,
    db: Db,
    user: ReadyUser,
) -> dict:
    require_user_role(user, "admin")
    if role not in ROLE_VALUES:
        raise bad_request("DASHBOARD_ROLE_INVALID", "仪表盘角色无效")
    item = db.scalar(
        select(RoleDashboardLayout).where(RoleDashboardLayout.role == role)
    )
    before = dashboard_layout_data(role, item)
    if item:
        item.component_keys = list(payload.component_keys)
        item.updated_by = user.id
        item.updated_at = utcnow()
    else:
        item = RoleDashboardLayout(
            role=role,
            component_keys=list(payload.component_keys),
            updated_by=user.id,
        )
        db.add(item)
    db.flush()
    after = dashboard_layout_data(role, item)
    write_audit(
        db,
        request,
        user.id,
        "update",
        "dashboard_layout",
        role,
        before=before,
        after=after,
    )
    db.commit()
    return after


@router.get("/dashboard", summary="分角色仪表盘")
def dashboard(
    db: Db,
    user: ReadyUser,
    project_id: int | None = None,
    annual_plan_id: int | None = None,
    version_id: int | None = None,
) -> dict:
    allowed = project_ids_for(db, user)
    if project_id:
        ensure_project_access(db, user, project_id)
    metric_project_ids = {project_id} if project_id else allowed
    req_stmt = select(Requirement)
    if allowed is not None:
        req_stmt = req_stmt.where(Requirement.project_id.in_(allowed or {-1}))
    if user.role == "customer":
        req_stmt = req_stmt.where(Requirement.requester_id == user.id)
    if project_id:
        req_stmt = req_stmt.where(Requirement.project_id == project_id)
    if annual_plan_id:
        plan = db.get(AnnualPlan, annual_plan_id)
        if not plan:
            raise bad_request("PLAN_NOT_FOUND", "年度计划不存在", 404)
        ensure_project_access(db, user, plan.project_id)
        if project_id and plan.project_id != project_id:
            raise bad_request("PLAN_SCOPE_MISMATCH", "年度计划与项目层级不匹配")
        metric_project_ids = {plan.project_id}
        req_stmt = req_stmt.where(Requirement.annual_plan_id == annual_plan_id)
    if version_id:
        version, version_plan = get_version_scope(db, user, version_id)
        if project_id and version_plan.project_id != project_id:
            raise bad_request("VERSION_SCOPE_MISMATCH", "版本与项目层级不匹配")
        if annual_plan_id and version.annual_plan_id != annual_plan_id:
            raise bad_request("VERSION_SCOPE_MISMATCH", "版本与年度计划层级不匹配")
        metric_project_ids = {version_plan.project_id}
        req_stmt = req_stmt.where(Requirement.version_id == version_id)
    requirements = db.scalars(req_stmt).all()
    status_counts = {state: 0 for state in REQUIREMENT_STATES}
    for item in requirements:
        status_counts[item.status] = status_counts.get(item.status, 0) + 1
    scope_projects = metric_project_ids
    if scope_projects is None:
        scope_projects = set(db.scalars(select(Project.id).where(Project.status != "deleted")).all())
    version_criteria = [AnnualPlan.project_id.in_(scope_projects or {-1})]
    artifact_criteria = [Deliverable.project_id.in_(scope_projects or {-1})]
    operation_criteria = [OperationFeedback.project_id.in_(scope_projects or {-1})]
    if annual_plan_id:
        version_criteria.append(DeliveryVersion.annual_plan_id == annual_plan_id)
        artifact_criteria.append(Deliverable.annual_plan_id == annual_plan_id)
    if version_id:
        version_criteria.append(DeliveryVersion.id == version_id)
        artifact_criteria.append(Deliverable.version_id == version_id)
        operation_criteria.append(OperationFeedback.version_id == version_id)
    versions_count = db.scalar(
        select(func.count()).select_from(DeliveryVersion).join(AnnualPlan).where(*version_criteria)
    ) or 0
    artifact_count_stmt = select(func.count()).select_from(Deliverable).where(*artifact_criteria)
    operation_count_stmt = select(func.count()).select_from(OperationFeedback).where(
        *operation_criteria, OperationFeedback.status.in_(("open", "processing"))
    )
    if user.role == "customer":
        artifact_count_stmt = artifact_count_stmt.outerjoin(
            Requirement, Deliverable.requirement_id == Requirement.id
        ).where(
            Requirement.requester_id == user.id,
            Deliverable.approval_status == "approved",
        )
        operation_count_stmt = operation_count_stmt.outerjoin(
            Requirement, OperationFeedback.requirement_id == Requirement.id
        ).where(
            or_(
                OperationFeedback.reporter_id == user.id,
                Requirement.requester_id == user.id,
            )
        )
    artifacts_count = db.scalar(artifact_count_stmt) or 0
    open_operations = db.scalar(operation_count_stmt) or 0
    estimated = sum((Decimal(item.estimated_budget or 0) for item in requirements), Decimal(0))
    actual = sum((Decimal(item.actual_cost or 0) for item in requirements), Decimal(0))
    recent_requirements = []
    for item in sorted(requirements, key=lambda value: value.updated_at, reverse=True)[:8]:
        recent = {
            "id": item.id,
            "code": item.code,
            "title": item.title,
            "status": item.status,
            "priority": item.priority,
            "version_id": item.version_id,
            "assignee_id": item.assignee_id,
            "updated_at": item.updated_at,
        }
        if user.role in MONEY_VISIBLE_ROLES:
            recent["estimated_budget"] = money(item.estimated_budget)
        recent_requirements.append(recent)
    return {
        "role": user.role,
        "metrics": {
            "requirements": len(requirements),
            "planning_pool": sum(1 for item in requirements if item.version_id is None),
            "versions": versions_count,
            "artifacts": artifacts_count,
            "open_operations": open_operations,
            "estimated_budget": money(estimated) if user.role in MONEY_VISIBLE_ROLES else None,
            "actual_cost": money(actual) if user.role in {"admin", "sales", "manager", "leader"} else None,
        },
        "status_distribution": [{"status": key, "count": value} for key, value in status_counts.items()],
        "priority_distribution": [
            {"priority": priority, "count": sum(1 for item in requirements if item.priority == priority)}
            for priority in ("urgent", "high", "medium", "low")
        ],
        "recent_requirements": recent_requirements,
    }


@router.get("/projects")
def list_projects(db: Db, user: ReadyUser) -> list[dict]:
    allowed = project_ids_for(db, user)
    stmt = select(Project).where(Project.status != "deleted").order_by(Project.id.desc())
    if allowed is not None:
        stmt = stmt.where(Project.id.in_(allowed or {-1}))
    return [project_data(item, user.role in MONEY_VISIBLE_ROLES) for item in db.scalars(stmt).all()]


@router.post("/projects", status_code=201)
def create_project(payload: ProjectCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    if db.scalar(select(Project.id).where(Project.code == payload.code)):
        raise bad_request("PROJECT_CODE_EXISTS", "项目编码已存在", 409)
    item = Project(**payload.model_dump())
    db.add(item)
    db.flush()
    write_audit(db, request, user.id, "create", "project", item.id, after=project_data(item))
    db.commit()
    return project_data(item)


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Db, user: ReadyUser) -> dict:
    return project_data(ensure_project_access(db, user, project_id), user.role in MONEY_VISIBLE_ROLES)


@router.patch("/projects/{project_id}")
def patch_project(project_id: int, payload: ProjectPatch, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    item = lock_project_scope(db, user, project_id)
    before = project_data(item)
    if payload.total_budget is not None:
        allocated = budget_sum(db, AnnualPlan.budget, AnnualPlan.project_id == item.id)
        if payload.total_budget < allocated:
            raise bad_request(
                "PROJECT_BUDGET_BELOW_PLANS",
                f"项目总预算不能低于已分配年度预算 {money(allocated)}",
                409,
            )
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    write_audit(db, request, user.id, "update", "project", item.id, before, project_data(item))
    db.commit()
    return project_data(item)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin")
    item = lock_project_scope(db, user, project_id)
    has_children = locked_child_exists(
        db, AnnualPlan, AnnualPlan.project_id == item.id
    )
    if has_children:
        raise bad_request("PROJECT_IN_USE", "项目已有年度计划，不能删除", 409)
    item.status = "deleted"
    write_audit(db, request, user.id, "delete", "project", item.id)
    db.commit()
    return {"ok": True}


@router.get("/plans")
def list_plans(db: Db, user: ReadyUser, project_id: int | None = None) -> list[dict]:
    allowed = project_ids_for(db, user)
    if project_id:
        ensure_project_access(db, user, project_id)
    stmt = select(AnnualPlan).order_by(AnnualPlan.year.desc(), AnnualPlan.id.desc())
    if project_id:
        stmt = stmt.where(AnnualPlan.project_id == project_id)
    elif allowed is not None:
        stmt = stmt.where(AnnualPlan.project_id.in_(allowed or {-1}))
    return [plan_data(item, user.role in MONEY_VISIBLE_ROLES) for item in db.scalars(stmt).all()]


@router.post("/plans", status_code=201)
def create_plan(payload: PlanCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    project = lock_project_scope(db, user, payload.project_id)
    ensure_project_budget_capacity(db, project, payload.budget)
    if db.scalar(select(AnnualPlan.id).where(AnnualPlan.project_id == payload.project_id, AnnualPlan.year == payload.year)):
        raise bad_request("PLAN_EXISTS", "该项目已存在同年度计划", 409)
    item = AnnualPlan(**payload.model_dump())
    db.add(item)
    db.flush()
    write_audit(db, request, user.id, "create", "annual_plan", item.id, after=plan_data(item))
    db.commit()
    return plan_data(item)


@router.patch("/plans/{plan_id}")
def patch_plan(plan_id: int, payload: PlanPatch, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    item, project = lock_plan_scope(db, user, plan_id)
    before = plan_data(item)
    if payload.budget is not None:
        version_total = budget_sum(db, DeliveryVersion.budget, DeliveryVersion.annual_plan_id == item.id)
        if payload.budget < version_total:
            raise bad_request(
                "PLAN_BUDGET_BELOW_VERSIONS",
                f"年度预算不能低于已分配版本预算 {money(version_total)}",
                409,
            )
        ensure_project_budget_capacity(db, project, payload.budget, item.id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    write_audit(db, request, user.id, "update", "annual_plan", item.id, before, plan_data(item))
    db.commit()
    return plan_data(item)


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin")
    item, _ = lock_plan_scope(db, user, plan_id)
    if locked_child_exists(
        db, DeliveryVersion, DeliveryVersion.annual_plan_id == item.id
    ):
        raise bad_request("PLAN_IN_USE", "年度计划已有版本，不能删除", 409)
    db.delete(item)
    write_audit(db, request, user.id, "delete", "annual_plan", item.id)
    db.commit()
    return {"ok": True}


@router.get("/versions")
def list_versions(
    db: Db,
    user: ReadyUser,
    project_id: int | None = None,
    annual_plan_id: int | None = None,
) -> list[dict]:
    allowed = project_ids_for(db, user)
    stmt = select(DeliveryVersion, AnnualPlan).join(AnnualPlan).order_by(DeliveryVersion.id.desc())
    if project_id:
        ensure_project_access(db, user, project_id)
        stmt = stmt.where(AnnualPlan.project_id == project_id)
    elif allowed is not None:
        stmt = stmt.where(AnnualPlan.project_id.in_(allowed or {-1}))
    if annual_plan_id:
        stmt = stmt.where(DeliveryVersion.annual_plan_id == annual_plan_id)
    return [version_data(version, plan, user.role in MONEY_VISIBLE_ROLES) for version, plan in db.execute(stmt).all()]


@router.post("/versions", status_code=201)
def create_version(payload: VersionCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    plan, _ = lock_plan_scope(db, user, payload.annual_plan_id)
    ensure_plan_budget_capacity(db, plan, payload.budget)
    if db.scalar(select(DeliveryVersion.id).where(DeliveryVersion.annual_plan_id == plan.id, DeliveryVersion.code == payload.code)):
        raise bad_request("VERSION_CODE_EXISTS", "当前年度的版本编码已存在", 409)
    item = DeliveryVersion(**payload.model_dump())
    db.add(item)
    db.flush()
    write_audit(db, request, user.id, "create", "version", item.id, after=version_data(item, plan))
    db.commit()
    return version_data(item, plan)


def get_version_scope(db: Db, user: User, version_id: int) -> tuple[DeliveryVersion, AnnualPlan]:
    item = db.get(DeliveryVersion, version_id)
    if not item:
        raise bad_request("VERSION_NOT_FOUND", "版本不存在", 404)
    plan = db.get(AnnualPlan, item.annual_plan_id)
    assert plan is not None
    ensure_project_access(db, user, plan.project_id)
    return item, plan


def lock_project_scope(db: Db, user: User, project_id: int) -> Project:
    item = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not item or item.status == "deleted":
        raise bad_request("PROJECT_NOT_FOUND", "项目不存在", 404)
    ensure_project_access(db, user, item.id)
    return item


def lock_plan_scope(db: Db, user: User, plan_id: int) -> tuple[AnnualPlan, Project]:
    initial = db.get(AnnualPlan, plan_id)
    if not initial:
        raise bad_request("PLAN_NOT_FOUND", "年度计划不存在", 404)
    project = lock_project_scope(db, user, initial.project_id)
    item = db.scalar(
        select(AnnualPlan)
        .where(AnnualPlan.id == plan_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not item:
        raise bad_request("PLAN_NOT_FOUND", "年度计划不存在", 404)
    if item.project_id != project.id:
        raise bad_request(
            "PLAN_SCOPE_CHANGED",
            "年度计划所属项目已被其他操作更新，请刷新后重试",
            409,
        )
    return item, project


def lock_version_scope(db: Db, user: User, version_id: int) -> tuple[DeliveryVersion, AnnualPlan]:
    initial = db.get(DeliveryVersion, version_id)
    if not initial:
        raise bad_request("VERSION_NOT_FOUND", "版本不存在", 404)
    plan, _ = lock_plan_scope(db, user, initial.annual_plan_id)
    item = db.scalar(
        select(DeliveryVersion)
        .where(DeliveryVersion.id == version_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not item:
        raise bad_request("VERSION_NOT_FOUND", "版本不存在", 404)
    if item.annual_plan_id != plan.id:
        raise bad_request(
            "VERSION_SCOPE_CHANGED",
            "版本所属年度计划已被其他操作更新，请刷新后重试",
            409,
        )
    return item, plan


def latest_baseline_sequence(db: Db, version_id: int) -> int:
    return int(
        db.scalar(
            select(VersionBaseline.sequence)
            .where(VersionBaseline.version_id == version_id)
            .order_by(VersionBaseline.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        or 0
    )


@router.patch("/versions/{version_id}")
def patch_version(version_id: int, payload: VersionPatch, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    item, plan = lock_version_scope(db, user, version_id)
    if item.status != "draft":
        raise bad_request("VERSION_LOCKED", "版本已冻结，只能通过变更申请调整", 409)
    before = version_data(item, plan)
    if payload.budget is not None:
        allocated = budget_sum(db, Requirement.allocated_budget, Requirement.version_id == item.id)
        if payload.budget < allocated:
            raise bad_request(
                "VERSION_BUDGET_BELOW_REQUIREMENTS",
                f"版本预算不能低于已分配需求预算 {money(allocated)}",
                409,
            )
        ensure_plan_budget_capacity(db, plan, payload.budget, item.id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    write_audit(db, request, user.id, "update", "version", item.id, before, version_data(item, plan))
    db.commit()
    return version_data(item, plan)


@router.delete("/versions/{version_id}")
def delete_version(version_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin")
    item, _ = lock_version_scope(db, user, version_id)
    if item.status != "draft" or locked_child_exists(
        db, Requirement, Requirement.version_id == item.id
    ):
        raise bad_request("VERSION_IN_USE", "只能删除没有需求的草稿版本", 409)
    db.delete(item)
    write_audit(db, request, user.id, "delete", "version", item.id)
    db.commit()
    return {"ok": True}


def build_snapshot(db: Db, version: DeliveryVersion, plan: AnnualPlan) -> dict:
    requirements = db.scalars(select(Requirement).where(Requirement.version_id == version.id).order_by(Requirement.code)).all()
    tag_map = tag_ids_by_requirement(db, [item.id for item in requirements])
    requirement_ids = [item.id for item in requirements]
    artifact_scope = Deliverable.version_id == version.id
    if requirement_ids:
        artifact_scope = or_(
            artifact_scope,
            Deliverable.requirement_id.in_(requirement_ids),
        )
    return {
        "schema_version": 1,
        "captured_at": utcnow().isoformat(),
        "project_id": plan.project_id,
        "annual_plan": {"id": plan.id, "year": plan.year, "budget": money(plan.budget)},
        "version": {
            "id": version.id,
            "code": version.code,
            "name": version.name,
            "target": version.target,
            "budget": money(version.budget),
        },
        "requirements": [
            {
                "code": item.code,
                "stable_key": item.stable_key,
                "requester_id": item.requester_id,
                "title": item.title,
                "description": item.description,
                "status": item.status,
                "priority": item.priority,
                "estimated_budget": money(item.estimated_budget),
                "allocated_budget": money(item.allocated_budget),
                "actual_cost": money(item.actual_cost),
                "assignee_id": item.assignee_id,
                "estimated_hours": money(item.estimated_hours),
                "actual_hours": money(item.actual_hours),
                "stakeholder_role": item.stakeholder_role,
                "source_requirement_id": item.source_requirement_id,
                "tag_ids": sorted(tag_map[item.id]),
            }
            for item in requirements
        ],
        "budget_entries": [
            {
                "id": item.id,
                "entry_type": item.entry_type,
                "amount": money(item.amount),
                "requirement_id": item.requirement_id,
                "description": item.description,
            }
            for item in db.scalars(select(BudgetEntry).where(BudgetEntry.version_id == version.id)).all()
        ],
        "artifacts": [
            {
                "id": item.id,
                "stage": item.stage,
                "category": item.category,
                "title": item.title,
                "storage_key": item.storage_key,
                "approval_status": item.approval_status,
            }
            for item in db.scalars(select(Deliverable).where(artifact_scope)).all()
        ],
    }


def visible_snapshot(snapshot: dict, user: User) -> dict:
    result = copy.deepcopy(snapshot)
    can_view_money = user.role in MONEY_VISIBLE_ROLES
    can_view_hours = user.role in {"admin", "manager", "developer", "leader"}
    if not can_view_money:
        result.get("annual_plan", {}).pop("budget", None)
        result.get("version", {}).pop("budget", None)
        result.pop("budget_entries", None)
    if user.role == "customer":
        result["requirements"] = [
            item for item in result.get("requirements", []) if item.get("requester_id") == user.id
        ]
        result["artifacts"] = []
    for requirement in result.get("requirements", []):
        if not can_view_money:
            for key in ("estimated_budget", "allocated_budget", "actual_cost"):
                requirement.pop(key, None)
        if not can_view_hours:
            for key in ("estimated_hours", "actual_hours"):
                requirement.pop(key, None)
    return result


@router.post("/versions/{version_id}/freeze", summary="冻结版本并生成完整基线")
def freeze_version(version_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    item, plan = lock_version_scope(db, user, version_id)
    if item.status != "draft":
        raise bad_request("VERSION_ALREADY_FROZEN", "版本不是可冻结的草稿状态", 409)
    sequence = latest_baseline_sequence(db, item.id) + 1
    baseline = VersionBaseline(
        version_id=item.id,
        sequence=sequence,
        snapshot=build_snapshot(db, item, plan),
        created_by=user.id,
    )
    db.add(baseline)
    item.status = "frozen"
    item.frozen_at = utcnow()
    write_audit(db, request, user.id, "freeze", "version", item.id, after={"baseline_sequence": sequence})
    db.commit()
    return {"version": version_data(item, plan), "baseline": {"id": baseline.id, "sequence": baseline.sequence, "snapshot": baseline.snapshot}}


@router.post("/versions/{version_id}/release")
def release_version(version_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    item, plan = lock_version_scope(db, user, version_id)
    if item.status != "frozen":
        raise bad_request("VERSION_NOT_FROZEN", "只能发布已冻结版本", 409)
    item.status = "released"
    item.released_at = utcnow()
    db.flush()
    sequence = latest_baseline_sequence(db, item.id) + 1
    db.add(
        VersionBaseline(
            version_id=item.id,
            sequence=sequence,
            snapshot=build_snapshot(db, item, plan),
            created_by=user.id,
        )
    )
    write_audit(db, request, user.id, "release", "version", item.id, after={"baseline_sequence": sequence})
    db.commit()
    return version_data(item, plan)


def latest_snapshot(db: Db, version: DeliveryVersion, plan: AnnualPlan) -> dict:
    baseline = db.scalar(
        select(VersionBaseline).where(VersionBaseline.version_id == version.id).order_by(VersionBaseline.sequence.desc()).limit(1)
    )
    return baseline.snapshot if baseline else build_snapshot(db, version, plan)


@router.get("/versions/compare", summary="跨年度版本需求与预算差异")
def compare_versions(
    db: Db,
    user: ReadyUser,
    left_id: int | None = None,
    right_id: int | None = None,
    project_id: int | None = None,
) -> dict:
    left = left_plan = None
    if left_id is not None:
        left, left_plan = get_version_scope(db, user, left_id)
        project_id = left_plan.project_id
    if project_id is None:
        raise bad_request("PROJECT_REQUIRED", "请先选择项目")
    ensure_project_access(db, user, project_id)
    count = db.scalar(
        select(func.count()).select_from(DeliveryVersion).join(AnnualPlan).where(AnnualPlan.project_id == project_id)
    ) or 0
    if count < 2:
        raise bad_request("INSUFFICIENT_VERSIONS", "至少需要两个版本才能比对")
    if left_id is None or right_id is None:
        raise bad_request("TWO_VERSIONS_REQUIRED", "请选择两个待比对版本")
    if left_id == right_id:
        raise bad_request("SAME_VERSION", "请选择两个不同版本")
    assert left is not None and left_plan is not None
    right, right_plan = get_version_scope(db, user, right_id)
    if left_plan.project_id != right_plan.project_id:
        raise bad_request("CROSS_PROJECT_COMPARE", "只能比对同一项目的版本")
    left_snapshot = visible_snapshot(latest_snapshot(db, left, left_plan), user)
    right_snapshot = visible_snapshot(latest_snapshot(db, right, right_plan), user)
    left_items = {item.get("stable_key", item["code"]): item for item in left_snapshot["requirements"]}
    right_items = {item.get("stable_key", item["code"]): item for item in right_snapshot["requirements"]}
    changed = []
    for stable_key in sorted(left_items.keys() & right_items.keys()):
        fields = sorted(
            key
            for key in left_items[stable_key].keys() | right_items[stable_key].keys()
            if key not in {"code", "stable_key"}
            and left_items[stable_key].get(key) != right_items[stable_key].get(key)
        )
        if fields:
            changed.append(
                {
                    "stable_key": stable_key,
                    "code": right_items[stable_key].get("code", left_items[stable_key].get("code")),
                    "fields": fields,
                    "left": left_items[stable_key],
                    "right": right_items[stable_key],
                }
            )
    result = {
        "left": {**left_snapshot["version"], "year": left_plan.year},
        "right": {**right_snapshot["version"], "year": right_plan.year},
        "requirements": {
            "added": [right_items[key] for key in sorted(right_items.keys() - left_items.keys())],
            "removed": [left_items[key] for key in sorted(left_items.keys() - right_items.keys())],
            "changed": changed,
            "unchanged_count": len(left_items.keys() & right_items.keys()) - len(changed),
        },
        "cross_year": left_plan.year != right_plan.year,
    }
    if user.role in MONEY_VISIBLE_ROLES:
        result["budget"] = {
            "left": left_snapshot["version"]["budget"],
            "right": right_snapshot["version"]["budget"],
            "difference": money(Decimal(right_snapshot["version"]["budget"]) - Decimal(left_snapshot["version"]["budget"])),
        }
    return result


@router.get("/tags")
def list_tags(db: Db, _: ReadyUser) -> list[dict]:
    return [{"id": item.id, "name": item.name, "color": item.color} for item in db.scalars(select(Tag).order_by(Tag.name)).all()]


@router.post("/tags", status_code=201)
def create_tag(payload: TagCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader", "manager")
    if db.scalar(select(Tag.id).where(Tag.name == payload.name)):
        raise bad_request("TAG_EXISTS", "标签已存在", 409)
    item = Tag(**payload.model_dump())
    db.add(item)
    db.flush()
    write_audit(db, request, user.id, "create", "tag", item.id)
    db.commit()
    return {"id": item.id, "name": item.name, "color": item.color}


@router.get("/requirements")
def list_requirements(
    db: Db,
    user: ReadyUser,
    project_id: int | None = None,
    annual_plan_id: int | None = None,
    version_id: int | None = None,
    planning_pool: bool | None = None,
    status: str | None = None,
    assignee_id: int | None = None,
) -> list[dict]:
    allowed = project_ids_for(db, user)
    stmt = select(Requirement).order_by(Requirement.updated_at.desc())
    if project_id:
        ensure_project_access(db, user, project_id)
        stmt = stmt.where(Requirement.project_id == project_id)
    elif allowed is not None:
        stmt = stmt.where(Requirement.project_id.in_(allowed or {-1}))
    if user.role == "customer":
        stmt = stmt.where(Requirement.requester_id == user.id)
    if annual_plan_id:
        stmt = stmt.where(Requirement.annual_plan_id == annual_plan_id)
    if version_id:
        stmt = stmt.where(Requirement.version_id == version_id)
    if planning_pool is True:
        stmt = stmt.where(Requirement.version_id.is_(None))
    if status:
        stmt = stmt.where(Requirement.status == status)
    if assignee_id:
        stmt = stmt.where(Requirement.assignee_id == assignee_id)
    items = db.scalars(stmt).all()
    tags = tag_ids_by_requirement(db, [item.id for item in items])
    return [requirement_dict(item, tags[item.id], user) for item in items]


def set_requirement_tags(db: Db, requirement_id: int, tag_ids: list[int]) -> None:
    unique = set(tag_ids)
    found = set(db.scalars(select(Tag.id).where(Tag.id.in_(unique))).all()) if unique else set()
    if found != unique:
        raise bad_request("TAG_NOT_FOUND", "标签中包含不存在的项")
    db.execute(delete(RequirementTag).where(RequirementTag.requirement_id == requirement_id))
    db.add_all(RequirementTag(requirement_id=requirement_id, tag_id=tag_id) for tag_id in sorted(found))


def ensure_stable_key_available(
    db: Db,
    project_id: int,
    version_id: int | None,
    stable_key: str,
    exclude_requirement_id: int | None = None,
) -> None:
    stmt = select(Requirement.id).where(Requirement.stable_key == stable_key)
    if version_id is None:
        stmt = stmt.where(Requirement.project_id == project_id, Requirement.version_id.is_(None))
    else:
        stmt = stmt.where(Requirement.version_id == version_id)
    if exclude_requirement_id is not None:
        stmt = stmt.where(Requirement.id != exclude_requirement_id)
    if db.scalar(stmt.order_by(Requirement.id).limit(1).with_for_update()):
        raise bad_request("STABLE_KEY_EXISTS", "同一版本内的需求稳定标识必须唯一", 409)


def validate_source_requirement(
    db: Db,
    project_id: int,
    source_id: int | None,
    current_id: int | None = None,
    user: User | None = None,
) -> None:
    if source_id is None:
        return
    source = db.get(Requirement, source_id)
    if not source or source.project_id != project_id:
        raise bad_request("SOURCE_REQUIREMENT_INVALID", "原需求不存在或不属于同一项目")
    if user is not None:
        ensure_requirement_access(user, source)
    if current_id == source_id:
        raise bad_request("SOURCE_REQUIREMENT_SELF", "需求不能关联自身")
    visited: set[int] = set()
    while source:
        if source.id in visited or (current_id is not None and source.id == current_id):
            raise bad_request("SOURCE_REQUIREMENT_CYCLE", "原需求关联不能形成循环")
        visited.add(source.id)
        if source.source_requirement_id is None:
            break
        source = db.get(Requirement, source.source_requirement_id)
        if not source or source.project_id != project_id:
            raise bad_request("SOURCE_REQUIREMENT_INVALID", "原需求链包含无效或跨项目关联")


@router.post("/requirements", status_code=201)
def create_requirement(payload: RequirementCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    if user.role not in MONEY_VISIBLE_ROLES and "estimated_budget" in payload.model_fields_set:
        raise bad_request("FINANCE_FIELD_FORBIDDEN", "当前角色提交需求时不能填写项目资金或成本字段", 403)
    if payload.stable_key is not None and user.role not in {"admin", "leader", "manager"}:
        raise bad_request("STABLE_KEY_FORBIDDEN", "只有管理员、咨询负责人或项目经理可以指定需求稳定标识", 403)
    if payload.version_id is not None:
        version, plan = lock_version_scope(db, user, payload.version_id)
        if plan.project_id != payload.project_id or (
            payload.annual_plan_id is not None
            and version.annual_plan_id != payload.annual_plan_id
        ):
            raise bad_request(
                "VERSION_SCOPE_MISMATCH",
                "版本与项目或年度计划层级不匹配",
            )
        if version.status != "draft":
            raise bad_request("VERSION_LOCKED", "已冻结版本不能直接新增需求", 409)
    else:
        lock_project_scope(db, user, payload.project_id)
        _, plan, _, _ = validate_hierarchy(
            db, payload.project_id, payload.annual_plan_id
        )
    if db.scalar(select(Requirement.id).where(Requirement.code == payload.code)):
        raise bad_request("REQUIREMENT_CODE_EXISTS", "需求编码已存在", 409)
    stable_key = (payload.stable_key or payload.code).strip()
    if not stable_key:
        raise bad_request("STABLE_KEY_INVALID", "需求稳定标识不能为空")
    ensure_stable_key_available(db, payload.project_id, payload.version_id, stable_key)
    validate_source_requirement(db, payload.project_id, payload.source_requirement_id, user=user)
    values = payload.model_dump(exclude={"tag_ids"})
    values["annual_plan_id"] = plan.id if plan else None
    values["stable_key"] = stable_key
    item = Requirement(**values, requester_id=user.id, status="draft")
    db.add(item)
    db.flush()
    set_requirement_tags(db, item.id, payload.tag_ids)
    write_audit(db, request, user.id, "create", "requirement", item.id, after={"code": item.code, "version_id": item.version_id})
    db.commit()
    return requirement_dict(item, payload.tag_ids, user)


def get_requirement_scope(db: Db, user: User, requirement_id: int) -> Requirement:
    item = db.get(Requirement, requirement_id)
    if not item:
        raise bad_request("REQUIREMENT_NOT_FOUND", "需求不存在", 404)
    ensure_project_access(db, user, item.project_id)
    ensure_requirement_access(user, item)
    return item


def lock_requirement_scope(
    db: Db, user: User, requirement_id: int
) -> tuple[Requirement, DeliveryVersion | None]:
    initial = get_requirement_scope(db, user, requirement_id)
    initial_project_id = initial.project_id
    initial_version_id = initial.version_id
    version = None
    if initial_version_id is None:
        lock_project_scope(db, user, initial_project_id)
    else:
        version, _ = lock_version_scope(db, user, initial_version_id)
    item = db.scalar(
        select(Requirement)
        .where(Requirement.id == requirement_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not item:
        raise bad_request("REQUIREMENT_NOT_FOUND", "需求不存在", 404)
    if (
        item.project_id != initial_project_id
        or item.version_id != initial_version_id
    ):
        raise bad_request(
            "REQUIREMENT_SCOPE_CHANGED",
            "需求所属版本已被其他操作更新，请刷新后重试",
            409,
        )
    ensure_project_access(db, user, item.project_id)
    ensure_requirement_access(user, item)
    return item, version


@router.get("/requirements/{requirement_id}")
def get_requirement(requirement_id: int, db: Db, user: ReadyUser) -> dict:
    item = get_requirement_scope(db, user, requirement_id)
    tag_ids = db.scalars(select(RequirementTag.tag_id).where(RequirementTag.requirement_id == item.id)).all()
    return requirement_dict(item, tag_ids, user)


@router.patch("/requirements/{requirement_id}")
def patch_requirement(requirement_id: int, payload: RequirementPatch, request: Request, db: Db, user: ReadyUser) -> dict:
    item = get_requirement_scope(db, user, requirement_id)
    management_roles = {"admin", "leader", "manager"}
    submitter_fields = {"title", "description", "source_requirement_id", "tag_ids"}
    if user.role == "sales":
        submitter_fields.add("estimated_budget")
    if user.role in {"sales", "customer"}:
        if item.requester_id != user.id or item.status != "draft":
            raise bad_request("FORBIDDEN", "只能修改自己提交的草稿需求", 403)
        forbidden_fields = payload.model_fields_set - submitter_fields
        if forbidden_fields:
            raise bad_request("REQUIREMENT_FIELD_FORBIDDEN", "当前角色无权修改需求规划核心字段", 403)
    elif user.role not in management_roles:
        raise bad_request("FORBIDDEN", "当前角色无权修改需求范围", 403)
    if user.role not in MONEY_VISIBLE_ROLES and "estimated_budget" in payload.model_fields_set:
        raise bad_request("FINANCE_FIELD_FORBIDDEN", "当前角色无权修改需求资金或成本字段", 403)
    values = payload.model_dump(exclude_unset=True, exclude={"tag_ids"})
    initial_version_id = item.version_id
    next_version_id = values.get("version_id", initial_version_id)
    lock_project_scope(db, user, item.project_id)
    version_scopes: dict[int, tuple[DeliveryVersion, AnnualPlan]] = {}
    for locked_version_id in sorted(
        {value for value in (initial_version_id, next_version_id) if value is not None}
    ):
        version_scopes[locked_version_id] = lock_version_scope(
            db, user, locked_version_id
        )
    item = db.scalar(
        select(Requirement)
        .where(Requirement.id == requirement_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not item:
        raise bad_request("REQUIREMENT_NOT_FOUND", "需求不存在", 404)
    if item.version_id != initial_version_id:
        raise bad_request(
            "REQUIREMENT_SCOPE_CHANGED",
            "需求所属版本已被其他操作更新，请刷新后重试",
            409,
        )
    ensure_project_access(db, user, item.project_id)
    ensure_requirement_access(user, item)
    if user.role in {"sales", "customer"} and (
        item.requester_id != user.id or item.status != "draft"
    ):
        raise bad_request("FORBIDDEN", "只能修改自己提交的草稿需求", 403)
    if initial_version_id is not None:
        current_version, _ = version_scopes[initial_version_id]
        if current_version.status != "draft":
            raise bad_request(
                "VERSION_LOCKED",
                "已冻结版本的需求只能通过变更申请调整",
                409,
            )
    if next_version_id:
        next_version, next_plan = version_scopes[next_version_id]
        if next_plan.project_id != item.project_id:
            raise bad_request(
                "VERSION_SCOPE_MISMATCH", "版本与需求所属项目层级不匹配"
            )
        if next_version.status != "draft":
            raise bad_request("VERSION_LOCKED", "不能将需求排入已冻结版本", 409)
        values["annual_plan_id"] = next_plan.id
    next_stable_key = values.get("stable_key", item.stable_key)
    if not isinstance(next_stable_key, str) or not next_stable_key.strip():
        raise bad_request("STABLE_KEY_INVALID", "需求稳定标识不能为空")
    next_stable_key = next_stable_key.strip()
    if "stable_key" in values:
        values["stable_key"] = next_stable_key
    ensure_stable_key_available(db, item.project_id, next_version_id, next_stable_key, item.id)
    source_id = values.get("source_requirement_id", item.source_requirement_id)
    validate_source_requirement(db, item.project_id, source_id, item.id, user)
    before = {"version_id": item.version_id, "title": item.title, "estimated_budget": money(item.estimated_budget)}
    for key, value in values.items():
        setattr(item, key, value)
    if payload.tag_ids is not None:
        set_requirement_tags(db, item.id, payload.tag_ids)
    write_audit(db, request, user.id, "update", "requirement", item.id, before, {"version_id": item.version_id, "title": item.title})
    db.commit()
    tags = db.scalars(select(RequirementTag.tag_id).where(RequirementTag.requirement_id == item.id)).all()
    return requirement_dict(item, tags, user)


@router.delete("/requirements/{requirement_id}")
def delete_requirement(requirement_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    item, _ = lock_requirement_scope(db, user, requirement_id)
    if item.status != "draft" or item.version_id is not None:
        raise bad_request("REQUIREMENT_IN_USE", "只能删除待规划池中的草稿需求", 409)
    if user.role == "customer" and item.requester_id != user.id:
        raise bad_request("FORBIDDEN", "无权删除该需求", 403)
    require_user_role(user, "admin", "leader", "manager", "customer")
    db.delete(item)
    write_audit(db, request, user.id, "delete", "requirement", item.id)
    db.commit()
    return {"ok": True}


@router.post("/requirements/{requirement_id}/transition", summary="受控需求状态机")
def transition_requirement(requirement_id: int, payload: TransitionIn, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader", "manager", "developer")
    item, _ = lock_requirement_scope(db, user, requirement_id)
    if user.role == "developer" and item.assignee_id != user.id:
        raise bad_request("FORBIDDEN", "研发人员只能流转自己领取的需求", 403)
    allowed = REQUIREMENT_TRANSITIONS.get(item.status, ())
    if payload.status not in allowed:
        raise bad_request("INVALID_TRANSITION", f"需求不能从 {item.status} 直接流转到 {payload.status}；可选状态：{', '.join(allowed) or '无'}")
    if payload.status == "scheduled" and item.version_id is None:
        raise bad_request("VERSION_REQUIRED", "需求排期前必须关联落地版本")
    before = item.status
    item.status = payload.status
    db.add(
        RequirementStatusHistory(
            requirement_id=item.id,
            from_status=before,
            to_status=item.status,
            transition_note=payload.note.strip(),
            changed_by=user.id,
        )
    )
    write_audit(db, request, user.id, "transition", "requirement", item.id, {"status": before}, {"status": item.status, "note": payload.note.strip()})
    db.commit()
    tags = db.scalars(select(RequirementTag.tag_id).where(RequirementTag.requirement_id == item.id)).all()
    return requirement_dict(item, tags, user)


@router.get("/requirements/{requirement_id}/history", summary="需求状态流转历史")
def requirement_history(requirement_id: int, db: Db, user: ReadyUser) -> list[dict]:
    get_requirement_scope(db, user, requirement_id)
    return [
        {
            "id": item.id,
            "requirement_id": item.requirement_id,
            "from_status": item.from_status,
            "to_status": item.to_status,
            "note": item.transition_note,
            "changed_by": item.changed_by,
            "changed_at": item.changed_at,
        }
        for item in db.scalars(
            select(RequirementStatusHistory)
            .where(RequirementStatusHistory.requirement_id == requirement_id)
            .order_by(RequirementStatusHistory.id.desc())
        ).all()
    ]


@router.post("/requirements/{requirement_id}/claim", summary="研发领取任务")
def claim_requirement(requirement_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "developer")
    item, _ = lock_requirement_scope(db, user, requirement_id)
    if item.version_id is None or item.status not in {"scheduled", "developing"}:
        raise bad_request("NOT_CLAIMABLE", "只能领取已排期且已关联版本的需求", 409)
    if item.assignee_id and item.assignee_id != user.id:
        raise bad_request("ALREADY_CLAIMED", "需求已被其他研发人员领取", 409)
    item.assignee_id = user.id
    write_audit(db, request, user.id, "claim", "requirement", item.id)
    db.commit()
    tags = db.scalars(select(RequirementTag.tag_id).where(RequirementTag.requirement_id == item.id)).all()
    return requirement_dict(item, tags, user)


@router.patch("/requirements/{requirement_id}/hours", summary="研发评估与填报工时")
def update_hours(requirement_id: int, payload: WorkHoursIn, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "manager", "developer", "leader")
    item, _ = lock_requirement_scope(db, user, requirement_id)
    if user.role == "developer" and item.assignee_id != user.id:
        raise bad_request("FORBIDDEN", "研发人员只能填报自己领取的需求", 403)
    before = {"estimated_hours": money(item.estimated_hours), "actual_hours": money(item.actual_hours)}
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    write_audit(db, request, user.id, "update_hours", "requirement", item.id, before, {"estimated_hours": money(item.estimated_hours), "actual_hours": money(item.actual_hours)})
    db.commit()
    tags = db.scalars(select(RequirementTag.tag_id).where(RequirementTag.requirement_id == item.id)).all()
    return requirement_dict(item, tags, user)


@router.get("/versions/{version_id}/baselines")
def list_baselines(version_id: int, db: Db, user: ReadyUser) -> list[dict]:
    get_version_scope(db, user, version_id)
    return [
        {"id": item.id, "version_id": item.version_id, "sequence": item.sequence, "snapshot": visible_snapshot(item.snapshot, user), "created_at": item.created_at}
        for item in db.scalars(select(VersionBaseline).where(VersionBaseline.version_id == version_id).order_by(VersionBaseline.sequence.desc())).all()
    ]


@router.get("/changes")
def list_changes(db: Db, user: ReadyUser, version_id: int | None = None) -> list[dict]:
    stmt = select(ChangeRequest).join(DeliveryVersion).join(AnnualPlan).order_by(ChangeRequest.id.desc())
    if version_id:
        get_version_scope(db, user, version_id)
        stmt = stmt.where(ChangeRequest.version_id == version_id)
    else:
        allowed = project_ids_for(db, user)
        if allowed is not None:
            stmt = stmt.where(AnnualPlan.project_id.in_(allowed or {-1}))
    if user.role == "customer":
        stmt = stmt.where(ChangeRequest.requested_by == user.id)
    items = db.scalars(stmt).all()
    result = []
    for item in items:
        version, _ = get_version_scope(db, user, item.version_id)
        data = {key: getattr(item, key) for key in ("id", "version_id", "title", "reason", "change_type", "payload", "expected_baseline_sequence", "status", "requested_by", "decided_by", "decision_note", "decided_at", "applied_by", "applied_at", "created_at")}
        if user.role not in MONEY_VISIBLE_ROLES:
            visible_payload = copy.deepcopy(data["payload"] or {})
            if isinstance(visible_payload.get("version"), dict):
                visible_payload["version"].pop("budget", None)
            for operation in visible_payload.get("requirements", []):
                if not isinstance(operation, dict):
                    continue
                for field_name in ("data", "fields"):
                    if isinstance(operation.get(field_name), dict):
                        operation[field_name].pop("estimated_budget", None)
            data["payload"] = visible_payload
        data["staged_artifacts"] = [
            staged_artifact_data(upload)
            for upload in db.scalars(
                select(ArtifactChangeUpload).where(
                    ArtifactChangeUpload.change_request_id == item.id
                )
            ).all()
        ]
        result.append(data)
    return result


@router.get("/change-requests", summary="版本变更申请列表")
def list_change_requests(db: Db, user: ReadyUser, version_id: int | None = None) -> list[dict]:
    return list_changes(db, user, version_id)


def change_conflict_targets(payload: dict) -> set[tuple[str, str]]:
    targets: set[tuple[str, str]] = set()
    version_changes = payload.get("version")
    if isinstance(version_changes, dict):
        targets.update(("version", str(field)) for field in version_changes)
    requirements = payload.get("requirements")
    if isinstance(requirements, list):
        for operation in requirements:
            if not isinstance(operation, dict):
                continue
            requirement_id = operation.get("requirement_id")
            if isinstance(requirement_id, int):
                targets.add(("requirement", str(requirement_id)))
            data = operation.get("data")
            if isinstance(data, dict):
                code = data.get("code")
                stable_key = data.get("stable_key") or code
                if isinstance(code, str):
                    targets.add(("requirement_code", code.casefold()))
                if isinstance(stable_key, str):
                    targets.add(("requirement_stable_key", stable_key.casefold()))
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        for operation in artifacts:
            if not isinstance(operation, dict):
                continue
            artifact_id = operation.get("artifact_id")
            if isinstance(artifact_id, int):
                targets.add(("artifact", str(artifact_id)))
            data = operation.get("data")
            if isinstance(data, dict):
                title = data.get("title")
                stage = data.get("stage")
                requirement_id = data.get("requirement_id")
                if isinstance(title, str) and isinstance(stage, int):
                    targets.add(
                        (
                            "artifact_add",
                            f"{stage}:{requirement_id or 0}:{title.casefold()}",
                        )
                    )
    return targets


def artifact_version_id(db: Db, item: Deliverable) -> int | None:
    if item.version_id is not None:
        return item.version_id
    if item.requirement_id is None:
        return None
    return db.scalar(
        select(Requirement.version_id).where(Requirement.id == item.requirement_id)
    )


def change_artifact(db: Db, version_id: int, artifact_id: object) -> Deliverable:
    item = db.get(Deliverable, artifact_id) if isinstance(artifact_id, int) else None
    if not item or artifact_version_id(db, item) != version_id:
        raise bad_request(
            "CHANGE_PAYLOAD_INVALID",
            "变更成果物不存在或不属于当前版本",
        )
    return item


def staged_artifact_data(item: ArtifactChangeUpload) -> dict:
    return {
        "token": item.token,
        "version_id": item.version_id,
        "change_request_id": item.change_request_id,
        "stage": item.stage,
        "category": item.category,
        "title": item.title,
        "requirement_id": item.requirement_id,
        "original_filename": item.original_filename,
        "content_type": item.content_type,
        "size_bytes": item.size_bytes,
        "sha256_hex": item.sha256_hex,
        "uploaded_by": item.uploaded_by,
        "created_at": item.created_at,
    }


def change_upload(
    db: Db,
    change: ChangeRequest,
    token: object,
) -> ArtifactChangeUpload:
    item = db.scalar(
        select(ArtifactChangeUpload)
        .where(
            ArtifactChangeUpload.token == token,
            ArtifactChangeUpload.change_request_id == change.id,
        )
        .with_for_update()
    ) if isinstance(token, str) else None
    if not item or item.version_id != change.version_id:
        raise bad_request(
            "CHANGE_UPLOAD_INVALID",
            "变更附件不存在、未绑定当前申请或已被处理",
            409,
        )
    if item.expected_baseline_sequence != change.expected_baseline_sequence:
        raise bad_request(
            "CHANGE_UPLOAD_STALE",
            "变更附件与申请基线不一致，请取消后重新上传",
            409,
        )
    return item


def verify_change_upload_file(item: ArtifactChangeUpload) -> None:
    try:
        root = get_settings().upload_dir.resolve()
        target = (root / item.storage_key).resolve()
    except (OSError, RuntimeError):
        raise bad_request(
            "CHANGE_UPLOAD_FILE_MISSING",
            "变更附件暂存路径无效，请取消后重新上传",
            409,
        ) from None
    if root not in target.parents or not target.is_file():
        raise bad_request(
            "CHANGE_UPLOAD_FILE_MISSING",
            "变更附件文件不存在或暂存路径无效，请取消后重新上传",
            409,
        )
    expected_digest = (item.sha256_hex or "").lower()
    if len(expected_digest) != 64 or any(
        character not in "0123456789abcdef" for character in expected_digest
    ):
        raise bad_request(
            "CHANGE_UPLOAD_CHECKSUM_MISSING",
            "变更附件缺少完整性校验信息，请取消后重新上传",
            409,
        )
    try:
        if target.stat().st_size != item.size_bytes:
            raise bad_request(
                "CHANGE_UPLOAD_FILE_CORRUPT",
                "变更附件大小与上传记录不一致，请取消后重新上传",
                409,
            )
        digest = hashlib.sha256()
        with target.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        raise bad_request(
            "CHANGE_UPLOAD_FILE_MISSING",
            "变更附件文件无法读取，请取消后重新上传",
            409,
        ) from None
    if digest.hexdigest() != expected_digest:
        raise bad_request(
            "CHANGE_UPLOAD_FILE_CORRUPT",
            "变更附件内容与上传记录不一致，请取消后重新上传",
            409,
        )


def remove_change_uploads(db: Db, change_id: int) -> list[str]:
    items = db.scalars(
        select(ArtifactChangeUpload).where(
            ArtifactChangeUpload.change_request_id == change_id
        )
    ).all()
    keys = [item.storage_key for item in items]
    for item in items:
        db.delete(item)
    return keys


def unlink_upload_files(storage_keys: list[str]) -> None:
    if not storage_keys:
        return
    root = get_settings().upload_dir.resolve()
    for storage_key in storage_keys:
        target = (root / storage_key).resolve()
        if root in target.parents:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                # The database state remains authoritative. A later storage sweep
                # can remove a file that was temporarily locked by the host.
                pass


def ensure_change_baseline_current(db: Db, change: ChangeRequest) -> int:
    current_sequence = latest_baseline_sequence(db, change.version_id)
    if current_sequence != change.expected_baseline_sequence:
        raise bad_request(
            "CHANGE_BASELINE_STALE",
            (
                "变更申请基于的版本基线已过期，"
                f"预期基线 {change.expected_baseline_sequence}，当前基线 {current_sequence}，"
                "请基于最新基线重新提交"
            ),
            409,
        )
    return current_sequence


@router.post("/versions/{version_id}/changes", status_code=201)
def create_change(version_id: int, payload: ChangeCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    item, _ = lock_version_scope(db, user, version_id)
    if item.status == "draft":
        raise bad_request("CHANGE_NOT_REQUIRED", "草稿版本可直接编辑，无需变更申请")
    expected_baseline_sequence = latest_baseline_sequence(db, item.id)
    if expected_baseline_sequence < 1:
        raise bad_request(
            "VERSION_BASELINE_MISSING",
            "已冻结版本缺少基线，不能提交变更申请",
            409,
        )
    # JSON mode keeps Decimal values exact as strings while making the payload
    # safe for SQLAlchemy JSON columns on both SQLite and MySQL.
    change_values = payload.model_dump(mode="json", exclude_unset=True)
    artifact_operations = change_values["payload"].get("artifacts", [])
    if any(
        isinstance(operation, dict)
        and (
            operation.get("action") == "replace_file"
            or (
                operation.get("action") == "add"
                and isinstance(operation.get("data"), dict)
                and operation["data"].get("upload_token")
            )
        )
        for operation in artifact_operations
    ):
        raise bad_request(
            "CHANGE_UPLOAD_ENDPOINT_REQUIRED",
            "附件新增或替换必须使用 multipart 变更上传接口",
            409,
        )
    if user.role not in MONEY_VISIBLE_ROLES:
        change_payload = change_values["payload"]
        contains_money = isinstance(change_payload.get("version"), dict) and "budget" in change_payload["version"]
        contains_money = contains_money or any(
            isinstance(operation, dict)
            and any(
                isinstance(operation.get(field_name), dict)
                and "estimated_budget" in operation[field_name]
                for field_name in ("data", "fields")
            )
            for operation in change_payload.get("requirements", [])
        )
        if contains_money:
            raise bad_request("FINANCE_FIELD_FORBIDDEN", "当前角色无权提交资金字段变更", 403)
    change = ChangeRequest(
        version_id=item.id,
        requested_by=user.id,
        expected_baseline_sequence=expected_baseline_sequence,
        **change_values,
    )
    db.add(change)
    db.flush()
    write_audit(db, request, user.id, "create", "change_request", change.id)
    db.commit()
    return {
        "id": change.id,
        "version_id": change.version_id,
        "expected_baseline_sequence": change.expected_baseline_sequence,
        "status": change.status,
        **change_values,
    }


@router.post("/change-requests", status_code=201, summary="提交版本变更申请")
def create_change_request(payload: ChangeRequestCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    return create_change(
        payload.version_id,
        ChangeCreate(**payload.model_dump(exclude={"version_id"}, exclude_unset=True)),
        request,
        db,
        user,
    )


@router.post("/changes/{change_id}/decision")
def decide_change(change_id: int, payload: DecisionIn, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    change = db.scalar(
        select(ChangeRequest)
        .where(ChangeRequest.id == change_id)
        .with_for_update()
    )
    if not change:
        raise bad_request("CHANGE_NOT_FOUND", "变更申请不存在", 404)
    lock_version_scope(db, user, change.version_id)
    if change.status != "pending":
        raise bad_request("CHANGE_DECIDED", "变更申请已审批", 409)
    if change.requested_by == user.id:
        raise bad_request("SELF_APPROVAL", "变更申请人不能审批自己的申请", 409)
    if payload.approved:
        ensure_change_baseline_current(db, change)
        current_targets = change_conflict_targets(change.payload or {})
        approved_changes = db.scalars(
            select(ChangeRequest).where(
                ChangeRequest.version_id == change.version_id,
                ChangeRequest.status == "approved",
                ChangeRequest.expected_baseline_sequence
                == change.expected_baseline_sequence,
                ChangeRequest.id != change.id,
            )
        ).all()
        for approved_change in approved_changes:
            overlap = current_targets & change_conflict_targets(
                approved_change.payload or {}
            )
            if overlap:
                raise bad_request(
                    "CHANGE_OVERLAP",
                    f"变更内容与已审批申请 {approved_change.id} 重叠，请先处理该申请",
                    409,
                )
        for operation in (change.payload or {}).get("artifacts", []):
            if not isinstance(operation, dict) or operation.get("action") != "decide":
                continue
            artifact = change_artifact(
                db, change.version_id, operation.get("artifact_id")
            )
            if artifact.uploaded_by == user.id:
                raise bad_request(
                    "ARTIFACT_SELF_APPROVAL",
                    "成果物上传人不能审批包含该成果物审批操作的变更",
                    409,
                )
        if db.scalar(
            select(ArtifactChangeUpload.id).where(
                ArtifactChangeUpload.change_request_id == change.id,
                ArtifactChangeUpload.uploaded_by == user.id,
            ).limit(1)
        ):
            raise bad_request(
                "ARTIFACT_SELF_APPROVAL",
                "变更附件上传人不能审批自己的附件变更",
                409,
            )
    removed_storage_keys = (
        [] if payload.approved else remove_change_uploads(db, change.id)
    )
    change.status = "approved" if payload.approved else "rejected"
    change.decided_by = user.id
    change.decision_note = payload.note
    change.decided_at = utcnow()
    write_audit(db, request, user.id, "approve" if payload.approved else "reject", "change_request", change.id)
    db.commit()
    unlink_upload_files(removed_storage_keys)
    return {
        "id": change.id,
        "status": change.status,
        "expected_baseline_sequence": change.expected_baseline_sequence,
        "decided_by": user.id,
        "decision_note": change.decision_note,
    }


@router.patch("/change-requests/{change_id}", summary="批准或驳回版本变更")
def patch_change_request(change_id: int, payload: DecisionIn, request: Request, db: Db, user: ReadyUser) -> dict:
    return decide_change(change_id, payload, request, db, user)


@router.post("/changes/{change_id}/cancel", summary="取消待处理版本变更")
@router.post("/change-requests/{change_id}/cancel", summary="取消待处理版本变更")
def cancel_change(change_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    change = db.scalar(
        select(ChangeRequest)
        .where(ChangeRequest.id == change_id)
        .with_for_update()
    )
    if not change:
        raise bad_request("CHANGE_NOT_FOUND", "变更申请不存在", 404)
    get_version_scope(db, user, change.version_id)
    if change.requested_by != user.id and user.role not in {
        "admin",
        "leader",
    }:
        raise bad_request("CHANGE_CANCEL_FORBIDDEN", "无权取消该变更申请", 403)
    if change.status not in {"pending", "approved"}:
        raise bad_request(
            "CHANGE_CANCEL_INVALID",
            "只能取消待审批或已审批但未执行的变更申请",
            409,
        )
    before = change.status
    storage_keys = remove_change_uploads(db, change.id)
    change.status = "cancelled"
    write_audit(
        db,
        request,
        user.id,
        "cancel",
        "change_request",
        change.id,
        before={"status": before},
        after={"status": change.status},
    )
    db.commit()
    unlink_upload_files(storage_keys)
    return {"id": change.id, "status": change.status}


@router.post("/changes/{change_id}/apply", summary="执行已审批变更并生成新基线")
def apply_change(change_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader")
    change = db.scalar(
        select(ChangeRequest)
        .where(ChangeRequest.id == change_id)
        .with_for_update()
    )
    if not change:
        raise bad_request("CHANGE_NOT_FOUND", "变更申请不存在", 404)
    if change.status != "approved":
        raise bad_request("CHANGE_NOT_APPROVED", "只能执行已审批且未执行的变更", 409)
    version, plan = lock_version_scope(db, user, change.version_id)
    current_sequence = ensure_change_baseline_current(db, change)
    payload = change.payload or {}
    version_changes = payload.get("version", {})
    if not isinstance(version_changes, dict):
        raise bad_request("CHANGE_PAYLOAD_INVALID", "版本变更内容格式无效")
    unknown_version_fields = set(version_changes) - {"name", "target", "budget"}
    if unknown_version_fields:
        raise bad_request("CHANGE_PAYLOAD_INVALID", "版本变更包含不允许的字段")
    for key, value in version_changes.items():
        if key == "budget":
            value = Decimal(str(value))
            if not value.is_finite() or value < 0:
                raise bad_request("CHANGE_PAYLOAD_INVALID", "版本预算不能为负数")
            allocated = budget_sum(db, Requirement.allocated_budget, Requirement.version_id == version.id)
            if value < allocated:
                raise bad_request(
                    "VERSION_BUDGET_BELOW_REQUIREMENTS",
                    f"变更后版本预算不能低于已分配需求预算 {money(allocated)}",
                    409,
                )
            ensure_plan_budget_capacity(db, plan, value, version.id)
        elif not isinstance(value, str) or (key == "name" and not value.strip()):
            raise bad_request("CHANGE_PAYLOAD_INVALID", f"版本字段 {key} 不能为空")
        setattr(version, key, value)

    operations = payload.get("requirements", [])
    if not isinstance(operations, list):
        raise bad_request("CHANGE_PAYLOAD_INVALID", "需求变更内容必须为列表")
    changed_ids: list[int] = []
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("action") not in {"add", "update", "remove"}:
            raise bad_request("CHANGE_PAYLOAD_INVALID", "需求变更操作只支持 add、update 或 remove")
        action = operation["action"]
        if action == "add":
            data = operation.get("data")
            if not isinstance(data, dict):
                raise bad_request("CHANGE_PAYLOAD_INVALID", "新增需求缺少 data")
            required = {"code", "title", "stakeholder_role"}
            if not required.issubset(data):
                raise bad_request("CHANGE_PAYLOAD_INVALID", "新增需求缺少 code、title 或 stakeholder_role")
            if db.scalar(select(Requirement.id).where(Requirement.code == str(data["code"]))):
                raise bad_request("REQUIREMENT_CODE_EXISTS", "变更中的需求编码已存在", 409)
            stable_key = str(data.get("stable_key") or data["code"]).strip()
            if not stable_key:
                raise bad_request("CHANGE_PAYLOAD_INVALID", "新增需求的稳定标识不能为空")
            ensure_stable_key_available(db, plan.project_id, version.id, stable_key)
            stakeholder_role = str(data["stakeholder_role"])
            if stakeholder_role not in {"admin", "customer", "sales", "manager", "developer", "operator", "leader"}:
                raise bad_request("CHANGE_PAYLOAD_INVALID", "新增需求的对接角色无效")
            source_id = data.get("source_requirement_id")
            validate_source_requirement(db, plan.project_id, source_id)
            estimated_budget = Decimal(str(data.get("estimated_budget", 0)))
            if not estimated_budget.is_finite() or estimated_budget < 0:
                raise bad_request("CHANGE_PAYLOAD_INVALID", "需求预算不能为负数")
            requirement = Requirement(
                code=str(data["code"])[:64],
                stable_key=stable_key[:64],
                title=str(data["title"])[:300],
                description=str(data.get("description", "")),
                project_id=plan.project_id,
                annual_plan_id=plan.id,
                version_id=version.id,
                requester_id=change.requested_by,
                stakeholder_role=stakeholder_role,
                estimated_budget=estimated_budget,
                priority=str(data.get("priority", "medium")),
                source_requirement_id=source_id,
                status="draft",
            )
            if requirement.priority not in {"low", "medium", "high", "urgent"}:
                raise bad_request("CHANGE_PAYLOAD_INVALID", "新增需求的优先级无效")
            db.add(requirement)
            db.flush()
            set_requirement_tags(db, requirement.id, list(data.get("tag_ids", [])))
            changed_ids.append(requirement.id)
            continue

        requirement_id = operation.get("requirement_id")
        requirement = db.get(Requirement, requirement_id) if isinstance(requirement_id, int) else None
        if not requirement or requirement.version_id != version.id:
            raise bad_request("CHANGE_PAYLOAD_INVALID", "变更需求不存在或不属于当前版本")
        if action == "remove":
            referenced = any(
                (
                    db.scalar(select(BudgetEntry.id).where(BudgetEntry.requirement_id == requirement.id).limit(1)),
                    db.scalar(select(Deliverable.id).where(Deliverable.requirement_id == requirement.id).limit(1)),
                )
            )
            if referenced:
                raise bad_request("REQUIREMENT_IN_USE", "已产生资金或成果物记录的需求不能删除", 409)
            changed_ids.append(requirement.id)
            db.delete(requirement)
            continue

        raw_fields = operation.get("fields")
        fields = dict(raw_fields) if isinstance(raw_fields, dict) else None
        if not isinstance(fields, dict):
            raise bad_request("CHANGE_PAYLOAD_INVALID", "更新需求缺少 fields")
        allowed_fields = {"stable_key", "title", "description", "stakeholder_role", "estimated_budget", "priority", "source_requirement_id", "tag_ids"}
        if set(fields) - allowed_fields:
            raise bad_request("CHANGE_PAYLOAD_INVALID", "需求变更包含不允许的字段")
        if "source_requirement_id" in fields:
            validate_source_requirement(db, plan.project_id, fields["source_requirement_id"], requirement.id)
        if "estimated_budget" in fields:
            fields["estimated_budget"] = Decimal(str(fields["estimated_budget"]))
            if not fields["estimated_budget"].is_finite() or fields["estimated_budget"] < 0:
                raise bad_request("CHANGE_PAYLOAD_INVALID", "需求预算不能为负数")
        if "priority" in fields and fields["priority"] not in {"low", "medium", "high", "urgent"}:
            raise bad_request("CHANGE_PAYLOAD_INVALID", "需求优先级无效")
        if "stakeholder_role" in fields and fields["stakeholder_role"] not in {"admin", "customer", "sales", "manager", "developer", "operator", "leader"}:
            raise bad_request("CHANGE_PAYLOAD_INVALID", "需求对接角色无效")
        if "stable_key" in fields:
            if not isinstance(fields["stable_key"], str) or not fields["stable_key"].strip():
                raise bad_request("CHANGE_PAYLOAD_INVALID", "需求稳定标识不能为空")
            fields["stable_key"] = fields["stable_key"].strip()
            ensure_stable_key_available(db, plan.project_id, version.id, fields["stable_key"], requirement.id)
        tag_ids = fields.pop("tag_ids", None)
        for key, value in fields.items():
            setattr(requirement, key, value)
        if tag_ids is not None:
            set_requirement_tags(db, requirement.id, list(tag_ids))
        changed_ids.append(requirement.id)

    artifact_operations = payload.get("artifacts", [])
    if not isinstance(artifact_operations, list):
        raise bad_request("CHANGE_PAYLOAD_INVALID", "成果物变更内容必须为列表")
    changed_artifact_ids: list[int] = []
    removed_storage_keys: list[str] = []
    for operation in artifact_operations:
        if not isinstance(operation, dict) or operation.get("action") not in {
            "add",
            "update",
            "submit",
            "decide",
            "replace_file",
            "remove",
        }:
            raise bad_request(
                "CHANGE_PAYLOAD_INVALID",
                "成果物变更操作只支持 add、update、submit、decide、replace_file 或 remove",
            )
        action = operation["action"]
        if action == "add":
            data = operation.get("data")
            if not isinstance(data, dict):
                raise bad_request("CHANGE_PAYLOAD_INVALID", "新增成果物缺少 data")
            if set(data) - {
                "stage",
                "category",
                "title",
                "requirement_id",
                "upload_token",
            }:
                raise bad_request("CHANGE_PAYLOAD_INVALID", "新增成果物包含不允许的字段")
            stage = data.get("stage")
            category = data.get("category")
            title = data.get("title")
            if stage not in {3, 4, 5, 6}:
                raise bad_request(
                    "CHANGE_PAYLOAD_INVALID",
                    "版本变更只能新增建设、招投标、验收或运维成果物",
                )
            if not isinstance(category, str) or not category.strip():
                raise bad_request("CHANGE_PAYLOAD_INVALID", "成果物分类不能为空")
            if not isinstance(title, str) or not title.strip():
                raise bad_request("CHANGE_PAYLOAD_INVALID", "成果物标题不能为空")
            requirement_id = data.get("requirement_id")
            upload_token = data.get("upload_token")
            staged_upload = (
                change_upload(db, change, upload_token)
                if upload_token is not None
                else None
            )
            if staged_upload:
                verify_change_upload_file(staged_upload)
            if staged_upload and (
                staged_upload.stage != stage
                or staged_upload.category != category
                or staged_upload.title != title
                or staged_upload.requirement_id != requirement_id
            ):
                raise bad_request(
                    "CHANGE_UPLOAD_INVALID",
                    "暂存附件与变更中的成果物信息不一致",
                    409,
                )
            if staged_upload and (
                change.decided_by is None
                or staged_upload.uploaded_by == change.decided_by
            ):
                raise bad_request(
                    "ARTIFACT_SELF_APPROVAL",
                    "变更附件上传人不能审批自己的附件变更",
                    409,
                )
            file_values = (
                {
                    "original_filename": staged_upload.original_filename,
                    "storage_key": staged_upload.storage_key,
                    "content_type": staged_upload.content_type,
                    "size_bytes": staged_upload.size_bytes,
                    "approval_status": "approved",
                    "reviewed_by": change.decided_by,
                    "reviewed_at": change.decided_at or utcnow(),
                    "review_note": change.decision_note,
                }
                if staged_upload
                else {}
            )
            if stage == 6:
                requirement = (
                    db.get(Requirement, requirement_id)
                    if isinstance(requirement_id, int)
                    else None
                )
                if not requirement or requirement.version_id != version.id:
                    raise bad_request(
                        "CHANGE_PAYLOAD_INVALID",
                        "运维成果物必须关联当前版本内的需求",
                    )
                artifact = Deliverable(
                    project_id=plan.project_id,
                    annual_plan_id=plan.id,
                    version_id=None,
                    requirement_id=requirement.id,
                    stage=stage,
                    category=category,
                    title=title,
                    uploaded_by=(
                        staged_upload.uploaded_by
                        if staged_upload
                        else change.requested_by
                    ),
                    **file_values,
                )
            else:
                if requirement_id is not None:
                    raise bad_request(
                        "CHANGE_PAYLOAD_INVALID",
                        "建设、招投标和验收成果物不能关联需求",
                    )
                artifact = Deliverable(
                    project_id=plan.project_id,
                    annual_plan_id=plan.id,
                    version_id=version.id,
                    requirement_id=None,
                    stage=stage,
                    category=category,
                    title=title,
                    uploaded_by=(
                        staged_upload.uploaded_by
                        if staged_upload
                        else change.requested_by
                    ),
                    **file_values,
                )
            db.add(artifact)
            db.flush()
            if staged_upload:
                db.delete(staged_upload)
            changed_artifact_ids.append(artifact.id)
            write_audit(
                db,
                request,
                user.id,
                "change_add",
                "artifact",
                artifact.id,
                after={
                    "change_request_id": change.id,
                    "status": artifact.approval_status,
                    "filename": artifact.original_filename,
                },
            )
            continue

        artifact = change_artifact(db, version.id, operation.get("artifact_id"))
        before = {
            "title": artifact.title,
            "category": artifact.category,
            "status": artifact.approval_status,
        }
        if action == "update":
            raw_fields = operation.get("fields")
            fields = dict(raw_fields) if isinstance(raw_fields, dict) else None
            if not fields or set(fields) - {"title", "category"}:
                raise bad_request(
                    "CHANGE_PAYLOAD_INVALID",
                    "成果物更新只能包含 title 或 category",
                )
            if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
                raise bad_request("CHANGE_PAYLOAD_INVALID", "成果物更新字段不能为空")
            for key, value in fields.items():
                setattr(artifact, key, value)
        elif action == "replace_file":
            staged_upload = change_upload(
                db, change, operation.get("upload_token")
            )
            verify_change_upload_file(staged_upload)
            if (
                staged_upload.stage != artifact.stage
                or staged_upload.category != artifact.category
                or staged_upload.title != artifact.title
                or staged_upload.requirement_id != artifact.requirement_id
            ):
                raise bad_request(
                    "CHANGE_UPLOAD_INVALID",
                    "替换附件与目标成果物范围不一致",
                    409,
                )
            if (
                change.decided_by is None
                or staged_upload.uploaded_by == change.decided_by
            ):
                raise bad_request(
                    "ARTIFACT_SELF_APPROVAL",
                    "变更附件上传人不能审批自己的附件变更",
                    409,
                )
            if artifact.storage_key:
                removed_storage_keys.append(artifact.storage_key)
            artifact.original_filename = staged_upload.original_filename
            artifact.storage_key = staged_upload.storage_key
            artifact.content_type = staged_upload.content_type
            artifact.size_bytes = staged_upload.size_bytes
            artifact.uploaded_by = staged_upload.uploaded_by
            artifact.approval_status = "approved"
            artifact.reviewed_by = change.decided_by
            artifact.reviewed_at = change.decided_at or utcnow()
            artifact.review_note = change.decision_note
            db.delete(staged_upload)
        elif action == "submit":
            if artifact.approval_status not in {"draft", "rejected"}:
                raise bad_request(
                    "ARTIFACT_STATUS_INVALID",
                    "只有草稿或已驳回成果物可以通过变更提交审批",
                    409,
                )
            artifact.approval_status = "submitted"
            artifact.reviewed_by = None
            artifact.reviewed_at = None
            artifact.review_note = None
        elif action == "decide":
            if artifact.approval_status != "submitted":
                raise bad_request(
                    "ARTIFACT_STATUS_INVALID",
                    "只有已提交成果物可以通过变更审批",
                    409,
                )
            if not isinstance(operation.get("approved"), bool):
                raise bad_request("CHANGE_PAYLOAD_INVALID", "成果物审批结论无效")
            if change.decided_by is None or artifact.uploaded_by == change.decided_by:
                raise bad_request(
                    "ARTIFACT_SELF_APPROVAL",
                    "成果物上传人不能审批自己的成果物",
                    409,
                )
            note = operation.get("note", "")
            if not isinstance(note, str):
                raise bad_request("CHANGE_PAYLOAD_INVALID", "成果物审批意见无效")
            artifact.approval_status = (
                "approved" if operation["approved"] else "rejected"
            )
            artifact.reviewed_by = change.decided_by
            artifact.reviewed_at = change.decided_at or utcnow()
            artifact.review_note = note
        else:
            if artifact.storage_key:
                removed_storage_keys.append(artifact.storage_key)
            changed_artifact_ids.append(artifact.id)
            db.delete(artifact)
            write_audit(
                db,
                request,
                user.id,
                "change_remove",
                "artifact",
                artifact.id,
                before={**before, "change_request_id": change.id},
            )
            continue
        changed_artifact_ids.append(artifact.id)
        write_audit(
            db,
            request,
            user.id,
            f"change_{action}",
            "artifact",
            artifact.id,
            before=before,
            after={
                "change_request_id": change.id,
                "title": artifact.title,
                "category": artifact.category,
                "status": artifact.approval_status,
            },
        )

    db.flush()
    if db.scalar(
        select(ArtifactChangeUpload.id).where(
            ArtifactChangeUpload.change_request_id == change.id
        ).limit(1)
    ):
        raise bad_request(
            "CHANGE_UPLOAD_UNUSED",
            "变更申请仍有未使用的暂存附件，不能执行",
            409,
        )
    sequence = current_sequence + 1
    baseline = VersionBaseline(
        version_id=version.id,
        sequence=sequence,
        snapshot=build_snapshot(db, version, plan),
        created_by=user.id,
    )
    db.add(baseline)
    change.status = "applied"
    change.applied_by = user.id
    change.applied_at = utcnow()
    write_audit(
        db,
        request,
        user.id,
        "apply",
        "change_request",
        change.id,
        after={
            "baseline_sequence": sequence,
            "requirement_ids": changed_ids,
            "artifact_ids": changed_artifact_ids,
        },
    )
    db.commit()
    unlink_upload_files(removed_storage_keys)
    return {
        "id": change.id,
        "status": change.status,
        "applied_by": change.applied_by,
        "applied_at": change.applied_at,
        "baseline": {"id": baseline.id, "sequence": baseline.sequence},
    }


@router.post("/change-requests/{change_id}/apply", summary="执行已批准版本变更")
def apply_change_request(change_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    return apply_change(change_id, request, db, user)
