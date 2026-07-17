from __future__ import annotations

import csv
import hashlib
import io
import mimetypes
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, or_, select

from .config import get_settings
from .core_api import (
    change_artifact,
    compare_versions,
    get_requirement_scope,
    get_version_scope,
    latest_baseline_sequence,
    lock_version_scope,
    plan_data,
    project_data,
    require_user_role,
    staged_artifact_data,
    version_data,
)
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
    Requirement,
    RequirementTag,
    Tag,
    User,
    utcnow,
)
from .schemas import (
    ArtifactCreate,
    ArtifactDecisionIn,
    BudgetEntryCreate,
    ChangeCreate,
    FundingApplicationCreate,
    FundingApplicationPatch,
    FundingStatusIn,
    OperationCreate,
    OperationPatch,
)
from .security import Db, ReadyUser, write_audit
from .services import (
    bad_request,
    ensure_project_access,
    ensure_requirement_access,
    money,
    project_ids_for,
    validate_hierarchy,
)


router = APIRouter(prefix="/api")


MONEY_ROLES = {"admin", "sales", "manager", "leader"}
BLOCKED_UPLOAD_SUFFIXES = {".exe", ".com", ".bat", ".cmd", ".ps1", ".sh", ".js", ".vbs", ".msi", ".dll", ".scr"}


def require_money(user: User) -> None:
    if user.role not in MONEY_ROLES:
        raise bad_request("FINANCE_FORBIDDEN", "当前角色无权查看项目资金或成本明细", 403)


def budget_data(item: BudgetEntry) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "annual_plan_id": item.annual_plan_id,
        "version_id": item.version_id,
        "requirement_id": item.requirement_id,
        "entry_type": item.entry_type,
        "amount": money(item.amount),
        "description": item.description,
        "occurred_on": item.occurred_on,
        "created_by": item.created_by,
        "created_at": item.created_at,
    }


@router.get("/funds/tree", summary="项目→年度→版本→需求四级资金穿透")
def funds_tree(project_id: int, db: Db, user: ReadyUser) -> dict:
    require_money(user)
    project = ensure_project_access(db, user, project_id)
    plans = db.scalars(select(AnnualPlan).where(AnnualPlan.project_id == project.id).order_by(AnnualPlan.year)).all()
    plan_nodes = []
    actual_total = Decimal(0)

    def requirement_node(requirement: Requirement, planning_pool: bool = False) -> tuple[dict, Decimal]:
        actual_entries = Decimal(
            db.scalar(
                select(func.coalesce(func.sum(BudgetEntry.amount), 0)).where(
                    BudgetEntry.requirement_id == requirement.id,
                    BudgetEntry.entry_type == "actual",
                )
            )
            or 0
        )
        allocated = Decimal(requirement.allocated_budget or 0)
        return (
            {
                "id": requirement.id,
                "type": "requirement",
                "code": requirement.code,
                "name": requirement.title,
                "estimated_budget": money(requirement.estimated_budget),
                "budget": money(allocated),
                "allocated_budget": money(allocated),
                "actual": money(actual_entries),
                "variance": money(allocated - actual_entries),
                "overrun": actual_entries > allocated,
                "planning_pool": planning_pool,
            },
            actual_entries,
        )

    for plan in plans:
        versions = db.scalars(select(DeliveryVersion).where(DeliveryVersion.annual_plan_id == plan.id).order_by(DeliveryVersion.id)).all()
        version_nodes = []
        for version in versions:
            requirements = db.scalars(select(Requirement).where(Requirement.version_id == version.id).order_by(Requirement.code)).all()
            requirement_nodes = []
            for requirement in requirements:
                node, requirement_actual = requirement_node(requirement)
                actual_total += requirement_actual
                requirement_nodes.append(node)
            version_actual = sum((Decimal(node["actual"]) for node in requirement_nodes), Decimal(0))
            version_nodes.append(
                {
                    "id": version.id,
                    "type": "version",
                    "code": version.code,
                    "name": version.name,
                    "status": version.status,
                    "budget": money(version.budget),
                    "actual": money(version_actual),
                    "variance": money(Decimal(version.budget or 0) - version_actual),
                    "children": requirement_nodes,
                }
            )
        pending_requirements = db.scalars(
            select(Requirement).where(
                Requirement.project_id == project.id,
                Requirement.annual_plan_id == plan.id,
                Requirement.version_id.is_(None),
            ).order_by(Requirement.code)
        ).all()
        if pending_requirements:
            pending_nodes = []
            pending_actual = Decimal(0)
            for requirement in pending_requirements:
                node, requirement_actual = requirement_node(requirement, planning_pool=True)
                pending_nodes.append(node)
                pending_actual += requirement_actual
                actual_total += requirement_actual
            pending_budget = sum((Decimal(node["budget"]) for node in pending_nodes), Decimal(0))
            version_nodes.append(
                {
                    "id": f"planning-plan-{plan.id}",
                    "type": "version",
                    "code": "待规划",
                    "name": "年度内待规划",
                    "budget": money(pending_budget),
                    "actual": money(pending_actual),
                    "variance": money(pending_budget - pending_actual),
                    "planning_pool": True,
                    "children": pending_nodes,
                }
            )
        plan_actual = sum((Decimal(node["actual"]) for node in version_nodes), Decimal(0))
        plan_nodes.append(
            {
                "id": plan.id,
                "type": "annual_plan",
                "year": plan.year,
                "name": plan.name,
                "budget": money(plan.budget),
                "actual": money(plan_actual),
                "variance": money(Decimal(plan.budget or 0) - plan_actual),
                "children": version_nodes,
            }
        )
    unscoped_requirements = db.scalars(
        select(Requirement).where(
            Requirement.project_id == project.id,
            Requirement.annual_plan_id.is_(None),
            Requirement.version_id.is_(None),
        ).order_by(Requirement.code)
    ).all()
    if unscoped_requirements:
        unscoped_nodes = []
        unscoped_actual = Decimal(0)
        for requirement in unscoped_requirements:
            node, requirement_actual = requirement_node(requirement, planning_pool=True)
            unscoped_nodes.append(node)
            unscoped_actual += requirement_actual
            actual_total += requirement_actual
        unscoped_budget = sum((Decimal(node["budget"]) for node in unscoped_nodes), Decimal(0))
        plan_nodes.append(
            {
                "id": "planning-pool",
                "type": "annual_plan",
                "year": None,
                "name": "无年度待规划",
                "budget": money(unscoped_budget),
                "actual": money(unscoped_actual),
                "variance": money(unscoped_budget - unscoped_actual),
                "planning_pool": True,
                "children": [
                    {
                        "id": "planning-unscoped",
                        "type": "version",
                        "code": "待规划",
                        "name": "待规划需求",
                        "budget": money(unscoped_budget),
                        "actual": money(unscoped_actual),
                        "variance": money(unscoped_budget - unscoped_actual),
                        "planning_pool": True,
                        "children": unscoped_nodes,
                    }
                ],
            }
        )
    return {
        "id": project.id,
        "type": "project",
        "code": project.code,
        "name": project.name,
        "budget": money(project.total_budget),
        "actual": money(actual_total),
        "variance": money(Decimal(project.total_budget or 0) - actual_total),
        "execution_rate": float(actual_total / Decimal(project.total_budget) * 100) if project.total_budget else 0,
        "children": plan_nodes,
    }


