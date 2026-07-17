from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AnnualPlan, DeliveryVersion, Project, ProjectAccess, Requirement, User


def bad_request(code: str, message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def project_ids_for(db: Session, user: User) -> set[int] | None:
    if user.role != "customer":
        return None
    return set(db.scalars(select(ProjectAccess.project_id).where(ProjectAccess.user_id == user.id)).all())


def ensure_project_access(db: Session, user: User, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise bad_request("PROJECT_NOT_FOUND", "项目不存在", 404)
    allowed = project_ids_for(db, user)
    if allowed is not None and project_id not in allowed:
        raise bad_request("PROJECT_FORBIDDEN", "无权访问该项目", 403)
    return project


def ensure_requirement_access(user: User, requirement: Requirement) -> None:
    if user.role == "customer" and requirement.requester_id != user.id:
        raise bad_request("REQUIREMENT_FORBIDDEN", "客户只能访问本人提交的需求", 403)


def validate_hierarchy(
    db: Session,
    project_id: int,
    annual_plan_id: int | None = None,
    version_id: int | None = None,
    requirement_id: int | None = None,
) -> tuple[Project, AnnualPlan | None, DeliveryVersion | None, Requirement | None]:
    project = db.get(Project, project_id)
    if not project:
        raise bad_request("PROJECT_NOT_FOUND", "项目不存在", 404)
    plan = db.get(AnnualPlan, annual_plan_id) if annual_plan_id else None
    if annual_plan_id and (not plan or plan.project_id != project_id):
        raise bad_request("PLAN_SCOPE_MISMATCH", "年度计划与项目层级不匹配")
    version = db.get(DeliveryVersion, version_id) if version_id else None
    if version_id and (not version or (plan is not None and version.annual_plan_id != plan.id)):
        raise bad_request("VERSION_SCOPE_MISMATCH", "版本与年度计划层级不匹配")
    if version and plan is None:
        plan = db.get(AnnualPlan, version.annual_plan_id)
        if not plan or plan.project_id != project_id:
            raise bad_request("VERSION_SCOPE_MISMATCH", "版本与项目层级不匹配")
    requirement = db.get(Requirement, requirement_id) if requirement_id else None
    if requirement_id and (
        not requirement
        or requirement.project_id != project_id
        or (annual_plan_id and requirement.annual_plan_id != annual_plan_id)
        or (version_id and requirement.version_id != version_id)
    ):
        raise bad_request("REQUIREMENT_SCOPE_MISMATCH", "需求与上级数据层级不匹配")
    return project, plan, version, requirement


def money(value: Decimal | int | float | None) -> str:
    return f"{Decimal(value or 0):.2f}"


def requirement_dict(item: Requirement, tag_ids: list[int], user: User) -> dict:
    data = {
        "id": item.id,
        "code": item.code,
        "stable_key": item.stable_key,
        "title": item.title,
        "description": item.description,
        "project_id": item.project_id,
        "annual_plan_id": item.annual_plan_id,
        "version_id": item.version_id,
        "planning_pool": item.version_id is None,
        "requester_id": item.requester_id,
        "stakeholder_role": item.stakeholder_role,
        "status": item.status,
        "priority": item.priority,
        "source_requirement_id": item.source_requirement_id,
        "assignee_id": item.assignee_id,
        "tag_ids": tag_ids,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if user.role in {"admin", "sales", "manager", "leader"}:
        data.update(
            estimated_budget=money(item.estimated_budget),
            allocated_budget=money(item.allocated_budget),
            actual_cost=money(item.actual_cost),
        )
    if user.role in {"admin", "manager", "developer", "leader"}:
        data.update(
            estimated_hours=money(item.estimated_hours),
            actual_hours=money(item.actual_hours),
        )
    return data