@router.get("/funds/entries")
def list_fund_entries(
    db: Db,
    user: ReadyUser,
    project_id: int | None = None,
    annual_plan_id: int | None = None,
    version_id: int | None = None,
    requirement_id: int | None = None,
) -> list[dict]:
    require_money(user)
    stmt = select(BudgetEntry).order_by(BudgetEntry.occurred_on.desc(), BudgetEntry.id.desc())
    for column, value in (
        (BudgetEntry.project_id, project_id),
        (BudgetEntry.annual_plan_id, annual_plan_id),
        (BudgetEntry.version_id, version_id),
        (BudgetEntry.requirement_id, requirement_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    items = db.scalars(stmt).all()
    for item in items:
        ensure_project_access(db, user, item.project_id)
    return [budget_data(item) for item in items]


@router.post("/funds/entries", status_code=201)
def create_fund_entry(payload: BudgetEntryCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "leader", "manager")
    if payload.entry_type in {"allocation", "actual"} and payload.amount <= 0:
        raise bad_request("AMOUNT_INVALID", "该资金类型的金额必须大于 0")
    if payload.entry_type == "adjustment" and payload.amount == 0:
        raise bad_request("AMOUNT_INVALID", "预算调整金额不能为 0")
    linked_types = {"allocation", "actual", "adjustment"}
    if payload.entry_type in linked_types and (payload.requirement_id is None or payload.version_id is None or payload.annual_plan_id is None):
        raise bad_request("REQUIREMENT_REQUIRED", "需求预算分配、调整和实际消耗必须关联同一项目-年度-版本-需求完整层级")
    assert payload.version_id is not None
    assert payload.annual_plan_id is not None
    assert payload.requirement_id is not None
    version, plan = lock_version_scope(db, user, payload.version_id)
    if plan.project_id != payload.project_id or plan.id != payload.annual_plan_id:
        raise bad_request(
            "VERSION_SCOPE_MISMATCH",
            "版本与项目或年度计划层级不匹配",
        )
    requirements = db.scalars(
        select(Requirement)
        .where(Requirement.version_id == version.id)
        .order_by(Requirement.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    requirement = next(
        (item for item in requirements if item.id == payload.requirement_id), None
    )
    if requirement is None:
        raise bad_request(
            "REQUIREMENT_SCOPE_MISMATCH",
            "需求与上级数据层级不匹配",
        )
    if version.status != "draft" and payload.entry_type != "actual":
        raise bad_request("VERSION_LOCKED", "版本已冻结，只允许继续登记实际消耗", 409)
    if payload.entry_type in {"allocation", "adjustment"}:
        projected_requirement = Decimal(requirement.allocated_budget or 0) + payload.amount
        if projected_requirement < 0:
            raise bad_request("ALLOCATION_NEGATIVE", "调整后的需求分配预算不能小于 0", 409)
        version_allocated = sum(
            (
                Decimal(item.allocated_budget or 0)
                for item in requirements
                if item.id != requirement.id
            ),
            Decimal(0),
        )
        projected_version = version_allocated + projected_requirement
        if projected_version > Decimal(version.budget or 0):
            raise bad_request(
                "VERSION_ALLOCATION_EXCEEDED",
                f"分配后的需求预算合计 {money(projected_version)} 不能超过版本预算 {money(version.budget)}",
                409,
            )
        requirement.allocated_budget = projected_requirement
    elif payload.entry_type == "actual":
        projected_actual = Decimal(requirement.actual_cost or 0) + payload.amount
        is_overrun = projected_actual > Decimal(requirement.allocated_budget or 0)
        if is_overrun and not payload.allow_actual_overrun:
            raise bad_request(
                "ACTUAL_OVERRUN",
                f"实际消耗 {money(projected_actual)} 将超过需求已分配预算 {money(requirement.allocated_budget)}；如需超支必须由负责人显式确认",
                409,
            )
        if is_overrun and payload.allow_actual_overrun and user.role not in {"admin", "leader", "manager"}:
            raise bad_request("OVERRUN_APPROVAL_FORBIDDEN", "只有管理员、咨询负责人或项目经理可确认超支", 403)
        requirement.actual_cost = projected_actual
    item_values = payload.model_dump(exclude={"allow_actual_overrun"})
    item = BudgetEntry(**item_values, created_by=user.id)
    db.add(item)
    db.flush()
    audit_after = budget_data(item)
    if payload.entry_type == "actual":
        audit_after["allow_actual_overrun"] = payload.allow_actual_overrun
    write_audit(db, request, user.id, "create", "budget_entry", item.id, after=audit_after)
    db.commit()
    return budget_data(item)


def application_data(db: Db, item: FundingApplication) -> dict:
    applicant = db.get(User, item.applicant_id)
    return {
        "id": item.id,
        "project_id": item.project_id,
        "annual_plan_id": item.annual_plan_id,
        "version_id": item.version_id,
        "title": item.title,
        "amount": money(item.amount),
        "status": item.status,
        "applicant_id": item.applicant_id,
        "applicant_name": applicant.full_name if applicant else "已删除用户",
        "note": item.note,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.get("/funds/applications")
def list_applications(db: Db, user: ReadyUser, project_id: int | None = None) -> list[dict]:
    require_money(user)
    stmt = select(FundingApplication).order_by(FundingApplication.id.desc())
    if project_id:
        ensure_project_access(db, user, project_id)
        stmt = stmt.where(FundingApplication.project_id == project_id)
    return [application_data(db, item) for item in db.scalars(stmt).all()]


@router.post("/funds/applications", status_code=201)
def create_application(payload: FundingApplicationCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "sales", "leader")
    ensure_project_access(db, user, payload.project_id)
    validate_hierarchy(db, payload.project_id, payload.annual_plan_id, payload.version_id)
    item = FundingApplication(**payload.model_dump(), applicant_id=user.id)
    db.add(item)
    db.flush()
    write_audit(db, request, user.id, "create", "funding_application", item.id, after=application_data(db, item))
    db.commit()
    return application_data(db, item)


@router.patch("/funds/applications/{application_id}", summary="编辑资金申报草稿")
def patch_application(
    application_id: int,
    payload: FundingApplicationPatch,
    request: Request,
    db: Db,
    user: ReadyUser,
) -> dict:
    require_user_role(user, "admin", "sales", "leader")
    item = db.get(FundingApplication, application_id)
    if not item:
        raise bad_request("APPLICATION_NOT_FOUND", "资金申报不存在", 404)
    ensure_project_access(db, user, item.project_id)
    if item.applicant_id != user.id:
        raise bad_request("FUNDING_EDIT_FORBIDDEN", "只有申请人可以编辑资金申报", 403)
    if item.status not in {"draft", "rejected"}:
        raise bad_request("FUNDING_EDIT_LOCKED", "只能编辑草稿或已驳回的资金申报", 409)
    if not payload.model_fields_set:
        raise bad_request("EMPTY_UPDATE", "请至少提交一个需要修改的字段")

    values = payload.model_dump(exclude_unset=True)
    for field in ("annual_plan_id", "title", "amount", "note"):
        if field in values and values[field] is None:
            raise bad_request("FIELD_REQUIRED", f"{field} 不能为 null")
    next_plan_id = values.get("annual_plan_id", item.annual_plan_id)
    if next_plan_id is None:
        raise bad_request("PLAN_REQUIRED", "资金申报必须关联年度计划")
    next_version_id = values.get("version_id", item.version_id)
    validate_hierarchy(db, item.project_id, next_plan_id, next_version_id)
    before = application_data(db, item)
    for key, value in values.items():
        setattr(item, key, value)
    write_audit(db, request, user.id, "update", "funding_application", item.id, before, application_data(db, item))
    db.commit()
    return application_data(db, item)


FUNDING_TRANSITIONS = {
    "draft": {"submitted"},
    "submitted": {"reviewing", "rejected"},
    "reviewing": {"approved", "rejected"},
    "approved": {"disbursed"},
    "rejected": {"draft"},
    "disbursed": set(),
}


@router.patch("/funds/applications/{application_id}/status")
def update_application_status(
    application_id: int,
    payload: FundingStatusIn,
    request: Request,
    db: Db,
    user: ReadyUser,
) -> dict:
    require_user_role(user, "admin", "sales", "leader")
    item = db.get(FundingApplication, application_id)
    if not item:
        raise bad_request("APPLICATION_NOT_FOUND", "资金申报不存在", 404)
    ensure_project_access(db, user, item.project_id)
    if payload.status not in FUNDING_TRANSITIONS.get(item.status, set()):
        raise bad_request("INVALID_TRANSITION", f"资金申报不能从 {item.status} 流转到 {payload.status}")
    applicant_actions = (item.status == "draft" and payload.status == "submitted") or (
        item.status == "rejected" and payload.status == "draft"
    )
    if applicant_actions:
        if user.id != item.applicant_id:
            raise bad_request("FORBIDDEN", "只有申请人可提交草稿或将驳回单退回草稿", 403)
    else:
        if user.role not in {"admin", "leader"}:
            raise bad_request("FUNDING_REVIEW_FORBIDDEN", "销售只能创建、提交和重新编辑被驳回的申报", 403)
        if user.id == item.applicant_id:
            raise bad_request("FUNDING_SELF_REVIEW", "资金申报的申请人不能审核、批复或拨付自己的申报", 409)
    before = item.status
    item.status = payload.status
    write_audit(db, request, user.id, "transition", "funding_application", item.id, {"status": before}, {"status": item.status})
    db.commit()
    return application_data(db, item)


def validate_artifact_scope(payload: ArtifactCreate) -> None:
    if payload.stage == 1 and any((payload.annual_plan_id, payload.version_id, payload.requirement_id)):
        raise bad_request("ARTIFACT_SCOPE_INVALID", "可研报告应挂载在规划项目")
    if payload.stage == 2 and (not payload.annual_plan_id or payload.version_id or payload.requirement_id):
        raise bad_request("ARTIFACT_SCOPE_INVALID", "分年任务申报书应挂载在年度计划")
    if payload.stage in {3, 4, 5} and (not payload.version_id or payload.requirement_id is not None):
        raise bad_request("ARTIFACT_SCOPE_INVALID", "建设、招投标和验收成果物只能挂载在落地版本，不能同时关联需求")
    if payload.stage == 6 and (not payload.requirement_id or payload.version_id is not None):
        raise bad_request("ARTIFACT_SCOPE_INVALID", "运维反馈成果物只能挂载在需求任务，不能重复挂载版本")


def artifact_data(item: Deliverable) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "annual_plan_id": item.annual_plan_id,
        "version_id": item.version_id,
        "requirement_id": item.requirement_id,
        "stage": item.stage,
        "category": item.category,
        "title": item.title,
        "original_filename": item.original_filename,
        "content_type": item.content_type,
        "size_bytes": item.size_bytes,
        "has_file": bool(item.storage_key),
        "uploaded_by": item.uploaded_by,
        "approval_status": item.approval_status,
        "reviewed_by": item.reviewed_by,
        "reviewed_at": item.reviewed_at,
        "review_note": item.review_note,
        "created_at": item.created_at,
    }


def apply_artifact_visibility(stmt, user: User):
    if user.role != "customer":
        return stmt
    return stmt.outerjoin(
        Requirement, Deliverable.requirement_id == Requirement.id
    ).where(
        Requirement.requester_id == user.id,
        Deliverable.approval_status == "approved",
    )


def artifact_version_id(db: Db, item: Deliverable) -> int | None:
    version_id = item.version_id
    if version_id is None and item.requirement_id is not None:
        version_id = db.scalar(
            select(Requirement.version_id).where(
                Requirement.id == item.requirement_id
            )
        )
    return version_id


def lock_artifact_version_mutable(
    db: Db, item: Deliverable, user: User
) -> Deliverable:
    version_id = artifact_version_id(db, item)
    if version_id is not None:
        version, _ = lock_version_scope(db, user, version_id)
        if version.status != "draft":
            raise bad_request(
                "VERSION_LOCKED",
                "已冻结版本内的成果物只能通过版本变更申请调整",
                409,
            )
    if item.id is None:
        return item
    locked_item = db.scalar(
        select(Deliverable)
        .where(Deliverable.id == item.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not locked_item:
        raise bad_request(
            "ARTIFACT_NOT_FOUND",
            "成果物不存在",
            404,
        )
    return locked_item


@router.get("/artifacts")
def list_artifacts(
    db: Db,
    user: ReadyUser,
    project_id: int | None = None,
    annual_plan_id: int | None = None,
    stage: int | None = None,
    version_id: int | None = None,
    requirement_id: int | None = None,
) -> list[dict]:
    allowed = project_ids_for(db, user)
    stmt = apply_artifact_visibility(
        select(Deliverable).order_by(Deliverable.id.desc()), user
    )
    if project_id:
        ensure_project_access(db, user, project_id)
        stmt = stmt.where(Deliverable.project_id == project_id)
    elif allowed is not None:
        stmt = stmt.where(Deliverable.project_id.in_(allowed or {-1}))
    if stage:
        stmt = stmt.where(Deliverable.stage == stage)
    if annual_plan_id:
        stmt = stmt.where(Deliverable.annual_plan_id == annual_plan_id)
    if version_id:
        stmt = stmt.where(Deliverable.version_id == version_id)
    if requirement_id:
        stmt = stmt.where(Deliverable.requirement_id == requirement_id)
    return [artifact_data(item) for item in db.scalars(stmt).all()]


def create_artifact_record(payload: ArtifactCreate, db: Db, user: User, **file_values) -> Deliverable:
    ensure_project_access(db, user, payload.project_id)
    validate_artifact_scope(payload)
    validate_hierarchy(db, payload.project_id, payload.annual_plan_id, payload.version_id, payload.requirement_id)
    item = Deliverable(**payload.model_dump(), uploaded_by=user.id, **file_values)
    return lock_artifact_version_mutable(db, item, user)


@router.post("/artifacts", status_code=201, summary="新建成果物元数据")
def create_artifact(payload: ArtifactCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "sales", "manager", "operator", "leader")
    item = create_artifact_record(payload, db, user)
    db.add(item)
    db.flush()
    write_audit(db, request, user.id, "create", "artifact", item.id)
    db.commit()
    return artifact_data(item)


@router.post("/artifacts/upload", status_code=201, summary="安全上传成果物附件")
async def upload_artifact(
    request: Request,
    db: Db,
    user: ReadyUser,
    project_id: int = Form(...),
    stage: int = Form(...),
    category: str = Form(...),
    title: str = Form(...),
    annual_plan_id: int | None = Form(None),
    version_id: int | None = Form(None),
    requirement_id: int | None = Form(None),
    file: UploadFile = File(...),
) -> dict:
    require_user_role(user, "admin", "sales", "manager", "operator", "leader")
    payload = ArtifactCreate(
        project_id=project_id,
        annual_plan_id=annual_plan_id,
        version_id=version_id,
        requirement_id=requirement_id,
        stage=stage,
        category=category,
        title=title,
    )
    original = Path(file.filename or "attachment").name
    suffix = Path(original).suffix.lower()
    if suffix in BLOCKED_UPLOAD_SUFFIXES:
        raise bad_request("FILE_TYPE_BLOCKED", "不允许上传可执行或脚本文件")
    settings = get_settings()
    upload_root = settings.upload_dir.resolve()
    day_dir = upload_root / utcnow().strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    storage_name = f"{uuid.uuid4().hex}{suffix}"
    target = (day_dir / storage_name).resolve()
    if upload_root not in target.parents:
        raise bad_request("FILE_PATH_INVALID", "附件存储路径无效")
    size = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        with target.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise bad_request("FILE_TOO_LARGE", f"附件不能超过 {settings.max_upload_mb} MB", 413)
                output.write(chunk)
        item = create_artifact_record(
            payload,
            db,
            user,
            original_filename=original,
            storage_key=str(target.relative_to(upload_root)).replace("\\", "/"),
            content_type=(file.content_type or mimetypes.guess_type(original)[0] or "application/octet-stream")[:150],
            size_bytes=size,
        )
        db.add(item)
        db.flush()
        write_audit(db, request, user.id, "upload", "artifact", item.id, after={"filename": original, "size": size})
        db.commit()
        return artifact_data(item)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post(
    "/versions/{version_id}/artifact-change-requests/upload",
    status_code=201,
    summary="上传冻结版本附件并自动创建变更申请",
)
async def upload_artifact_change_request(
    version_id: int,
    request: Request,
    db: Db,
    user: ReadyUser,
    file: UploadFile = File(...),
    change_title: str = Form(..., min_length=1, max_length=300),
    reason: str = Form(..., min_length=1, max_length=10000),
    artifact_id: int | None = Form(None),
    artifact_title: str | None = Form(None, min_length=1, max_length=300),
    stage: int | None = Form(None, ge=3, le=6),
    category: str | None = Form(None, min_length=1, max_length=50),
    requirement_id: int | None = Form(None),
) -> dict:
    require_user_role(user, "admin", "sales", "manager", "operator", "leader")
    version, plan = lock_version_scope(db, user, version_id)
    if version.status == "draft":
        raise bad_request(
            "CHANGE_NOT_REQUIRED",
            "草稿版本请直接上传成果物，无需创建附件变更",
            409,
        )
    expected_sequence = latest_baseline_sequence(db, version.id)
    if expected_sequence < 1:
        raise bad_request(
            "VERSION_BASELINE_MISSING",
            "已冻结版本缺少基线，不能上传变更附件",
            409,
        )

    token = uuid.uuid4().hex
    if artifact_id is not None:
        artifact = change_artifact(db, version.id, artifact_id)
        staged_stage = artifact.stage
        staged_category = artifact.category
        staged_title = artifact.title
        staged_requirement_id = artifact.requirement_id
        raw_payload = {
            "artifacts": [
                {
                    "action": "replace_file",
                    "artifact_id": artifact.id,
                    "upload_token": token,
                }
            ]
        }
        change_type = "artifact_file_replace"
    else:
        if stage is None or category is None or artifact_title is None:
            raise bad_request(
                "ARTIFACT_CHANGE_FIELDS_REQUIRED",
                "新增附件必须填写 stage、category 和 artifact_title",
            )
        payload = ArtifactCreate(
            project_id=plan.project_id,
            annual_plan_id=plan.id,
            version_id=version.id if stage in {3, 4, 5} else None,
            requirement_id=requirement_id,
            stage=stage,
            category=category,
            title=artifact_title,
        )
        validate_artifact_scope(payload)
        validate_hierarchy(
            db,
            payload.project_id,
            payload.annual_plan_id,
            payload.version_id,
            payload.requirement_id,
        )
        if stage == 6:
            requirement = db.get(Requirement, requirement_id)
            if not requirement or requirement.version_id != version.id:
                raise bad_request(
                    "ARTIFACT_SCOPE_INVALID",
                    "运维附件必须关联当前版本内的需求",
                )
        staged_stage = stage
        staged_category = category
        staged_title = artifact_title
        staged_requirement_id = requirement_id
        add_data = {
            "stage": stage,
            "category": category,
            "title": artifact_title,
            "upload_token": token,
        }
        if requirement_id is not None:
            add_data["requirement_id"] = requirement_id
        raw_payload = {"artifacts": [{"action": "add", "data": add_data}]}
        change_type = "artifact_file_add"

    validated_change = ChangeCreate(
        title=change_title,
        reason=reason,
        change_type=change_type,
        payload=raw_payload,
    )
    change_values = validated_change.model_dump(mode="json", exclude_unset=True)
    original = Path(file.filename or "attachment").name
    suffix = Path(original).suffix.lower()
    if suffix in BLOCKED_UPLOAD_SUFFIXES:
        raise bad_request("FILE_TYPE_BLOCKED", "不允许上传可执行或脚本文件")
    settings = get_settings()
    upload_root = settings.upload_dir.resolve()
    day_dir = upload_root / "pending" / utcnow().strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    target = (day_dir / f"{uuid.uuid4().hex}{suffix}").resolve()
    if upload_root not in target.parents:
        raise bad_request("FILE_PATH_INVALID", "附件暂存路径无效")
    size = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    try:
        with target.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise bad_request(
                        "FILE_TOO_LARGE",
                        f"附件不能超过 {settings.max_upload_mb} MB",
                        413,
                    )
                output.write(chunk)
                digest.update(chunk)
        change = ChangeRequest(
            version_id=version.id,
            requested_by=user.id,
            expected_baseline_sequence=expected_sequence,
            **change_values,
        )
        db.add(change)
        db.flush()
        staged = ArtifactChangeUpload(
            token=token,
            version_id=version.id,
            expected_baseline_sequence=expected_sequence,
            change_request_id=change.id,
            stage=staged_stage,
            category=staged_category,
            title=staged_title,
            requirement_id=staged_requirement_id,
            original_filename=original,
            storage_key=str(target.relative_to(upload_root)).replace("\\", "/"),
            content_type=(
                file.content_type
                or mimetypes.guess_type(original)[0]
                or "application/octet-stream"
            )[:150],
            size_bytes=size,
            sha256_hex=digest.hexdigest(),
            uploaded_by=user.id,
        )
        db.add(staged)
        db.flush()
        write_audit(
            db,
            request,
            user.id,
            "create",
            "change_request",
            change.id,
            after={
                "expected_baseline_sequence": expected_sequence,
                "artifact_upload_token": token,
            },
        )
        write_audit(
            db,
            request,
            user.id,
            "stage_upload",
            "artifact_change_upload",
            staged.id,
            after={"filename": original, "size": size},
        )
        db.commit()
        return {
            "change_request": {
                "id": change.id,
                "version_id": change.version_id,
                "expected_baseline_sequence": change.expected_baseline_sequence,
                "status": change.status,
                **change_values,
            },
            "staged_artifact": staged_artifact_data(staged),
        }
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get(
    "/artifact-change-uploads/{token}/download",
    summary="审批前预览暂存的变更附件",
)
def download_artifact_change_upload(
    token: str,
    db: Db,
    user: ReadyUser,
):
    if user.role == "customer":
        raise bad_request(
            "ARTIFACT_CHANGE_UPLOAD_FORBIDDEN",
            "客户无权访问尚未生效的变更附件",
            403,
        )
    item = db.scalar(
        select(ArtifactChangeUpload).where(ArtifactChangeUpload.token == token)
    )
    if not item:
        raise bad_request(
            "ARTIFACT_CHANGE_UPLOAD_NOT_FOUND",
            "变更附件不存在或已处理",
            404,
        )
    get_version_scope(db, user, item.version_id)
    root = get_settings().upload_dir.resolve()
    target = (root / item.storage_key).resolve()
    if root not in target.parents or not target.is_file():
        raise bad_request("FILE_NOT_FOUND", "变更附件文件不存在", 404)
    return FileResponse(
        target,
        media_type=item.content_type,
        filename=item.original_filename,
    )


def get_artifact_scope(artifact_id: int, db: Db, user: User) -> Deliverable:
    item = db.get(Deliverable, artifact_id)
    if not item:
        raise bad_request("ARTIFACT_NOT_FOUND", "成果物不存在", 404)
    ensure_project_access(db, user, item.project_id)
    if user.role == "customer":
        if not item.requirement_id:
            raise bad_request("ARTIFACT_FORBIDDEN", "客户只能访问本人需求关联的成果物", 403)
        if item.approval_status != "approved":
            raise bad_request("ARTIFACT_NOT_APPROVED", "客户只能访问已审批成果物", 403)
    if item.requirement_id:
        requirement = db.get(Requirement, item.requirement_id)
        if not requirement:
            raise bad_request("REQUIREMENT_NOT_FOUND", "成果物关联需求不存在", 404)
        ensure_requirement_access(user, requirement)
    return item


@router.post("/artifacts/{artifact_id}/submit", summary="提交成果物审批")
def submit_artifact(artifact_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "sales", "manager", "operator", "leader")
    item = get_artifact_scope(artifact_id, db, user)
    item = lock_artifact_version_mutable(db, item, user)
    if item.uploaded_by != user.id and user.role not in {"admin", "manager", "leader"}:
        raise bad_request("ARTIFACT_SUBMIT_FORBIDDEN", "只有上传人或项目管理角色可以提交成果物", 403)
    if item.approval_status not in {"draft", "rejected"}:
        raise bad_request("ARTIFACT_STATUS_INVALID", "只有草稿或已驳回成果物可以提交审批", 409)
    before = item.approval_status
    item.approval_status = "submitted"
    item.reviewed_by = None
    item.reviewed_at = None
    item.review_note = None
    write_audit(db, request, user.id, "submit", "artifact", item.id, {"status": before}, {"status": item.approval_status})
    db.commit()
    return artifact_data(item)


@router.patch("/artifacts/{artifact_id}/decision", summary="审批成果物")
def decide_artifact(
    artifact_id: int,
    payload: ArtifactDecisionIn,
    request: Request,
    db: Db,
    user: ReadyUser,
) -> dict:
    require_user_role(user, "admin", "manager", "leader")
    item = get_artifact_scope(artifact_id, db, user)
    item = lock_artifact_version_mutable(db, item, user)
    if item.approval_status != "submitted":
        raise bad_request("ARTIFACT_STATUS_INVALID", "只能审批已提交的成果物", 409)
    if item.uploaded_by == user.id:
        raise bad_request("ARTIFACT_SELF_APPROVAL", "成果物上传人不能审批自己的成果物", 409)
    item.approval_status = "approved" if payload.approved else "rejected"
    item.reviewed_by = user.id
    item.reviewed_at = utcnow()
    item.review_note = payload.note
    write_audit(
        db,
        request,
        user.id,
        "approve" if payload.approved else "reject",
        "artifact",
        item.id,
        after={"status": item.approval_status, "note": payload.note},
    )
    db.commit()
    return artifact_data(item)


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: int, db: Db, user: ReadyUser):
    item = get_artifact_scope(artifact_id, db, user)
    if not item.storage_key:
        raise bad_request("FILE_NOT_FOUND", "该成果物没有附件", 404)
    root = get_settings().upload_dir.resolve()
    target = (root / item.storage_key).resolve()
    if root not in target.parents or not target.is_file():
        raise bad_request("FILE_NOT_FOUND", "附件不存在", 404)
    return FileResponse(target, media_type=item.content_type, filename=item.original_filename)


@router.delete("/artifacts/{artifact_id}")
def delete_artifact(artifact_id: int, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "manager", "leader")
    item = db.get(Deliverable, artifact_id)
    if not item:
        raise bad_request("ARTIFACT_NOT_FOUND", "成果物不存在", 404)
    ensure_project_access(db, user, item.project_id)
    item = lock_artifact_version_mutable(db, item, user)
    storage_key = item.storage_key
    db.delete(item)
    write_audit(db, request, user.id, "delete", "artifact", item.id)
    db.commit()
    if storage_key:
        root = get_settings().upload_dir.resolve()
        target = (root / storage_key).resolve()
        if root in target.parents:
            target.unlink(missing_ok=True)
    return {"ok": True}


def operation_data(item: OperationFeedback) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "version_id": item.version_id,
        "requirement_id": item.requirement_id,
        "title": item.title,
        "content": item.content,
        "feedback_type": item.feedback_type,
        "status": item.status,
        "reporter_id": item.reporter_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def apply_operation_visibility(stmt, user: User):
    if user.role != "customer":
        return stmt
    return stmt.outerjoin(
        Requirement, OperationFeedback.requirement_id == Requirement.id
    ).where(
        or_(
            OperationFeedback.reporter_id == user.id,
            Requirement.requester_id == user.id,
        )
    )


@router.get("/operations")
def list_operations(db: Db, user: ReadyUser, project_id: int | None = None, status: str | None = None) -> list[dict]:
    allowed = project_ids_for(db, user)
    stmt = apply_operation_visibility(
        select(OperationFeedback).order_by(OperationFeedback.id.desc()), user
    )
    if project_id:
        ensure_project_access(db, user, project_id)
        stmt = stmt.where(OperationFeedback.project_id == project_id)
    elif allowed is not None:
        stmt = stmt.where(OperationFeedback.project_id.in_(allowed or {-1}))
    if status:
        stmt = stmt.where(OperationFeedback.status == status)
    return [operation_data(item) for item in db.scalars(stmt).all()]


@router.post("/operations", status_code=201)
def create_operation(payload: OperationCreate, request: Request, db: Db, user: ReadyUser) -> dict:
    ensure_project_access(db, user, payload.project_id)
    plan_id = None
    if payload.version_id:
        version = db.get(DeliveryVersion, payload.version_id)
        if not version:
            raise bad_request("VERSION_NOT_FOUND", "版本不存在", 404)
        plan = db.get(AnnualPlan, version.annual_plan_id)
        assert plan is not None
        if plan.project_id != payload.project_id:
            raise bad_request("VERSION_SCOPE_MISMATCH", "版本与项目不匹配")
        plan_id = plan.id
    if payload.requirement_id:
        requirement = db.get(Requirement, payload.requirement_id)
        if not requirement or requirement.project_id != payload.project_id:
            raise bad_request("REQUIREMENT_SCOPE_MISMATCH", "原需求与项目不匹配")
        if payload.version_id and requirement.version_id != payload.version_id:
            raise bad_request("REQUIREMENT_SCOPE_MISMATCH", "原需求与版本不匹配")
        ensure_requirement_access(user, requirement)
    item = OperationFeedback(**payload.model_dump(), reporter_id=user.id)
    db.add(item)
    db.flush()
    write_audit(db, request, user.id, "create", "operation_feedback", item.id)
    db.commit()
    return operation_data(item)


@router.patch("/operations/{operation_id}")
def patch_operation(operation_id: int, payload: OperationPatch, request: Request, db: Db, user: ReadyUser) -> dict:
    require_user_role(user, "admin", "operator", "manager", "leader")
    item = db.get(OperationFeedback, operation_id)
    if not item:
        raise bad_request("OPERATION_NOT_FOUND", "运营服务记录不存在", 404)
    ensure_project_access(db, user, item.project_id)
    transitions = {"open": {"processing", "closed"}, "processing": {"resolved", "closed"}, "resolved": {"closed", "processing"}, "closed": set()}
    if payload.status not in transitions.get(item.status, set()):
        raise bad_request("INVALID_TRANSITION", "运营服务状态流转无效")
    before = item.status
    item.status = payload.status
    write_audit(db, request, user.id, "transition", "operation_feedback", item.id, {"status": before}, {"status": item.status})
    db.commit()
    return operation_data(item)


@router.get("/milestones", summary="六阶段里程碑与验收成果提醒")
def milestones(project_id: int, db: Db, user: ReadyUser) -> dict:
    project = ensure_project_access(db, user, project_id)
    artifact_count_stmt = (
        select(Deliverable.stage, func.count())
        .where(Deliverable.project_id == project.id)
        .group_by(Deliverable.stage)
    )
    artifact_counts = dict(
        db.execute(apply_artifact_visibility(artifact_count_stmt, user)).all()
    )
    reminders = []
    if user.role != "customer":
        versions = db.scalars(select(DeliveryVersion).join(AnnualPlan).where(AnnualPlan.project_id == project.id)).all()
        for version in versions:
            requirements = db.scalars(select(Requirement).where(Requirement.version_id == version.id)).all()
            acceptance_ready = bool(requirements) and all(item.status in {"acceptance", "online", "closed"} for item in requirements)
            has_acceptance = bool(
                db.scalar(
                    select(Deliverable.id)
                    .where(
                        Deliverable.version_id == version.id,
                        Deliverable.stage == 5,
                        Deliverable.approval_status == "approved",
                    )
                    .limit(1)
                )
            )
            if acceptance_ready and not has_acceptance:
                reminders.append({"version_id": version.id, "version_name": version.name, "type": "acceptance_artifact_required", "message": "所有需求已进入验收或上线，请上传验收报告"})
    stage_names = ("宏观规划", "规划细化", "建设落地", "招投标", "项目交付验收", "运维运营")
    return {
        "project": project_data(project, user.role in MONEY_ROLES),
        "current_stage": project.current_stage,
        "stages": [
            {"stage": index, "name": name, "status": "completed" if index < project.current_stage else "current" if index == project.current_stage else "pending", "artifact_count": artifact_counts.get(index, 0)}
            for index, name in enumerate(stage_names, 1)
        ],
        "reminders": reminders,
    }


@router.get("/search", summary="全局搜索")
def global_search(q: str, db: Db, user: ReadyUser, limit: int = 50) -> dict:
    term = q.strip()
    if not term:
        return {"query": q, "results": []}
    limit = min(max(limit, 1), 100)
    pattern = f"%{term}%"
    allowed = project_ids_for(db, user)
    project_stmt = select(Project).where(or_(Project.code.like(pattern), Project.name.like(pattern)))
    requirement_stmt = (
        select(Requirement)
        .outerjoin(RequirementTag, RequirementTag.requirement_id == Requirement.id)
        .outerjoin(Tag, Tag.id == RequirementTag.tag_id)
        .where(
            or_(
                Requirement.code.like(pattern),
                Requirement.title.like(pattern),
                Requirement.description.like(pattern),
                Tag.name.like(pattern),
            )
        )
        .distinct()
    )
    version_stmt = select(DeliveryVersion, AnnualPlan).join(AnnualPlan).where(or_(DeliveryVersion.code.like(pattern), DeliveryVersion.name.like(pattern)))
    artifact_stmt = select(Deliverable).where(Deliverable.title.like(pattern))
    operation_stmt = select(OperationFeedback).where(or_(OperationFeedback.title.like(pattern), OperationFeedback.content.like(pattern)))
    artifact_stmt = apply_artifact_visibility(artifact_stmt, user)
    operation_stmt = apply_operation_visibility(operation_stmt, user)
    if allowed is not None:
        scope = allowed or {-1}
        project_stmt = project_stmt.where(Project.id.in_(scope))
        requirement_stmt = requirement_stmt.where(Requirement.project_id.in_(scope))
        version_stmt = version_stmt.where(AnnualPlan.project_id.in_(scope))
        artifact_stmt = artifact_stmt.where(Deliverable.project_id.in_(scope))
        operation_stmt = operation_stmt.where(OperationFeedback.project_id.in_(scope))
    if user.role == "customer":
        requirement_stmt = requirement_stmt.where(Requirement.requester_id == user.id)
    results = []
    results.extend({"type": "project", "id": item.id, "code": item.code, "title": item.name, "project_id": item.id} for item in db.scalars(project_stmt.limit(limit)).all())
    requirements = db.scalars(requirement_stmt.limit(limit)).all()
    project_map = {
        item.id: item.name
        for item in db.scalars(
            select(Project).where(
                Project.id.in_({item.project_id for item in requirements} or {-1})
            )
        ).all()
    }
    version_map = {
        item.id: item.name
        for item in db.scalars(
            select(DeliveryVersion).where(
                DeliveryVersion.id.in_({item.version_id for item in requirements if item.version_id} or {-1})
            )
        ).all()
    }
    for item in requirements:
        result = {
            "type": "requirement",
            "id": item.id,
            "code": item.code,
            "stable_key": item.stable_key,
            "title": item.title,
            "project_id": item.project_id,
            "annual_plan_id": item.annual_plan_id,
            "project_name": project_map.get(item.project_id),
            "version_id": item.version_id,
            "version_name": version_map.get(item.version_id),
            "status": item.status,
        }
        if user.role in MONEY_ROLES:
            result.update(
                estimated_budget=money(item.estimated_budget),
                allocated_budget=money(item.allocated_budget),
                actual_cost=money(item.actual_cost),
            )
        results.append(result)
    versions = db.execute(version_stmt.limit(limit)).all()
    artifacts = db.scalars(artifact_stmt.limit(limit)).all()
    operations = db.scalars(operation_stmt.limit(limit)).all()
    linked_requirements = {
        requirement.id: requirement
        for requirement in db.scalars(
            select(Requirement).where(
                Requirement.id.in_(
                    {
                        item.requirement_id
                        for item in [*artifacts, *operations]
                        if item.requirement_id
                    }
                    or {-1}
                )
            )
        ).all()
    }
    operation_version_ids = {
        item.version_id or linked_requirements.get(item.requirement_id).version_id
        for item in operations
        if item.version_id or linked_requirements.get(item.requirement_id)
    }
    operation_plan_map = {
        version.id: version.annual_plan_id
        for version in db.scalars(
            select(DeliveryVersion).where(
                DeliveryVersion.id.in_(operation_version_ids or {-1})
            )
        ).all()
    }
    results.extend(
        {
            "type": "version",
            "id": item.id,
            "code": item.code,
            "title": item.name,
            "project_id": plan.project_id,
            "annual_plan_id": plan.id,
            "version_id": item.id,
            "year": plan.year,
            "status": item.status,
        }
        for item, plan in versions
    )
    results.extend(
        {
            "type": "artifact",
            "id": item.id,
            "title": item.title,
            "project_id": item.project_id,
            "annual_plan_id": item.annual_plan_id or (linked_requirements.get(item.requirement_id).annual_plan_id if linked_requirements.get(item.requirement_id) else None),
            "version_id": item.version_id or (linked_requirements.get(item.requirement_id).version_id if linked_requirements.get(item.requirement_id) else None),
            "requirement_id": item.requirement_id,
            "stage": item.stage,
            "status": item.approval_status,
        }
        for item in artifacts
    )
    results.extend(
        {
            "type": "operation",
            "id": item.id,
            "title": item.title,
            "project_id": item.project_id,
            "annual_plan_id": operation_plan_map.get(item.version_id or (linked_requirements.get(item.requirement_id).version_id if linked_requirements.get(item.requirement_id) else None)),
            "version_id": item.version_id or (linked_requirements.get(item.requirement_id).version_id if linked_requirements.get(item.requirement_id) else None),
            "requirement_id": item.requirement_id,
            "status": item.status,
        }
        for item in operations
    )
    return {"query": term, "results": results[:limit]}


def csv_safe_cell(value: object) -> object:
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_response(filename: str, headers: list[str], rows: list[list[object]]) -> StreamingResponse:
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow([csv_safe_cell(value) for value in headers])
    writer.writerows([[csv_safe_cell(value) for value in row] for row in rows])
    payload = output.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/exports/requirements.csv")
def export_requirements(db: Db, user: ReadyUser, project_id: int | None = None):
    stmt = select(Requirement).order_by(Requirement.code)
    allowed = project_ids_for(db, user)
    if project_id:
        ensure_project_access(db, user, project_id)
        stmt = stmt.where(Requirement.project_id == project_id)
    elif allowed is not None:
        stmt = stmt.where(Requirement.project_id.in_(allowed or {-1}))
    if user.role == "customer":
        stmt = stmt.where(Requirement.requester_id == user.id)
    items = db.scalars(stmt).all()
    headers = ["需求编码", "稳定标识", "标题", "项目ID", "年度计划ID", "版本ID", "状态", "优先级"]
    rows = [[item.code, item.stable_key, item.title, item.project_id, item.annual_plan_id or "待规划", item.version_id or "待规划", item.status, item.priority] for item in items]
    if user.role in MONEY_ROLES:
        headers.extend(["预估预算", "已分配预算", "实际消耗"])
        for row, item in zip(rows, items):
            row.extend([money(item.estimated_budget), money(item.allocated_budget), money(item.actual_cost)])
    return csv_response("requirements.csv", headers, rows)


@router.get("/exports/project-progress.csv")
def export_project_progress(project_id: int, db: Db, user: ReadyUser):
    project = ensure_project_access(db, user, project_id)
    items = db.execute(
        select(Requirement, AnnualPlan, DeliveryVersion)
        .outerjoin(AnnualPlan, Requirement.annual_plan_id == AnnualPlan.id)
        .outerjoin(DeliveryVersion, Requirement.version_id == DeliveryVersion.id)
        .where(Requirement.project_id == project.id)
        .order_by(AnnualPlan.year, DeliveryVersion.code, Requirement.code)
    ).all()
    if user.role == "customer":
        items = [item for item in items if item[0].requester_id == user.id]
    headers = ["项目编码", "项目名称", "年度", "版本", "需求编码", "稳定标识", "需求标题", "状态", "优先级", "负责人ID"]
    rows = [
        [project.code, project.name, plan.year if plan else "待规划", version.code if version else "待规划", requirement.code, requirement.stable_key, requirement.title, requirement.status, requirement.priority, requirement.assignee_id or ""]
        for requirement, plan, version in items
    ]
    if user.role in MONEY_ROLES:
        headers.extend(["已分配预算", "实际消耗"])
        for row, (requirement, _, _) in zip(rows, items):
            row.extend([money(requirement.allocated_budget), money(requirement.actual_cost)])
    return csv_response("project-progress.csv", headers, rows)


@router.get("/exports/version-comparison.csv")
def export_version_comparison(left_id: int, right_id: int, db: Db, user: ReadyUser):
    comparison = compare_versions(db, user, left_id=left_id, right_id=right_id)
    rows: list[list[object]] = []
    for item in comparison["requirements"]["added"]:
        rows.append(["added", item.get("stable_key", item.get("code", "")), item.get("code", ""), "", item.get("title", ""), ""])
    for item in comparison["requirements"]["removed"]:
        rows.append(["removed", item.get("stable_key", item.get("code", "")), item.get("code", ""), item.get("title", ""), "", ""])
    for item in comparison["requirements"]["changed"]:
        rows.append(["changed", item["stable_key"], item["code"], item["left"].get("title", ""), item["right"].get("title", ""), ",".join(item["fields"])])
    if not rows:
        rows.append(["unchanged", "", "", "", "", comparison["requirements"]["unchanged_count"]])
    return csv_response(
        "version-comparison.csv",
        ["差异类型", "稳定标识", "需求编码", "左版本标题", "右版本标题", "变更字段/未变数"],
        rows,
    )


@router.get("/exports/artifacts.csv")
def export_artifacts(project_id: int, db: Db, user: ReadyUser):
    ensure_project_access(db, user, project_id)
    stmt = select(Deliverable).where(Deliverable.project_id == project_id).order_by(Deliverable.stage, Deliverable.id)
    items = db.scalars(apply_artifact_visibility(stmt, user)).all()
    return csv_response(
        "artifacts.csv",
        ["ID", "阶段", "类别", "标题", "年度ID", "版本ID", "需求ID", "审批状态", "审批人ID", "审批意见", "原文件名", "文件大小", "上传时间"],
        [[item.id, item.stage, item.category, item.title, item.annual_plan_id or "", item.version_id or "", item.requirement_id or "", item.approval_status, item.reviewed_by or "", item.review_note or "", item.original_filename or "", item.size_bytes or 0, item.created_at.isoformat()] for item in items],
    )


@router.get("/exports/operations.csv")
def export_operations(project_id: int, db: Db, user: ReadyUser):
    ensure_project_access(db, user, project_id)
    stmt = select(OperationFeedback).where(OperationFeedback.project_id == project_id).order_by(OperationFeedback.id)
    items = db.scalars(apply_operation_visibility(stmt, user)).all()
    return csv_response(
        "operations.csv",
        ["ID", "类型", "标题", "内容", "状态", "版本ID", "需求ID", "提交人ID", "创建时间"],
        [[item.id, item.feedback_type, item.title, item.content, item.status, item.version_id or "", item.requirement_id or "", item.reporter_id, item.created_at.isoformat()] for item in items],
    )


@router.get("/exports/funds.csv")
def export_funds(db: Db, user: ReadyUser, project_id: int | None = None):
    require_money(user)
    stmt = select(BudgetEntry).order_by(BudgetEntry.id)
    if project_id:
        ensure_project_access(db, user, project_id)
        stmt = stmt.where(BudgetEntry.project_id == project_id)
    items = db.scalars(stmt).all()
    return csv_response(
        "funds.csv",
        ["ID", "项目ID", "年度ID", "版本ID", "需求ID", "类型", "金额", "说明", "日期"],
        [[item.id, item.project_id, item.annual_plan_id or "", item.version_id or "", item.requirement_id or "", item.entry_type, money(item.amount), item.description, item.occurred_on.isoformat()] for item in items],
    )


@router.get("/exports/funding-applications.csv")
def export_applications(db: Db, user: ReadyUser, project_id: int | None = None):
    require_money(user)
    stmt = select(FundingApplication).order_by(FundingApplication.id)
    if project_id:
        ensure_project_access(db, user, project_id)
        stmt = stmt.where(FundingApplication.project_id == project_id)
    items = db.scalars(stmt).all()
    return csv_response(
        "funding-applications.csv",
        ["ID", "项目ID", "年度ID", "版本ID", "标题", "金额", "状态", "申请人ID"],
        [[item.id, item.project_id, item.annual_plan_id, item.version_id or "", item.title, money(item.amount), item.status, item.applicant_id] for item in items],
    )


@router.get("/audit", summary="操作日志溯源")
def audit_logs(
    db: Db,
    user: ReadyUser,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    require_user_role(user, "admin", "leader")
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    for column, value in ((AuditLog.entity_type, entity_type), (AuditLog.entity_id, entity_id), (AuditLog.actor_id, actor_id)):
        if value is not None:
            stmt = stmt.where(column == value)
    items = db.scalars(stmt.offset(max(offset, 0)).limit(min(max(limit, 1), 500))).all()
    return [
        {
            "id": item.id,
            "actor_id": item.actor_id,
            "action": item.action,
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "before_data": item.before_data,
            "after_data": item.after_data,
            "ip_address": item.ip_address,
            "created_at": item.created_at,
        }
        for item in items
    ]
