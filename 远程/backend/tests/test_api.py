from __future__ import annotations

from decimal import Decimal
import hashlib
import os
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


TEST_UPLOADS = Path(__file__).with_name("uploads")
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["COOKIE_SECURE"] = "false"
os.environ["AUTO_CREATE_TABLES"] = "false"
os.environ["AUTO_SEED"] = "false"
os.environ["UPLOAD_DIR"] = str(TEST_UPLOADS)

from app.config import Settings  # noqa: E402
from app.database import (  # noqa: E402
    Base,
    SessionLocal,
    database_engine_options,
    engine,
)
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    ArtifactChangeUpload,
    AnnualPlan,
    AuditLog,
    BudgetEntry,
    ChangeRequest,
    Deliverable,
    DeliveryVersion,
    Project,
    Requirement,
    RequirementTag,
    Tag,
    User,
    VersionBaseline,
)
from app.seed import DEFAULT_TAGS, seed_database  # noqa: E402
import app.core_api as core_api  # noqa: E402
import app.support_api as support_api  # noqa: E402
from app.support_api import csv_safe_cell  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_database(db)
    yield
    Base.metadata.drop_all(engine)
    shutil.rmtree(TEST_UPLOADS, ignore_errors=True)


@pytest.fixture
def client():
    with TestClient(app, base_url="http://testserver") as value:
        yield value


def login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def ready_admin(client: TestClient) -> str:
    csrf = login(client, "admin", "Admin@123456")
    response = client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": "Admin@123456", "new_password": "Changed@123456"},
    )
    assert response.status_code == 200, response.text
    return csrf


def post(client: TestClient, path: str, csrf: str, payload: dict):
    return client.post(path, headers={"X-CSRF-Token": csrf}, json=payload)


def make_project_tree(client: TestClient, csrf: str, second_version: bool = False) -> dict:
    project = post(
        client,
        "/api/projects",
        csrf,
        {"code": "PRJ-001", "name": "全流程项目", "total_budget": "2000000", "current_stage": 2},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    plan = post(
        client,
        "/api/plans",
        csrf,
        {"project_id": project_id, "year": 2026, "name": "2026 年度计划", "budget": "600000"},
    )
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["id"]
    version = post(
        client,
        "/api/versions",
        csrf,
        {"annual_plan_id": plan_id, "code": "V1.0", "name": "首版", "budget": "300000"},
    )
    assert version.status_code == 201, version.text
    result = {"project_id": project_id, "plan_id": plan_id, "version_id": version.json()["id"]}
    if second_version:
        second_plan = post(
            client,
            "/api/plans",
            csrf,
            {"project_id": project_id, "year": 2027, "name": "2027 年度计划", "budget": "700000"},
        )
        assert second_plan.status_code == 201, second_plan.text
        second = post(
            client,
            "/api/versions",
            csrf,
            {"annual_plan_id": second_plan.json()["id"], "code": "V2.0", "name": "跨年版", "budget": "400000"},
        )
        assert second.status_code == 201, second.text
        result.update(second_plan_id=second_plan.json()["id"], second_version_id=second.json()["id"])
    return result


def test_login_first_password_csrf_and_no_registration(client: TestClient):
    csrf = login(client, "admin", "Admin@123456")
    blocked = client.get("/api/projects")
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    no_csrf = client.post(
        "/api/auth/change-password",
        json={"current_password": "Admin@123456", "new_password": "Changed@123456"},
    )
    assert no_csrf.status_code == 403
    changed = client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": "Admin@123456", "new_password": "Changed@123456"},
    )
    assert changed.status_code == 200
    assert client.post("/api/auth/register", json={}).status_code == 404


def test_server_database_engine_uses_read_committed_isolation():
    mysql_options = database_engine_options(
        "mysql+pymysql://user:password@db/concurrency_test"
    )
    assert mysql_options["isolation_level"] == "READ COMMITTED"
    assert "isolation_level" not in database_engine_options("sqlite://")


def test_single_account_session_replacement_is_immediate(client: TestClient):
    ready_admin(client)
    second = TestClient(app, base_url="http://testserver")
    csrf2 = login(second, "admin", "Changed@123456")
    assert csrf2
    invalidated = client.get("/api/auth/me")
    assert invalidated.status_code == 401
    assert invalidated.json()["detail"]["code"] == "UNAUTHENTICATED"
    assert second.get("/api/auth/me").status_code == 200
    second.close()


def test_admin_user_rbac_project_whitelist_and_reset(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    customer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "customer1",
            "full_name": "客户一",
            "role": "customer",
            "initial_password": "Customer@123",
            "project_ids": [tree["project_id"]],
        },
    )
    assert customer.status_code == 201, customer.text
    assert customer.json()["project_ids"] == [tree["project_id"]]
    reset = post(client, f"/api/users/{customer.json()['id']}/reset-password", csrf, {"new_password": "ResetPwd@123"})
    assert reset.status_code == 200
    customer_client = TestClient(app, base_url="http://testserver")
    customer_csrf = login(customer_client, "customer1", "ResetPwd@123")
    changed = customer_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": customer_csrf},
        json={"current_password": "ResetPwd@123", "new_password": "Customer@456"},
    )
    assert changed.status_code == 200
    assert len(customer_client.get("/api/projects").json()) == 1
    assert customer_client.get(f"/api/funds/tree?project_id={tree['project_id']}").status_code == 403
    assert customer_client.get("/api/users").status_code == 403
    customer_context = customer_client.get("/api/context").json()
    assert "total_budget" not in customer_context["projects"][0]
    assert "budget" not in customer_context["plans"][0]
    forbidden_money = post(
        customer_client,
        "/api/requirements",
        customer_csrf,
        {
            "code": "REQ-CUSTOMER-MONEY",
            "title": "客户不应填资金",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "stakeholder_role": "customer",
            "estimated_budget": "100",
        },
    )
    assert forbidden_money.status_code == 403
    role_changed = client.patch(
        f"/api/users/{customer.json()['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"role": "sales"},
    )
    assert role_changed.status_code == 200
    assert customer_client.get("/api/auth/me").status_code == 401
    assert client.patch(
        "/api/users/1", headers={"X-CSRF-Token": csrf}, json={"role": "leader"}
    ).status_code == 409
    assert post(client, "/api/users/1/reset-password", csrf, {"new_password": "CannotSelf@123"}).status_code == 409
    customer_client.close()


def test_seed_normalizes_default_tags_and_merges_legacy_alias(client: TestClient):
    default_names = {name for name, _ in DEFAULT_TAGS}
    assert default_names == {
        "业务痛点",
        "功能优化",
        "运维 Bug",
        "招投标要求",
        "验收整改",
        "客户新增",
        "版本必做",
        "待确认",
    }

    with SessionLocal() as db:
        canonical = db.scalar(select(Tag).where(Tag.name == "运维 Bug"))
        assert canonical is not None
        canonical_id = canonical.id
        canonical.name = "运维Bug"
        db.commit()

    with SessionLocal() as db:
        assert seed_database(db) is False
        renamed = db.scalar(select(Tag).where(Tag.name == "运维 Bug"))
        assert renamed is not None and renamed.id == canonical_id
        assert db.scalar(select(Tag).where(Tag.name == "运维Bug")) is None
        assert default_names <= set(db.scalars(select(Tag.name)).all())
        assert seed_database(db) is False

    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    first = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-TAG-MERGE-1",
            "title": "同时关联新旧标签",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
            "tag_ids": [canonical_id],
        },
    )
    second = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-TAG-MERGE-2",
            "title": "仅关联旧标签",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_id = first.json()["id"]
    second_id = second.json()["id"]

    with SessionLocal() as db:
        legacy = Tag(name="运维Bug", color="#D97706")
        db.add(legacy)
        db.flush()
        db.add_all(
            [
                RequirementTag(requirement_id=first_id, tag_id=legacy.id),
                RequirementTag(requirement_id=second_id, tag_id=legacy.id),
            ]
        )
        db.commit()

    with SessionLocal() as db:
        assert seed_database(db) is False
        assert db.scalar(select(Tag).where(Tag.name == "运维Bug")) is None
        canonical = db.scalar(select(Tag).where(Tag.name == "运维 Bug"))
        assert canonical is not None and canonical.id == canonical_id
        links = set(
            db.execute(
                select(RequirementTag.requirement_id, RequirementTag.tag_id).where(
                    RequirementTag.requirement_id.in_([first_id, second_id])
                )
            ).all()
        )
        assert links == {
            (first_id, canonical_id),
            (second_id, canonical_id),
        }
        assert seed_database(db) is False


def test_core_crud_planning_pool_state_machine_claim_and_hours(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-001",
            "title": "需求统一收口",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "stakeholder_role": "customer",
            "estimated_budget": "50000",
            "tag_ids": [1],
        },
    )
    assert requirement.status_code == 201, requirement.text
    requirement_id = requirement.json()["id"]
    assert requirement.json()["planning_pool"] is True
    invalid = post(client, f"/api/requirements/{requirement_id}/transition", csrf, {"status": "scheduled", "note": "试图跳级"})
    assert invalid.status_code == 400
    assigned = client.patch(
        f"/api/requirements/{requirement_id}",
        headers={"X-CSRF-Token": csrf},
        json={"version_id": tree["version_id"]},
    )
    assert assigned.status_code == 200
    for status in ("planning", "scheduled"):
        response = post(client, f"/api/requirements/{requirement_id}/transition", csrf, {"status": status, "note": f"推进到 {status}"})
        assert response.status_code == 200, response.text
    missing_note = post(client, f"/api/requirements/{requirement_id}/transition", csrf, {"status": "developing"})
    assert missing_note.status_code == 422
    history = client.get(f"/api/requirements/{requirement_id}/history")
    assert history.status_code == 200
    assert [item["to_status"] for item in history.json()] == ["scheduled", "planning"]
    assert len(client.get("/api/context").json()["requirement_states"]) == 12
    developer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "developer1",
            "full_name": "研发一",
            "role": "developer",
            "initial_password": "Developer@123",
        },
    )
    assert developer.status_code == 201
    dev_client = TestClient(app, base_url="http://testserver")
    dev_csrf = login(dev_client, "developer1", "Developer@123")
    assert dev_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": dev_csrf},
        json={"current_password": "Developer@123", "new_password": "Developer@456"},
    ).status_code == 200
    claimed = post(dev_client, f"/api/requirements/{requirement_id}/claim", dev_csrf, {})
    assert claimed.status_code == 200, claimed.text
    hours = dev_client.patch(
        f"/api/requirements/{requirement_id}/hours",
        headers={"X-CSRF-Token": dev_csrf},
        json={"estimated_hours": "40", "actual_hours": "8"},
    )
    assert hours.status_code == 200
    assert hours.json()["actual_hours"] == "8.00"
    dev_client.close()


def test_project_delete_and_plan_creation_share_active_project_lock(client: TestClient):
    csrf = ready_admin(client)
    deleted_project = post(
        client,
        "/api/projects",
        csrf,
        {"code": "PRJ-DELETE-FIRST", "name": "先删除项目", "total_budget": "100"},
    ).json()
    deleted = client.delete(
        f"/api/projects/{deleted_project['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200, deleted.text
    after_delete = post(
        client,
        "/api/plans",
        csrf,
        {
            "project_id": deleted_project["id"],
            "year": 2026,
            "name": "不应写入已删除项目",
            "budget": "10",
        },
    )
    assert after_delete.status_code == 404, after_delete.text
    assert after_delete.json()["detail"]["code"] == "PROJECT_NOT_FOUND"

    populated_project = post(
        client,
        "/api/projects",
        csrf,
        {"code": "PRJ-PLAN-FIRST", "name": "先创建年度", "total_budget": "100"},
    ).json()
    created_plan = post(
        client,
        "/api/plans",
        csrf,
        {
            "project_id": populated_project["id"],
            "year": 2026,
            "name": "已提交年度",
            "budget": "10",
        },
    )
    assert created_plan.status_code == 201, created_plan.text
    blocked_delete = client.delete(
        f"/api/projects/{populated_project['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked_delete.status_code == 409, blocked_delete.text
    assert blocked_delete.json()["detail"]["code"] == "PROJECT_IN_USE"
    with SessionLocal() as db:
        assert db.scalar(
            select(AnnualPlan.id).where(
                AnnualPlan.project_id == deleted_project["id"]
            )
        ) is None
        stored_project = db.get(Project, populated_project["id"])
        assert stored_project is not None and stored_project.status == "active"


def test_requirement_operational_writes_lock_scope_and_continue_after_freeze(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-FROZEN-OPS",
            "title": "冻结后不可直接改运行字段",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    ).json()
    for status in ("planning", "scheduled"):
        transitioned = post(
            client,
            f"/api/requirements/{requirement['id']}/transition",
            csrf,
            {"status": status, "note": f"推进至 {status}"},
        )
        assert transitioned.status_code == 200, transitioned.text

    developer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "frozen_ops_developer",
            "full_name": "冻结运行字段研发",
            "role": "developer",
            "initial_password": "FrozenOps@123",
            "project_ids": [tree["project_id"]],
        },
    ).json()
    developer_client = TestClient(app, base_url="http://testserver")
    developer_csrf = login(
        developer_client, "frozen_ops_developer", "FrozenOps@123"
    )
    assert developer_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": developer_csrf},
        json={
            "current_password": "FrozenOps@123",
            "new_password": "FrozenOps@456",
        },
    ).status_code == 200
    frozen = post(client, f"/api/versions/{tree['version_id']}/freeze", csrf, {})
    assert frozen.status_code == 200, frozen.text
    original_lock = core_api.lock_requirement_scope
    lock_calls: list[int] = []

    def tracked_lock(db, user, requirement_id):
        lock_calls.append(requirement_id)
        return original_lock(db, user, requirement_id)

    monkeypatch.setattr(core_api, "lock_requirement_scope", tracked_lock)
    transitioned = post(
        client,
        f"/api/requirements/{requirement['id']}/transition",
        csrf,
        {"status": "developing", "note": "冻结后继续交付"},
    )
    assert transitioned.status_code == 200, transitioned.text
    claimed = post(
        developer_client,
        f"/api/requirements/{requirement['id']}/claim",
        developer_csrf,
        {},
    )
    assert claimed.status_code == 200, claimed.text
    hours = client.patch(
        f"/api/requirements/{requirement['id']}/hours",
        headers={"X-CSRF-Token": csrf},
        json={"estimated_hours": "8", "actual_hours": "2"},
    )
    assert hours.status_code == 200, hours.text
    assert lock_calls == [requirement["id"]] * 3
    with SessionLocal() as db:
        stored = db.get(Requirement, requirement["id"])
        assert stored is not None
        assert stored.status == "developing"
        assert stored.assignee_id == developer["id"]
        assert stored.estimated_hours == Decimal("8")
        assert stored.actual_hours == Decimal("2")
    developer_client.close()


def test_requirement_delete_rechecks_state_after_row_lock(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    csrf = ready_admin(client)
    project = post(
        client,
        "/api/projects",
        csrf,
        {"code": "PRJ-DELETE-RACE", "name": "需求删除竞态"},
    ).json()
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-DELETE-RACE",
            "title": "等待删除的需求",
            "project_id": project["id"],
            "stakeholder_role": "manager",
        },
    ).json()
    original_lock = core_api.lock_requirement_scope

    def transitioned_after_wait(db, user, requirement_id):
        item, version = original_lock(db, user, requirement_id)
        item.status = "planning"
        return item, version

    monkeypatch.setattr(
        core_api, "lock_requirement_scope", transitioned_after_wait
    )
    rejected = client.delete(
        f"/api/requirements/{requirement['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "REQUIREMENT_IN_USE"
    with SessionLocal() as db:
        stored = db.get(Requirement, requirement["id"])
        assert stored is not None and stored.status == "draft"


def test_version_compare_gate_freeze_baseline_and_cross_year(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    gate = client.get(f"/api/versions/compare?project_id={tree['project_id']}")
    assert gate.status_code == 400
    assert gate.json()["detail"]["code"] == "INSUFFICIENT_VERSIONS"
    second_plan = post(
        client,
        "/api/plans",
        csrf,
        {"project_id": tree["project_id"], "year": 2027, "name": "2027 计划", "budget": "700000"},
    ).json()
    second = post(
        client,
        "/api/versions",
        csrf,
        {"annual_plan_id": second_plan["id"], "code": "V2.0", "name": "跨年版", "budget": "400000"},
    ).json()
    frozen = post(client, f"/api/versions/{tree['version_id']}/freeze", csrf, {})
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["baseline"]["snapshot"]["schema_version"] == 1
    compare = client.get(
        f"/api/versions/compare?left_id={tree['version_id']}&right_id={second['id']}"
    )
    assert compare.status_code == 200, compare.text
    assert compare.json()["cross_year"] is True
    locked = client.patch(
        f"/api/versions/{tree['version_id']}",
        headers={"X-CSRF-Token": csrf},
        json={"name": "不应直接改"},
    )
    assert locked.status_code == 409


def test_fund_tree_artifacts_operations_search_export_and_audit(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-FUND",
            "title": "资金四级穿透",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "sales",
            "estimated_budget": "50000",
        },
    ).json()
    allocation = post(
        client,
        "/api/funds/entries",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "requirement_id": requirement["id"],
            "entry_type": "allocation",
            "amount": "50000",
            "description": "需求预算分配",
        },
    )
    assert allocation.status_code == 201, allocation.text
    entry = post(
        client,
        "/api/funds/entries",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "requirement_id": requirement["id"],
            "entry_type": "actual",
            "amount": "12000",
            "description": "首期投入",
        },
    )
    assert entry.status_code == 201, entry.text
    annual_pending = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-ANNUAL-PENDING",
            "title": "年度内待规划需求",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "stakeholder_role": "customer",
            "estimated_budget": "8000",
        },
    ).json()
    unscoped_pending = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-UNSCOPED-PENDING",
            "title": "无年度待规划需求",
            "project_id": tree["project_id"],
            "stakeholder_role": "customer",
            "estimated_budget": "6000",
        },
    ).json()
    fund_tree = client.get(f"/api/funds/tree?project_id={tree['project_id']}")
    assert fund_tree.status_code == 200
    fund_payload = fund_tree.json()
    assert fund_payload["children"][0]["children"][0]["status"] == "draft"
    assert fund_payload["children"][0]["children"][0]["children"][0]["actual"] == "12000.00"
    annual_pool = next(
        node
        for node in fund_payload["children"][0]["children"]
        if node.get("planning_pool")
    )
    assert annual_pool["name"] == "年度内待规划"
    assert [node["id"] for node in annual_pool["children"]] == [annual_pending["id"]]
    unscoped_pool = next(
        node for node in fund_payload["children"] if node.get("planning_pool")
    )
    assert unscoped_pool["name"] == "无年度待规划"
    assert [node["id"] for node in unscoped_pool["children"][0]["children"]] == [unscoped_pending["id"]]
    assert fund_payload["actual"] == "12000.00"
    artifact = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stage": 3,
            "category": "task_book",
            "title": "任务书方案",
        },
    )
    assert artifact.status_code == 201, artifact.text
    operation = post(
        client,
        "/api/operations",
        csrf,
        {
            "project_id": tree["project_id"],
            "version_id": tree["version_id"],
            "requirement_id": requirement["id"],
            "title": "上线问题",
            "content": "需要处理",
            "feedback_type": "bug",
        },
    )
    assert operation.status_code == 201
    search_results = client.get("/api/search?q=资金").json()["results"]
    search_requirement = next(
        item for item in search_results if item["type"] == "requirement"
    )
    assert search_requirement["project_name"] == "全流程项目"
    assert search_requirement["annual_plan_id"] == tree["plan_id"]
    assert search_requirement["version_id"] == tree["version_id"]
    assert search_requirement["version_name"] == "首版"
    assert search_requirement["estimated_budget"] == "50000.00"
    assert search_requirement["allocated_budget"] == "50000.00"
    assert search_requirement["actual_cost"] == "12000.00"
    artifact_result = next(
        item
        for item in client.get("/api/search?q=任务书").json()["results"]
        if item["type"] == "artifact"
    )
    assert artifact_result["project_id"] == tree["project_id"]
    assert artifact_result["annual_plan_id"] == tree["plan_id"]
    assert artifact_result["version_id"] == tree["version_id"]
    assert artifact_result["stage"] == 3
    operation_result = next(
        item
        for item in client.get("/api/search?q=上线问题").json()["results"]
        if item["type"] == "operation"
    )
    assert operation_result["project_id"] == tree["project_id"]
    assert operation_result["annual_plan_id"] == tree["plan_id"]
    assert operation_result["version_id"] == tree["version_id"]
    assert operation_result["requirement_id"] == requirement["id"]
    dashboard = client.get(
        f"/api/dashboard?project_id={tree['project_id']}"
    ).json()
    recent_requirement = next(
        item for item in dashboard["recent_requirements"] if item["id"] == requirement["id"]
    )
    assert recent_requirement["version_id"] == tree["version_id"]
    assert recent_requirement["assignee_id"] is None
    assert recent_requirement["updated_at"]
    assert recent_requirement["estimated_budget"] == "50000.00"
    exported = client.get(f"/api/exports/requirements.csv?project_id={tree['project_id']}")
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert client.get("/api/audit").status_code == 200
    assert client.get(f"/api/milestones?project_id={tree['project_id']}").status_code == 200


def test_failed_login_lockout(client: TestClient):
    for _ in range(5):
        response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
        assert response.status_code == 401
    locked = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@123456"})
    assert locked.status_code == 423
    assert locked.json()["detail"]["code"] == "ACCOUNT_LOCKED"
    with SessionLocal() as db:
        actions = list(
            db.scalars(
                select(AuditLog.action).where(AuditLog.entity_type == "authentication")
            )
        )
    assert actions.count("login_failed") == 5
    assert actions.count("login_blocked") == 1


def test_requirement_field_visibility_by_role(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-VISIBILITY",
            "title": "字段权限",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
            "estimated_budget": "10000",
        },
    ).json()
    assert client.patch(
        f"/api/requirements/{requirement['id']}/hours",
        headers={"X-CSRF-Token": csrf},
        json={"estimated_hours": "20", "actual_hours": "5"},
    ).status_code == 200
    role_clients = {}
    for role in ("customer", "sales", "developer"):
        created = post(
            client,
            "/api/users",
            csrf,
            {
                "username": f"{role}2",
                "full_name": role,
                "role": role,
                "initial_password": f"{role.title()}@1234",
                "project_ids": [tree["project_id"]] if role == "customer" else [],
            },
        ).json()
        role_client = TestClient(app, base_url="http://testserver")
        role_csrf = login(role_client, f"{role}2", f"{role.title()}@1234")
        assert role_client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": role_csrf},
            json={"current_password": f"{role.title()}@1234", "new_password": f"{role.title()}@5678"},
        ).status_code == 200
        role_clients[role] = (role_client, role_csrf)
    customer_data = role_clients["customer"][0].get(f"/api/requirements/{requirement['id']}").json()
    sales_data = role_clients["sales"][0].get(f"/api/requirements/{requirement['id']}").json()
    developer_data = role_clients["developer"][0].get(f"/api/requirements/{requirement['id']}").json()
    assert not {"estimated_budget", "allocated_budget", "actual_cost", "estimated_hours", "actual_hours"} & customer_data.keys()
    assert {"estimated_budget", "allocated_budget", "actual_cost"} <= sales_data.keys()
    assert not {"estimated_hours", "actual_hours"} & sales_data.keys()
    assert {"estimated_hours", "actual_hours"} <= developer_data.keys()
    assert not {"estimated_budget", "allocated_budget", "actual_cost"} & developer_data.keys()
    forbidden = role_clients["developer"][0].patch(
        f"/api/requirements/{requirement['id']}",
        headers={"X-CSRF-Token": role_clients["developer"][1]},
        json={"actual_cost": "1"},
    )
    assert forbidden.status_code == 422
    for role_client, _ in role_clients.values():
        role_client.close()


def test_budget_hierarchy_limits_and_explicit_overrun(client: TestClient):
    csrf = ready_admin(client)
    project = post(
        client,
        "/api/projects",
        csrf,
        {"code": "PRJ-LIMIT", "name": "预算边界", "total_budget": "100"},
    ).json()
    plan = post(
        client,
        "/api/plans",
        csrf,
        {"project_id": project["id"], "year": 2026, "name": "2026", "budget": "80"},
    ).json()
    over_plan = post(
        client,
        "/api/plans",
        csrf,
        {"project_id": project["id"], "year": 2027, "name": "2027", "budget": "21"},
    )
    assert over_plan.status_code == 409
    assert over_plan.json()["detail"]["code"] == "PROJECT_BUDGET_EXCEEDED"
    assert client.patch(
        f"/api/projects/{project['id']}", headers={"X-CSRF-Token": csrf}, json={"total_budget": "79"}
    ).status_code == 409
    version = post(
        client,
        "/api/versions",
        csrf,
        {"annual_plan_id": plan["id"], "code": "V-LIMIT", "name": "限额版本", "budget": "50"},
    ).json()
    assert post(
        client,
        "/api/versions",
        csrf,
        {"annual_plan_id": plan["id"], "code": "V-OVER", "name": "超额版本", "budget": "31"},
    ).status_code == 409
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-LIMIT",
            "title": "限额需求",
            "project_id": project["id"],
            "annual_plan_id": plan["id"],
            "version_id": version["id"],
            "stakeholder_role": "manager",
        },
    ).json()
    base_entry = {
        "project_id": project["id"],
        "annual_plan_id": plan["id"],
        "version_id": version["id"],
        "requirement_id": requirement["id"],
    }
    assert post(client, "/api/funds/entries", csrf, {**base_entry, "entry_type": "allocation", "amount": "40"}).status_code == 201
    assert post(client, "/api/funds/entries", csrf, {**base_entry, "entry_type": "allocation", "amount": "11"}).status_code == 409
    overrun = post(client, "/api/funds/entries", csrf, {**base_entry, "entry_type": "actual", "amount": "41"})
    assert overrun.status_code == 409
    assert overrun.json()["detail"]["code"] == "ACTUAL_OVERRUN"
    explicit = post(
        client,
        "/api/funds/entries",
        csrf,
        {**base_entry, "entry_type": "actual", "amount": "41", "allow_actual_overrun": True},
    )
    assert explicit.status_code == 201
    not_finite = post(client, "/api/funds/entries", csrf, {**base_entry, "entry_type": "actual", "amount": "NaN"})
    assert not_finite.status_code == 422


def test_allocation_capacity_uses_locked_current_requirement_rows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    csrf = ready_admin(client)
    project = post(
        client,
        "/api/projects",
        csrf,
        {"code": "PRJ-CURRENT-READ", "name": "当前读预算", "total_budget": "100"},
    ).json()
    plan = post(
        client,
        "/api/plans",
        csrf,
        {
            "project_id": project["id"],
            "year": 2026,
            "name": "当前读年度",
            "budget": "100",
        },
    ).json()
    version = post(
        client,
        "/api/versions",
        csrf,
        {
            "annual_plan_id": plan["id"],
            "code": "V-CURRENT",
            "name": "当前读版本",
            "budget": "100",
        },
    ).json()
    requirements = []
    for index in (1, 2):
        requirements.append(
            post(
                client,
                "/api/requirements",
                csrf,
                {
                    "code": f"REQ-CURRENT-{index}",
                    "title": f"并发资金需求 {index}",
                    "project_id": project["id"],
                    "annual_plan_id": plan["id"],
                    "version_id": version["id"],
                    "stakeholder_role": "manager",
                },
            ).json()
        )
    base = {
        "project_id": project["id"],
        "annual_plan_id": plan["id"],
        "version_id": version["id"],
        "entry_type": "allocation",
        "amount": "60",
    }
    first = post(
        client,
        "/api/funds/entries",
        csrf,
        {**base, "requirement_id": requirements[0]["id"]},
    )
    assert first.status_code == 201, first.text

    original_scalar = Session.scalar
    original_scalars = Session.scalars
    locking_requirement_reads: list[bool] = []

    def stale_aggregate(db: Session, statement, *args, **kwargs):
        sql = str(statement).lower()
        if "sum(requirements.allocated_budget)" in sql:
            return Decimal(0)
        return original_scalar(db, statement, *args, **kwargs)

    def track_locking_read(db: Session, statement, *args, **kwargs):
        descriptions = getattr(statement, "column_descriptions", ())
        if (
            descriptions
            and descriptions[0].get("entity") is Requirement
            and getattr(statement, "_for_update_arg", None) is not None
        ):
            locking_requirement_reads.append(True)
        return original_scalars(db, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", stale_aggregate)
    monkeypatch.setattr(Session, "scalars", track_locking_read)
    second = post(
        client,
        "/api/funds/entries",
        csrf,
        {**base, "requirement_id": requirements[1]["id"]},
    )
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "VERSION_ALLOCATION_EXCEEDED"
    assert locking_requirement_reads
    with SessionLocal() as db:
        stored = db.scalars(
            select(Requirement).where(Requirement.id.in_([item["id"] for item in requirements]))
        ).all()
        assert sum((item.allocated_budget for item in stored), Decimal(0)) == Decimal("60")


def test_planning_and_fund_write_permissions_match_role_boundaries(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-ROLE-FUND",
            "title": "资金权限边界",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    ).json()

    role_clients: dict[str, tuple[TestClient, str]] = {}
    for role in ("sales", "manager"):
        username = f"boundary_{role}"
        created = post(
            client,
            "/api/users",
            csrf,
            {
                "username": username,
                "full_name": f"{role} 权限验证",
                "role": role,
                "initial_password": f"{role.title()}@1234",
                "project_ids": [tree["project_id"]],
            },
        )
        assert created.status_code == 201, created.text
        role_client = TestClient(app, base_url="http://testserver")
        role_csrf = login(role_client, username, f"{role.title()}@1234")
        assert role_client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": role_csrf},
            json={
                "current_password": f"{role.title()}@1234",
                "new_password": f"{role.title()}@5678",
            },
        ).status_code == 200
        role_clients[role] = (role_client, role_csrf)

    for role, (role_client, role_csrf) in role_clients.items():
        responses = (
            role_client.patch(
                f"/api/projects/{tree['project_id']}",
                headers={"X-CSRF-Token": role_csrf},
                json={"name": f"{role} 不可修改项目"},
            ),
            post(
                role_client,
                "/api/plans",
                role_csrf,
                {
                    "project_id": tree["project_id"],
                    "year": 2030,
                    "name": "不可创建年度计划",
                    "budget": "1",
                },
            ),
            role_client.patch(
                f"/api/plans/{tree['plan_id']}",
                headers={"X-CSRF-Token": role_csrf},
                json={"name": "不可修改年度计划"},
            ),
            post(
                role_client,
                "/api/versions",
                role_csrf,
                {
                    "annual_plan_id": tree["plan_id"],
                    "code": f"NO-{role}",
                    "name": "不可创建版本",
                    "budget": "1",
                },
            ),
            role_client.patch(
                f"/api/versions/{tree['version_id']}",
                headers={"X-CSRF-Token": role_csrf},
                json={"name": "不可修改版本"},
            ),
        )
        assert all(response.status_code == 403 for response in responses)

    sales_client, sales_csrf = role_clients["sales"]
    rejected_entry = post(
        sales_client,
        "/api/funds/entries",
        sales_csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "requirement_id": requirement["id"],
            "entry_type": "allocation",
            "amount": "10",
        },
    )
    assert rejected_entry.status_code == 403
    application = post(
        sales_client,
        "/api/funds/applications",
        sales_csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "title": "销售仍可维护资金申报",
            "amount": "10",
        },
    )
    assert application.status_code == 201, application.text

    manager_client, manager_csrf = role_clients["manager"]
    accepted_entry = post(
        manager_client,
        "/api/funds/entries",
        manager_csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "requirement_id": requirement["id"],
            "entry_type": "allocation",
            "amount": "10",
        },
    )
    assert accepted_entry.status_code == 201, accepted_entry.text
    for role_client, _ in role_clients.values():
        role_client.close()


def test_version_requirement_and_fund_writes_recheck_freeze_after_lock(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-FREEZE-RACE",
            "title": "冻结竞态需求",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    ).json()

    original_core_lock = core_api.lock_version_scope
    original_support_lock = support_api.lock_version_scope

    def frozen_core_scope(db, user, version_id):
        version, plan = original_core_lock(db, user, version_id)
        version.status = "frozen"
        return version, plan

    def frozen_support_scope(db, user, version_id):
        version, plan = original_support_lock(db, user, version_id)
        version.status = "frozen"
        return version, plan

    monkeypatch.setattr(core_api, "lock_version_scope", frozen_core_scope)
    core_responses = (
        client.patch(
            f"/api/versions/{tree['version_id']}",
            headers={"X-CSRF-Token": csrf},
            json={"name": "不应越过冻结"},
        ),
        post(
            client,
            "/api/requirements",
            csrf,
            {
                "code": "REQ-AFTER-FREEZE",
                "title": "不应新增",
                "project_id": tree["project_id"],
                "annual_plan_id": tree["plan_id"],
                "version_id": tree["version_id"],
                "stakeholder_role": "manager",
            },
        ),
        client.patch(
            f"/api/requirements/{requirement['id']}",
            headers={"X-CSRF-Token": csrf},
            json={"title": "不应修改"},
        ),
    )
    for response in core_responses:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "VERSION_LOCKED"

    monkeypatch.setattr(support_api, "lock_version_scope", frozen_support_scope)
    fund_response = post(
        client,
        "/api/funds/entries",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "requirement_id": requirement["id"],
            "entry_type": "allocation",
            "amount": "10",
        },
    )
    assert fund_response.status_code == 409, fund_response.text
    assert fund_response.json()["detail"]["code"] == "VERSION_LOCKED"
    with SessionLocal() as db:
        version = db.get(DeliveryVersion, tree["version_id"])
        assert version is not None and version.status == "draft"
        stored_requirement = db.get(Requirement, requirement["id"])
        assert stored_requirement is not None
        assert stored_requirement.title == "冻结竞态需求"


def test_concurrent_unique_constraint_failures_return_specific_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    original_flush = Session.flush

    def conflicting_flush(db: Session, *args, **kwargs):
        for item in db.new:
            message = None
            if isinstance(item, Project) and item.code == "PRJ-RACE":
                message = "UNIQUE constraint failed: projects.code"
            elif isinstance(item, AnnualPlan) and item.year == 2030:
                message = "UNIQUE constraint failed: annual_plans.project_id, annual_plans.year"
            elif isinstance(item, DeliveryVersion) and item.code == "V-RACE":
                message = "Duplicate entry for key 'uq_plan_version_code'"
            elif isinstance(item, Requirement) and item.code == "REQ-RACE-CODE":
                message = "Duplicate entry for key 'ix_requirements_code'"
            elif isinstance(item, Requirement) and item.stable_key == "STABLE-RACE":
                message = "UNIQUE constraint failed: requirements.version_id, requirements.stable_key"
            if message:
                raise IntegrityError("INSERT", {}, Exception(message))
        return original_flush(db, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", conflicting_flush)
    cases = (
        (
            post(client, "/api/projects", csrf, {"code": "PRJ-RACE", "name": "并发项目"}),
            "PROJECT_CODE_EXISTS",
        ),
        (
            post(
                client,
                "/api/plans",
                csrf,
                {
                    "project_id": tree["project_id"],
                    "year": 2030,
                    "name": "并发年度",
                    "budget": "1",
                },
            ),
            "PLAN_EXISTS",
        ),
        (
            post(
                client,
                "/api/versions",
                csrf,
                {
                    "annual_plan_id": tree["plan_id"],
                    "code": "V-RACE",
                    "name": "并发版本",
                    "budget": "1",
                },
            ),
            "VERSION_CODE_EXISTS",
        ),
        (
            post(
                client,
                "/api/requirements",
                csrf,
                {
                    "code": "REQ-RACE-CODE",
                    "title": "并发需求编码",
                    "project_id": tree["project_id"],
                    "annual_plan_id": tree["plan_id"],
                    "version_id": tree["version_id"],
                    "stakeholder_role": "manager",
                },
            ),
            "REQUIREMENT_CODE_EXISTS",
        ),
        (
            post(
                client,
                "/api/requirements",
                csrf,
                {
                    "code": "REQ-RACE-STABLE",
                    "stable_key": "STABLE-RACE",
                    "title": "并发稳定标识",
                    "project_id": tree["project_id"],
                    "annual_plan_id": tree["plan_id"],
                    "version_id": tree["version_id"],
                    "stakeholder_role": "manager",
                },
            ),
            "STABLE_KEY_EXISTS",
        ),
    )
    for response, expected_code in cases:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == expected_code
    recovered = post(
        client,
        "/api/projects",
        csrf,
        {"code": "PRJ-AFTER-RACE", "name": "异常回滚后可继续"},
    )
    assert recovered.status_code == 201, recovered.text


def test_parent_budget_capacity_is_rechecked_after_scope_lock(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    csrf = ready_admin(client)
    project = post(
        client,
        "/api/projects",
        csrf,
        {"code": "PRJ-PARENT-RACE", "name": "项目预算并发", "total_budget": "100"},
    ).json()
    original_project_lock = core_api.lock_project_scope

    def project_lock_with_competing_plan(db, user, project_id):
        locked = original_project_lock(db, user, project_id)
        db.add(
            AnnualPlan(
                project_id=project_id,
                year=2027,
                name="已抢先写入的年度",
                budget=Decimal("60"),
            )
        )
        db.flush()
        return locked

    monkeypatch.setattr(core_api, "lock_project_scope", project_lock_with_competing_plan)
    project_overrun = post(
        client,
        "/api/plans",
        csrf,
        {
            "project_id": project["id"],
            "year": 2028,
            "name": "等待锁的年度",
            "budget": "50",
        },
    )
    assert project_overrun.status_code == 409, project_overrun.text
    assert project_overrun.json()["detail"]["code"] == "PROJECT_BUDGET_EXCEEDED"

    monkeypatch.setattr(core_api, "lock_project_scope", original_project_lock)
    plan = post(
        client,
        "/api/plans",
        csrf,
        {
            "project_id": project["id"],
            "year": 2028,
            "name": "版本预算并发年度",
            "budget": "100",
        },
    ).json()
    original_plan_lock = core_api.lock_plan_scope

    def plan_lock_with_competing_version(db, user, plan_id):
        locked_plan, locked_project = original_plan_lock(db, user, plan_id)
        db.add(
            DeliveryVersion(
                annual_plan_id=plan_id,
                code="V-WINNER",
                name="已抢先写入的版本",
                budget=Decimal("60"),
            )
        )
        db.flush()
        return locked_plan, locked_project

    monkeypatch.setattr(core_api, "lock_plan_scope", plan_lock_with_competing_version)
    plan_overrun = post(
        client,
        "/api/versions",
        csrf,
        {
            "annual_plan_id": plan["id"],
            "code": "V-WAITER",
            "name": "等待锁的版本",
            "budget": "50",
        },
    )
    assert plan_overrun.status_code == 409, plan_overrun.text
    assert plan_overrun.json()["detail"]["code"] == "PLAN_BUDGET_EXCEEDED"


def test_budget_entry_write_types_reject_legacy_but_keep_read_export_compatibility(
    client: TestClient,
):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    for legacy_type in ("planned", "frozen"):
        rejected = post(
            client,
            "/api/funds/entries",
            csrf,
            {
                "project_id": tree["project_id"],
                "entry_type": legacy_type,
                "amount": "10",
            },
        )
        assert rejected.status_code == 422

    with SessionLocal() as db:
        db.add_all(
            BudgetEntry(
                project_id=tree["project_id"],
                entry_type=legacy_type,
                amount=Decimal("10"),
                description=f"legacy-{legacy_type}",
                created_by=1,
            )
            for legacy_type in ("planned", "frozen")
        )
        db.commit()
    entries = client.get(f"/api/funds/entries?project_id={tree['project_id']}")
    assert entries.status_code == 200, entries.text
    assert {item["entry_type"] for item in entries.json()} == {"planned", "frozen"}
    exported = client.get(f"/api/exports/funds.csv?project_id={tree['project_id']}")
    assert exported.status_code == 200, exported.text
    exported_text = exported.content.decode("utf-8-sig")
    assert "planned" in exported_text and "frozen" in exported_text


def test_funding_separation_of_duties_and_artifact_scope(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    sales = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "fundsales",
            "full_name": "申报销售",
            "role": "sales",
            "initial_password": "FundSales@123",
        },
    ).json()
    sales_client = TestClient(app, base_url="http://testserver")
    sales_csrf = login(sales_client, "fundsales", "FundSales@123")
    assert sales_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": sales_csrf},
        json={"current_password": "FundSales@123", "new_password": "FundSales@456"},
    ).status_code == 200
    application = post(
        sales_client,
        "/api/funds/applications",
        sales_csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "title": "年度资金申报",
            "amount": "100000",
        },
    ).json()
    assert application["applicant_name"] == "申报销售"
    assert sales_client.patch(
        f"/api/funds/applications/{application['id']}/status",
        headers={"X-CSRF-Token": sales_csrf},
        json={"status": "submitted"},
    ).status_code == 200
    sales_review = sales_client.patch(
        f"/api/funds/applications/{application['id']}/status",
        headers={"X-CSRF-Token": sales_csrf},
        json={"status": "reviewing"},
    )
    assert sales_review.status_code == 403
    for next_status in ("reviewing", "approved", "disbursed"):
        reviewed = client.patch(
            f"/api/funds/applications/{application['id']}/status",
            headers={"X-CSRF-Token": csrf},
            json={"status": next_status},
        )
        assert reviewed.status_code == 200, reviewed.text

    own_application = post(
        client,
        "/api/funds/applications",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "title": "管理员申报",
            "amount": "1",
        },
    ).json()
    assert client.patch(
        f"/api/funds/applications/{own_application['id']}/status",
        headers={"X-CSRF-Token": csrf},
        json={"status": "submitted"},
    ).status_code == 200
    self_review = client.patch(
        f"/api/funds/applications/{own_application['id']}/status",
        headers={"X-CSRF-Token": csrf},
        json={"status": "reviewing"},
    )
    assert self_review.status_code == 409
    assert self_review.json()["detail"]["code"] == "FUNDING_SELF_REVIEW"

    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-ART-SCOPE",
            "title": "成果物层级",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "operator",
        },
    ).json()
    bad_stage3 = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "requirement_id": requirement["id"],
            "stage": 3,
            "category": "task_book",
            "title": "错误层级",
        },
    )
    assert bad_stage3.status_code == 400
    bad_stage6 = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "requirement_id": requirement["id"],
            "stage": 6,
            "category": "operation_feedback",
            "title": "重复挂载",
        },
    )
    assert bad_stage6.status_code == 400
    sales_client.close()


def test_decimal_money_change_is_json_safe_and_persists(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement_response = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-DECIMAL-CHANGE",
            "title": "金额变更前",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
            "estimated_budget": "100000.10",
        },
    )
    assert requirement_response.status_code == 201, requirement_response.text
    requirement_id = requirement_response.json()["id"]
    assert post(client, f"/api/versions/{tree['version_id']}/freeze", csrf, {}).status_code == 200

    created = post(
        client,
        "/api/change-requests",
        csrf,
        {
            "version_id": tree["version_id"],
            "title": "精确调整版本与需求预算",
            "reason": "预算复核后调整",
            "change_type": "budget_update",
            "payload": {
                "version": {"budget": "350000.25"},
                "requirements": [
                    {
                        "action": "update",
                        "requirement_id": requirement_id,
                        "fields": {"estimated_budget": "125000.35"},
                    }
                ],
            },
        },
    )
    assert created.status_code == 201, created.text
    change = created.json()
    assert change["payload"]["version"]["budget"] == "350000.25"
    assert (
        change["payload"]["requirements"][0]["fields"]["estimated_budget"]
        == "125000.35"
    )
    with SessionLocal() as db:
        stored_change = db.get(ChangeRequest, change["id"])
        assert stored_change is not None
        assert stored_change.payload["version"]["budget"] == "350000.25"
        assert (
            stored_change.payload["requirements"][0]["fields"]["estimated_budget"]
            == "125000.35"
        )

    leader_response = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "budgetchangeleader",
            "full_name": "预算变更审批人",
            "role": "leader",
            "initial_password": "BudgetLead@123",
        },
    )
    assert leader_response.status_code == 201, leader_response.text
    with TestClient(app, base_url="http://testserver") as leader_client:
        leader_csrf = login(leader_client, "budgetchangeleader", "BudgetLead@123")
        password_changed = leader_client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": leader_csrf},
            json={
                "current_password": "BudgetLead@123",
                "new_password": "BudgetLead@456",
            },
        )
        assert password_changed.status_code == 200, password_changed.text
        approved = leader_client.patch(
            f"/api/change-requests/{change['id']}",
            headers={"X-CSRF-Token": leader_csrf},
            json={"approved": True, "note": "金额核对无误"},
        )
        assert approved.status_code == 200, approved.text
        applied = leader_client.post(
            f"/api/change-requests/{change['id']}/apply",
            headers={"X-CSRF-Token": leader_csrf},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["status"] == "applied"

    with SessionLocal() as db:
        stored_change = db.get(ChangeRequest, change["id"])
        stored_version = db.get(DeliveryVersion, tree["version_id"])
        stored_requirement = db.get(Requirement, requirement_id)
        assert stored_change is not None and stored_change.status == "applied"
        assert stored_version is not None
        assert stored_version.budget == Decimal("350000.25")
        assert stored_requirement is not None
        assert stored_requirement.estimated_budget == Decimal("125000.35")


def test_approved_change_application_and_secure_upload(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf, second_version=True)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-CHANGE",
            "title": "变更前",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    ).json()
    assert post(client, f"/api/versions/{tree['version_id']}/freeze", csrf, {}).status_code == 200
    change = post(
        client,
        "/api/change-requests",
        csrf,
        {
            "version_id": tree["version_id"],
            "title": "调整版本与需求名称",
            "reason": "客户已确认变更",
            "change_type": "scope_update",
            "payload": {
                "version": {"name": "变更后版本"},
                "requirements": [
                    {"action": "update", "requirement_id": requirement["id"], "fields": {"title": "变更后"}}
                ],
            },
        },
    ).json()
    leader = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "changeleader",
            "full_name": "变更审批人",
            "role": "leader",
            "initial_password": "ChangeLead@123",
        },
    ).json()
    leader_client = TestClient(app, base_url="http://testserver")
    leader_csrf = login(leader_client, "changeleader", "ChangeLead@123")
    assert leader_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": leader_csrf},
        json={"current_password": "ChangeLead@123", "new_password": "ChangeLead@456"},
    ).status_code == 200
    approved = leader_client.patch(
        f"/api/change-requests/{change['id']}",
        headers={"X-CSRF-Token": leader_csrf},
        json={"approved": True, "note": "同意变更"},
    )
    assert approved.status_code == 200
    applied = leader_client.post(
        f"/api/change-requests/{change['id']}/apply",
        headers={"X-CSRF-Token": leader_csrf},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"
    assert applied.json()["baseline"]["sequence"] == 2
    changed_requirement = leader_client.get(f"/api/requirements/{requirement['id']}").json()
    assert changed_requirement["title"] == "变更后"
    leader_client.close()

    uploaded = client.post(
        "/api/artifacts/upload",
        headers={"X-CSRF-Token": csrf},
        data={"project_id": str(tree["project_id"]), "stage": "1", "category": "feasibility", "title": "可研报告"},
        files={"file": ("../../report.pdf", b"%PDF-test", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    downloaded = client.get(f"/api/artifacts/{uploaded.json()['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-test"
    blocked = client.post(
        "/api/artifacts/upload",
        headers={"X-CSRF-Token": csrf},
        data={"project_id": str(tree["project_id"]), "stage": "1", "category": "other", "title": "危险文件"},
        files={"file": ("run.exe", b"MZ", "application/octet-stream")},
    )
    assert blocked.status_code == 400
    assert client.get(f"/api/exports/project-progress.csv?project_id={tree['project_id']}").status_code == 200
    assert client.get(
        f"/api/exports/version-comparison.csv?left_id={tree['version_id']}&right_id={tree['second_version_id']}"
    ).status_code == 200
    assert client.get(f"/api/exports/artifacts.csv?project_id={tree['project_id']}").status_code == 200
    assert client.get(f"/api/exports/operations.csv?project_id={tree['project_id']}").status_code == 200


def test_frozen_version_artifacts_require_structured_changes(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-ARTIFACT-LOCK",
            "title": "成果物锁定需求",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    ).json()
    artifacts: dict[int, dict] = {}
    for stage in (3, 4, 5):
        response = post(
            client,
            "/api/artifacts",
            csrf,
            {
                "project_id": tree["project_id"],
                "annual_plan_id": tree["plan_id"],
                "version_id": tree["version_id"],
                "stage": stage,
                "category": f"stage-{stage}",
                "title": f"阶段 {stage} 冻结前草稿",
            },
        )
        assert response.status_code == 201, response.text
        artifacts[stage] = response.json()
    operation_artifact = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "requirement_id": requirement["id"],
            "stage": 6,
            "category": "operation",
            "title": "冻结前运维草稿",
        },
    )
    assert operation_artifact.status_code == 201, operation_artifact.text
    artifacts[6] = operation_artifact.json()
    submitted_before_freeze = post(
        client,
        f"/api/artifacts/{artifacts[4]['id']}/submit",
        csrf,
        {},
    )
    assert submitted_before_freeze.status_code == 200, submitted_before_freeze.text

    frozen = post(client, f"/api/versions/{tree['version_id']}/freeze", csrf, {})
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["baseline"]["sequence"] == 1
    frozen_artifact_ids = {
        item["id"] for item in frozen.json()["baseline"]["snapshot"]["artifacts"]
    }
    assert frozen_artifact_ids == {item["id"] for item in artifacts.values()}

    for artifact in artifacts.values():
        blocked = post(
            client,
            f"/api/artifacts/{artifact['id']}/submit",
            csrf,
            {},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "VERSION_LOCKED"
    blocked_stage_five = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stage": 5,
            "category": "acceptance",
            "title": "冻结后直接新增验收报告",
        },
    )
    assert blocked_stage_five.status_code == 409
    assert blocked_stage_five.json()["detail"]["code"] == "VERSION_LOCKED"
    blocked_operation = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "requirement_id": requirement["id"],
            "stage": 6,
            "category": "operation",
            "title": "冻结后直接新增运维反馈",
        },
    )
    assert blocked_operation.status_code == 409
    assert blocked_operation.json()["detail"]["code"] == "VERSION_LOCKED"
    blocked_upload = client.post(
        "/api/artifacts/upload",
        headers={"X-CSRF-Token": csrf},
        data={
            "project_id": str(tree["project_id"]),
            "annual_plan_id": str(tree["plan_id"]),
            "version_id": str(tree["version_id"]),
            "stage": "5",
            "category": "acceptance",
            "title": "冻结后直接上传验收报告",
        },
        files={"file": ("acceptance.pdf", b"%PDF-frozen", "application/pdf")},
    )
    assert blocked_upload.status_code == 409, blocked_upload.text
    assert blocked_upload.json()["detail"]["code"] == "VERSION_LOCKED"

    reviewer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "artifactchangeleader",
            "full_name": "成果物变更审批人",
            "role": "leader",
            "initial_password": "ArtifactChange@123",
        },
    )
    assert reviewer.status_code == 201, reviewer.text
    with TestClient(app, base_url="http://testserver") as reviewer_client:
        reviewer_csrf = login(
            reviewer_client, "artifactchangeleader", "ArtifactChange@123"
        )
        changed_password = reviewer_client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={
                "current_password": "ArtifactChange@123",
                "new_password": "ArtifactChange@456",
            },
        )
        assert changed_password.status_code == 200, changed_password.text
        blocked_decision = reviewer_client.patch(
            f"/api/artifacts/{artifacts[4]['id']}/decision",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "不得绕过版本变更"},
        )
        assert blocked_decision.status_code == 409, blocked_decision.text
        assert blocked_decision.json()["detail"]["code"] == "VERSION_LOCKED"

        submitted_change = post(
            client,
            "/api/change-requests",
            csrf,
            {
                "version_id": tree["version_id"],
                "title": "调整冻结版本成果物",
                "reason": "阶段材料复核",
                "change_type": "artifact_update",
                "payload": {
                    "artifacts": [
                        {"action": "submit", "artifact_id": artifacts[3]["id"]},
                        {
                            "action": "update",
                            "artifact_id": artifacts[4]["id"],
                            "fields": {"title": "招投标成果物（已复核）"},
                        },
                        {"action": "remove", "artifact_id": artifacts[5]["id"]},
                        {"action": "submit", "artifact_id": artifacts[6]["id"]},
                        {
                            "action": "add",
                            "data": {
                                "stage": 5,
                                "category": "acceptance",
                                "title": "变更新增验收成果物",
                            },
                        },
                    ]
                },
            },
        )
        assert submitted_change.status_code == 201, submitted_change.text
        change = submitted_change.json()
        assert change["expected_baseline_sequence"] == 1
        approved = reviewer_client.patch(
            f"/api/change-requests/{change['id']}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "同意成果物变更"},
        )
        assert approved.status_code == 200, approved.text
        applied = reviewer_client.post(
            f"/api/change-requests/{change['id']}/apply",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["baseline"]["sequence"] == 2

        decision_change = post(
            client,
            "/api/change-requests",
            csrf,
            {
                "version_id": tree["version_id"],
                "title": "审批冻结版本成果物",
                "reason": "材料内容已核验",
                "change_type": "artifact_approval",
                "payload": {
                    "artifacts": [
                        {
                            "action": "decide",
                            "artifact_id": artifacts[3]["id"],
                            "approved": True,
                            "note": "建设材料通过",
                        },
                        {
                            "action": "decide",
                            "artifact_id": artifacts[4]["id"],
                            "approved": True,
                            "note": "招投标材料通过",
                        },
                        {
                            "action": "decide",
                            "artifact_id": artifacts[6]["id"],
                            "approved": True,
                            "note": "运维材料通过",
                        },
                    ]
                },
            },
        )
        assert decision_change.status_code == 201, decision_change.text
        assert decision_change.json()["expected_baseline_sequence"] == 2
        approved = reviewer_client.patch(
            f"/api/change-requests/{decision_change.json()['id']}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "审批结论有效"},
        )
        assert approved.status_code == 200, approved.text
        applied = reviewer_client.post(
            f"/api/change-requests/{decision_change.json()['id']}/apply",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["baseline"]["sequence"] == 3

    with SessionLocal() as db:
        assert db.get(Deliverable, artifacts[5]["id"]) is None
        assert db.get(Deliverable, artifacts[3]["id"]).approval_status == "approved"
        assert db.get(Deliverable, artifacts[6]["id"]).approval_status == "approved"
        assert db.scalar(
            select(Deliverable).where(Deliverable.title == "变更新增验收成果物")
        ) is not None
        latest_baseline = db.scalar(
            select(VersionBaseline)
            .where(VersionBaseline.version_id == tree["version_id"])
            .order_by(VersionBaseline.sequence.desc())
        )
        assert latest_baseline is not None and latest_baseline.sequence == 3
        assert artifacts[6]["id"] in {
            item["id"] for item in latest_baseline.snapshot["artifacts"]
        }


def test_change_baseline_conflicts_block_overlap_and_stale_apply(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-CONCURRENCY",
            "title": "并发变更前",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    ).json()
    assert post(client, f"/api/versions/{tree['version_id']}/freeze", csrf, {}).status_code == 200
    reviewer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "concurrencyleader",
            "full_name": "并发变更审批人",
            "role": "leader",
            "initial_password": "Concurrency@123",
        },
    )
    assert reviewer.status_code == 201, reviewer.text

    def create_change(title: str, payload: dict) -> dict:
        response = post(
            client,
            "/api/change-requests",
            csrf,
            {
                "version_id": tree["version_id"],
                "title": title,
                "reason": "并发控制测试",
                "change_type": "scope_update",
                "payload": payload,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["expected_baseline_sequence"] == 1
        return response.json()

    first = create_change(
        "第一项需求变更",
        {
            "requirements": [
                {
                    "action": "update",
                    "requirement_id": requirement["id"],
                    "fields": {"title": "第一项变更"},
                }
            ]
        },
    )
    overlapping = create_change(
        "重叠需求变更",
        {
            "requirements": [
                {
                    "action": "update",
                    "requirement_id": requirement["id"],
                    "fields": {"description": "会覆盖同一需求"},
                }
            ]
        },
    )
    independent = create_change(
        "独立版本目标变更",
        {"version": {"target": "独立但基于相同旧基线"}},
    )

    with TestClient(app, base_url="http://testserver") as reviewer_client:
        reviewer_csrf = login(
            reviewer_client, "concurrencyleader", "Concurrency@123"
        )
        assert reviewer_client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={
                "current_password": "Concurrency@123",
                "new_password": "Concurrency@456",
            },
        ).status_code == 200
        approve_first = reviewer_client.patch(
            f"/api/change-requests/{first['id']}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "先执行"},
        )
        assert approve_first.status_code == 200, approve_first.text
        blocked_overlap = reviewer_client.patch(
            f"/api/change-requests/{overlapping['id']}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "不应通过"},
        )
        assert blocked_overlap.status_code == 409, blocked_overlap.text
        assert blocked_overlap.json()["detail"]["code"] == "CHANGE_OVERLAP"
        approve_independent = reviewer_client.patch(
            f"/api/change-requests/{independent['id']}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "字段不重叠"},
        )
        assert approve_independent.status_code == 200, approve_independent.text

        applied = reviewer_client.post(
            f"/api/change-requests/{first['id']}/apply",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["baseline"]["sequence"] == 2
        stale_apply = reviewer_client.post(
            f"/api/change-requests/{independent['id']}/apply",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert stale_apply.status_code == 409, stale_apply.text
        assert stale_apply.json()["detail"]["code"] == "CHANGE_BASELINE_STALE"
        stale_approval = reviewer_client.patch(
            f"/api/change-requests/{overlapping['id']}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "旧基线不能再审批"},
        )
        assert stale_approval.status_code == 409, stale_approval.text
        assert stale_approval.json()["detail"]["code"] == "CHANGE_BASELINE_STALE"

    changes = client.get(
        f"/api/change-requests?version_id={tree['version_id']}"
    )
    assert changes.status_code == 200, changes.text
    assert {item["expected_baseline_sequence"] for item in changes.json()} == {1}


def test_artifact_writes_recheck_version_state_under_freeze_race(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    original = client.post(
        "/api/artifacts/upload",
        headers={"X-CSRF-Token": csrf},
        data={
            "project_id": str(tree["project_id"]),
            "annual_plan_id": str(tree["plan_id"]),
            "version_id": str(tree["version_id"]),
            "stage": "3",
            "category": "construction",
            "title": "冻结竞态前成果物",
        },
        files={"file": ("before-freeze.pdf", b"%PDF-before-freeze", "application/pdf")},
    )
    assert original.status_code == 201, original.text
    artifact_id = original.json()["id"]
    files_before = {item for item in TEST_UPLOADS.rglob("*") if item.is_file()}

    original_lock = support_api.lock_version_scope
    lock_calls: list[int] = []

    def freeze_after_initial_read(db, user, version_id):
        version, plan = original_lock(db, user, version_id)
        version.status = "frozen"
        lock_calls.append(version_id)
        return version, plan

    monkeypatch.setattr(support_api, "lock_version_scope", freeze_after_initial_read)
    responses = [
        post(
            client,
            "/api/artifacts",
            csrf,
            {
                "project_id": tree["project_id"],
                "annual_plan_id": tree["plan_id"],
                "version_id": tree["version_id"],
                "stage": 3,
                "category": "construction",
                "title": "不应越过冻结的新成果物",
            },
        ),
        client.post(
            "/api/artifacts/upload",
            headers={"X-CSRF-Token": csrf},
            data={
                "project_id": str(tree["project_id"]),
                "annual_plan_id": str(tree["plan_id"]),
                "version_id": str(tree["version_id"]),
                "stage": "3",
                "category": "construction",
                "title": "不应越过冻结的附件",
            },
            files={"file": ("blocked.pdf", b"%PDF-blocked", "application/pdf")},
        ),
        post(client, f"/api/artifacts/{artifact_id}/submit", csrf, {}),
        client.patch(
            f"/api/artifacts/{artifact_id}/decision",
            headers={"X-CSRF-Token": csrf},
            json={"approved": True, "note": "不应执行"},
        ),
        client.delete(
            f"/api/artifacts/{artifact_id}",
            headers={"X-CSRF-Token": csrf},
        ),
    ]
    for response in responses:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "VERSION_LOCKED"
    assert lock_calls == [tree["version_id"]] * len(responses)
    assert {item for item in TEST_UPLOADS.rglob("*") if item.is_file()} == files_before
    with SessionLocal() as db:
        version = db.get(DeliveryVersion, tree["version_id"])
        artifact = db.get(Deliverable, artifact_id)
        assert version is not None and version.status == "draft"
        assert artifact is not None and artifact.approval_status == "draft"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing", "CHANGE_UPLOAD_FILE_MISSING"),
        ("size", "CHANGE_UPLOAD_FILE_CORRUPT"),
        ("digest", "CHANGE_UPLOAD_FILE_CORRUPT"),
    ],
)
def test_artifact_change_apply_rejects_missing_or_corrupt_staged_file(
    client: TestClient, mutation: str, expected_code: str
):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    assert post(
        client, f"/api/versions/{tree['version_id']}/freeze", csrf, {}
    ).status_code == 200
    reviewer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "integrityleader",
            "full_name": "附件完整性审批人",
            "role": "leader",
            "initial_password": "Integrity@123",
        },
    )
    assert reviewer.status_code == 201, reviewer.text

    content = b"%PDF-integrity-check"
    uploaded = client.post(
        f"/api/versions/{tree['version_id']}/artifact-change-requests/upload",
        headers={"X-CSRF-Token": csrf},
        data={
            "change_title": "附件完整性验证",
            "reason": "损坏文件不能写入正式成果物和基线",
            "artifact_title": "完整性验证成果物",
            "stage": "5",
            "category": "acceptance",
        },
        files={"file": ("integrity.pdf", content, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    token = body["staged_artifact"]["token"]
    change_id = body["change_request"]["id"]
    expected_digest = hashlib.sha256(content).hexdigest()
    assert body["staged_artifact"]["sha256_hex"] == expected_digest
    with SessionLocal() as db:
        staged = db.scalar(
            select(ArtifactChangeUpload).where(ArtifactChangeUpload.token == token)
        )
        assert staged is not None and staged.sha256_hex == expected_digest
        staged_path = TEST_UPLOADS / staged.storage_key
    if mutation == "missing":
        staged_path.unlink()
    elif mutation == "size":
        staged_path.write_bytes(content + b"-changed")
    else:
        staged_path.write_bytes(b"X" * len(content))

    with TestClient(app, base_url="http://testserver") as reviewer_client:
        reviewer_csrf = login(
            reviewer_client, "integrityleader", "Integrity@123"
        )
        changed = reviewer_client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={
                "current_password": "Integrity@123",
                "new_password": "Integrity@456",
            },
        )
        assert changed.status_code == 200, changed.text
        approved = reviewer_client.patch(
            f"/api/change-requests/{change_id}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "进入执行完整性校验"},
        )
        assert approved.status_code == 200, approved.text
        applied = reviewer_client.post(
            f"/api/change-requests/{change_id}/apply",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert applied.status_code == 409, applied.text
        assert applied.json()["detail"]["code"] == expected_code
        baselines = reviewer_client.get(
            f"/api/versions/{tree['version_id']}/baselines"
        )
        assert baselines.status_code == 200
        assert [item["sequence"] for item in baselines.json()] == [1]
        artifacts = reviewer_client.get(
            f"/api/artifacts?version_id={tree['version_id']}"
        )
        assert artifacts.status_code == 200
        assert "完整性验证成果物" not in {
            item["title"] for item in artifacts.json()
        }
    with SessionLocal() as db:
        change = db.get(ChangeRequest, change_id)
        staged = db.scalar(
            select(ArtifactChangeUpload).where(ArtifactChangeUpload.token == token)
        )
        assert change is not None and change.status == "approved"
        assert staged is not None


def test_frozen_artifact_file_change_add_replace_reject_and_cancel(
    client: TestClient,
):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    initial_upload = client.post(
        "/api/artifacts/upload",
        headers={"X-CSRF-Token": csrf},
        data={
            "project_id": str(tree["project_id"]),
            "annual_plan_id": str(tree["plan_id"]),
            "version_id": str(tree["version_id"]),
            "stage": "3",
            "category": "construction",
            "title": "待替换任务书",
        },
        files={"file": ("old.pdf", b"%PDF-old", "application/pdf")},
    )
    assert initial_upload.status_code == 201, initial_upload.text
    original_artifact = initial_upload.json()
    with SessionLocal() as db:
        stored_original = db.get(Deliverable, original_artifact["id"])
        assert stored_original is not None and stored_original.storage_key
        old_path = TEST_UPLOADS / stored_original.storage_key
        assert old_path.read_bytes() == b"%PDF-old"
    assert post(
        client, f"/api/versions/{tree['version_id']}/freeze", csrf, {}
    ).status_code == 200

    reviewer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "filechangeleader",
            "full_name": "附件变更审批人",
            "role": "leader",
            "initial_password": "FileChange@123",
        },
    )
    assert reviewer.status_code == 201, reviewer.text
    customer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "filechangecustomer",
            "full_name": "附件变更客户",
            "role": "customer",
            "initial_password": "FileCustomer@123",
            "project_ids": [tree["project_id"]],
        },
    )
    assert customer.status_code == 201, customer.text

    add_upload = client.post(
        f"/api/versions/{tree['version_id']}/artifact-change-requests/upload",
        headers={"X-CSRF-Token": csrf},
        data={
            "change_title": "新增冻结版本验收报告",
            "reason": "补齐验收材料",
            "artifact_title": "最终验收报告",
            "stage": "5",
            "category": "acceptance",
        },
        files={"file": ("acceptance.pdf", b"%PDF-acceptance", "application/pdf")},
    )
    assert add_upload.status_code == 201, add_upload.text
    add_body = add_upload.json()
    add_change = add_body["change_request"]
    staged = add_body["staged_artifact"]
    assert add_change["status"] == "pending"
    assert add_change["expected_baseline_sequence"] == 1
    assert staged["original_filename"] == "acceptance.pdf"
    assert staged["size_bytes"] == len(b"%PDF-acceptance")
    assert staged["sha256_hex"] == hashlib.sha256(b"%PDF-acceptance").hexdigest()
    assert client.get(
        f"/api/artifact-change-uploads/{staged['token']}/download"
    ).content == b"%PDF-acceptance"
    artifact_list = client.get(
        f"/api/artifacts?version_id={tree['version_id']}"
    )
    assert artifact_list.status_code == 200
    assert "最终验收报告" not in {item["title"] for item in artifact_list.json()}
    baselines = client.get(f"/api/versions/{tree['version_id']}/baselines")
    assert baselines.status_code == 200
    assert "最终验收报告" not in {
        item["title"] for item in baselines.json()[0]["snapshot"]["artifacts"]
    }
    listed_changes = client.get(
        f"/api/change-requests?version_id={tree['version_id']}"
    )
    assert listed_changes.status_code == 200
    listed_add = next(
        item for item in listed_changes.json() if item["id"] == add_change["id"]
    )
    assert listed_add["staged_artifacts"][0]["token"] == staged["token"]

    with TestClient(app, base_url="http://testserver") as customer_client:
        customer_csrf = login(
            customer_client, "filechangecustomer", "FileCustomer@123"
        )
        assert customer_client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": customer_csrf},
            json={
                "current_password": "FileCustomer@123",
                "new_password": "FileCustomer@456",
            },
        ).status_code == 200
        hidden_preview = customer_client.get(
            f"/api/artifact-change-uploads/{staged['token']}/download"
        )
        assert hidden_preview.status_code == 403
        customer_artifacts = customer_client.get(
            f"/api/artifacts?project_id={tree['project_id']}"
        )
        assert customer_artifacts.status_code == 200
        assert "最终验收报告" not in {
            item["title"] for item in customer_artifacts.json()
        }

    with TestClient(app, base_url="http://testserver") as reviewer_client:
        reviewer_csrf = login(
            reviewer_client, "filechangeleader", "FileChange@123"
        )
        assert reviewer_client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={
                "current_password": "FileChange@123",
                "new_password": "FileChange@456",
            },
        ).status_code == 200
        approved = reviewer_client.patch(
            f"/api/change-requests/{add_change['id']}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "附件已预览，同意纳入基线"},
        )
        assert approved.status_code == 200, approved.text
        applied = reviewer_client.post(
            f"/api/change-requests/{add_change['id']}/apply",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["baseline"]["sequence"] == 2
        assert reviewer_client.get(
            f"/api/artifact-change-uploads/{staged['token']}/download"
        ).status_code == 404

        active_artifacts = reviewer_client.get(
            f"/api/artifacts?version_id={tree['version_id']}"
        ).json()
        added_artifact = next(
            item for item in active_artifacts if item["title"] == "最终验收报告"
        )
        assert added_artifact["approval_status"] == "approved"
        downloaded = reviewer_client.get(
            f"/api/artifacts/{added_artifact['id']}/download"
        )
        assert downloaded.status_code == 200
        assert downloaded.content == b"%PDF-acceptance"

        replacement_upload = client.post(
            f"/api/versions/{tree['version_id']}/artifact-change-requests/upload",
            headers={"X-CSRF-Token": csrf},
            data={
                "change_title": "替换冻结版本任务书附件",
                "reason": "任务书盖章版替换",
                "artifact_id": str(original_artifact["id"]),
            },
            files={"file": ("signed.pdf", b"%PDF-signed", "application/pdf")},
        )
        assert replacement_upload.status_code == 201, replacement_upload.text
        replacement = replacement_upload.json()
        assert replacement["change_request"]["expected_baseline_sequence"] == 2
        assert reviewer_client.patch(
            f"/api/change-requests/{replacement['change_request']['id']}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "盖章版有效"},
        ).status_code == 200
        replacement_applied = reviewer_client.post(
            f"/api/change-requests/{replacement['change_request']['id']}/apply",
            headers={"X-CSRF-Token": reviewer_csrf},
        )
        assert replacement_applied.status_code == 200, replacement_applied.text
        assert replacement_applied.json()["baseline"]["sequence"] == 3
        replaced_file = reviewer_client.get(
            f"/api/artifacts/{original_artifact['id']}/download"
        )
        assert replaced_file.status_code == 200
        assert replaced_file.content == b"%PDF-signed"
        assert not old_path.exists()

        rejected_upload = client.post(
            f"/api/versions/{tree['version_id']}/artifact-change-requests/upload",
            headers={"X-CSRF-Token": csrf},
            data={
                "change_title": "应驳回附件",
                "reason": "验证驳回清理",
                "artifact_title": "不生效成果物",
                "stage": "5",
                "category": "acceptance",
            },
            files={"file": ("reject.pdf", b"%PDF-reject", "application/pdf")},
        ).json()
        reject_token = rejected_upload["staged_artifact"]["token"]
        with SessionLocal() as db:
            reject_row = db.scalar(
                select(ArtifactChangeUpload).where(
                    ArtifactChangeUpload.token == reject_token
                )
            )
            assert reject_row is not None
            reject_path = TEST_UPLOADS / reject_row.storage_key
            assert reject_path.exists()
        rejected = reviewer_client.patch(
            f"/api/change-requests/{rejected_upload['change_request']['id']}",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": False, "note": "材料无效"},
        )
        assert rejected.status_code == 200, rejected.text
        assert not reject_path.exists()
        assert reviewer_client.get(
            f"/api/artifact-change-uploads/{reject_token}/download"
        ).status_code == 404

    cancelled_upload = client.post(
        f"/api/versions/{tree['version_id']}/artifact-change-requests/upload",
        headers={"X-CSRF-Token": csrf},
        data={
            "change_title": "应取消附件",
            "reason": "验证取消清理",
            "artifact_title": "取消成果物",
            "stage": "5",
            "category": "acceptance",
        },
        files={"file": ("cancel.pdf", b"%PDF-cancel", "application/pdf")},
    ).json()
    cancel_token = cancelled_upload["staged_artifact"]["token"]
    with SessionLocal() as db:
        cancel_row = db.scalar(
            select(ArtifactChangeUpload).where(
                ArtifactChangeUpload.token == cancel_token
            )
        )
        assert cancel_row is not None
        cancel_path = TEST_UPLOADS / cancel_row.storage_key
        assert cancel_path.exists()
    cancelled = post(
        client,
        f"/api/change-requests/{cancelled_upload['change_request']['id']}/cancel",
        csrf,
        {},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert not cancel_path.exists()
    assert client.get(
        f"/api/artifact-change-uploads/{cancel_token}/download"
    ).status_code == 404
    final_artifacts = client.get(
        f"/api/artifacts?version_id={tree['version_id']}"
    ).json()
    assert "不生效成果物" not in {item["title"] for item in final_artifacts}
    assert "取消成果物" not in {item["title"] for item in final_artifacts}


def test_planning_pool_stable_key_compare_and_source_cycle(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf, second_version=True)
    pending = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-STABLE-V1",
            "title": "跨版本原始需求",
            "project_id": tree["project_id"],
            "stakeholder_role": "manager",
        },
    )
    assert pending.status_code == 201, pending.text
    assert pending.json()["annual_plan_id"] is None
    assert pending.json()["version_id"] is None
    assert pending.json()["stable_key"] == "REQ-STABLE-V1"
    assigned = client.patch(
        f"/api/requirements/{pending.json()['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"version_id": tree["version_id"]},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["annual_plan_id"] == tree["plan_id"]

    second = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-STABLE-V2",
            "stable_key": "REQ-STABLE-V1",
            "title": "跨版本修改后需求",
            "project_id": tree["project_id"],
            "version_id": tree["second_version_id"],
            "stakeholder_role": "manager",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["annual_plan_id"] == tree["second_plan_id"]
    duplicate = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-STABLE-DUP",
            "stable_key": "REQ-STABLE-V1",
            "title": "重复稳定标识",
            "project_id": tree["project_id"],
            "version_id": tree["second_version_id"],
            "stakeholder_role": "manager",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "STABLE_KEY_EXISTS"

    comparison = client.get(
        f"/api/versions/compare?left_id={tree['version_id']}&right_id={tree['second_version_id']}"
    )
    assert comparison.status_code == 200, comparison.text
    differences = comparison.json()["requirements"]
    assert not differences["added"] and not differences["removed"]
    assert differences["changed"][0]["stable_key"] == "REQ-STABLE-V1"
    assert "code" not in differences["changed"][0]["fields"]
    assert "title" in differences["changed"][0]["fields"]

    source = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-CYCLE-A",
            "title": "循环 A",
            "project_id": tree["project_id"],
            "stakeholder_role": "manager",
        },
    ).json()
    child = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-CYCLE-B",
            "title": "循环 B",
            "project_id": tree["project_id"],
            "stakeholder_role": "manager",
            "source_requirement_id": source["id"],
        },
    ).json()
    cycle = client.patch(
        f"/api/requirements/{source['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"source_requirement_id": child["id"]},
    )
    assert cycle.status_code == 400
    assert cycle.json()["detail"]["code"] == "SOURCE_REQUIREMENT_CYCLE"


def test_requirement_field_rbac_and_developer_transition_ownership(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-RBAC",
            "title": "权限需求",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    ).json()
    for status in ("planning", "scheduled"):
        assert post(
            client,
            f"/api/requirements/{requirement['id']}/transition",
            csrf,
            {"status": status, "note": "管理员推进"},
        ).status_code == 200

    role_clients: dict[str, tuple[TestClient, str, int]] = {}
    for role in ("sales", "developer", "operator", "customer"):
        created = post(
            client,
            "/api/users",
            csrf,
            {
                "username": f"rbac_{role}",
                "full_name": role,
                "role": role,
                "initial_password": f"{role.title()}@1234",
                "project_ids": [tree["project_id"]] if role == "customer" else [],
            },
        ).json()
        role_client = TestClient(app, base_url="http://testserver")
        role_csrf = login(role_client, f"rbac_{role}", f"{role.title()}@1234")
        assert role_client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": role_csrf},
            json={"current_password": f"{role.title()}@1234", "new_password": f"{role.title()}@5678"},
        ).status_code == 200
        role_clients[role] = (role_client, role_csrf, created["id"])

    for role in ("developer", "operator", "customer"):
        role_client, role_csrf, _ = role_clients[role]
        forbidden_money = post(
            role_client,
            "/api/requirements",
            role_csrf,
            {
                "code": f"REQ-MONEY-{role.upper()}",
                "title": "无金额写权限需求",
                "project_id": tree["project_id"],
                "stakeholder_role": role,
                "estimated_budget": "1",
            },
        )
        assert forbidden_money.status_code == 403, (role, forbidden_money.text)
        assert forbidden_money.json()["detail"]["code"] == "FINANCE_FIELD_FORBIDDEN"

    for role in ("sales", "developer", "operator", "customer"):
        role_client, role_csrf, _ = role_clients[role]
        blocked = role_client.patch(
            f"/api/requirements/{requirement['id']}",
            headers={"X-CSRF-Token": role_csrf},
            json={"priority": "urgent"},
        )
        assert blocked.status_code == 403, (role, blocked.text)

    dev_client, dev_csrf, _ = role_clients["developer"]
    unowned = post(
        dev_client,
        f"/api/requirements/{requirement['id']}/transition",
        dev_csrf,
        {"status": "developing", "note": "未领取直接推进"},
    )
    assert unowned.status_code == 403
    assert post(dev_client, f"/api/requirements/{requirement['id']}/claim", dev_csrf, {}).status_code == 200
    owned = post(
        dev_client,
        f"/api/requirements/{requirement['id']}/transition",
        dev_csrf,
        {"status": "developing", "note": "领取后推进"},
    )
    assert owned.status_code == 200, owned.text

    sales_client, sales_csrf, _ = role_clients["sales"]
    own_sales = post(
        sales_client,
        "/api/requirements",
        sales_csrf,
        {
            "code": "REQ-SALES-OWN",
            "title": "销售自提需求",
            "project_id": tree["project_id"],
            "stakeholder_role": "sales",
            "estimated_budget": "25",
        },
    ).json()
    assert own_sales["estimated_budget"] == "25.00"
    assert sales_client.patch(
        f"/api/requirements/{own_sales['id']}",
        headers={"X-CSRF-Token": sales_csrf},
        json={"description": "补充业务说明"},
    ).status_code == 200
    assert sales_client.patch(
        f"/api/requirements/{own_sales['id']}",
        headers={"X-CSRF-Token": sales_csrf},
        json={"version_id": tree["version_id"]},
    ).status_code == 403

    with SessionLocal() as db:
        denied_actor_ids = set(
            db.scalars(
                select(AuditLog.actor_id).where(AuditLog.action == "permission_denied")
            )
        )
    assert {item[2] for item in role_clients.values()} <= denied_actor_ids

    for role_client, _, _ in role_clients.values():
        role_client.close()


def test_funding_draft_edit_artifact_filters_lock_and_operation_type(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    sales = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "draftsales",
            "full_name": "申报编辑人",
            "role": "sales",
            "initial_password": "DraftSales@123",
        },
    ).json()
    sales_client = TestClient(app, base_url="http://testserver")
    sales_csrf = login(sales_client, "draftsales", "DraftSales@123")
    assert sales_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": sales_csrf},
        json={"current_password": "DraftSales@123", "new_password": "DraftSales@456"},
    ).status_code == 200
    application = post(
        sales_client,
        "/api/funds/applications",
        sales_csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "title": "待编辑申报",
            "amount": "100",
        },
    ).json()
    assert application["applicant_name"] == "申报编辑人"
    listed = sales_client.get(f"/api/funds/applications?project_id={tree['project_id']}")
    assert listed.status_code == 200
    assert next(item for item in listed.json() if item["id"] == application["id"])["applicant_name"] == "申报编辑人"
    edited = sales_client.patch(
        f"/api/funds/applications/{application['id']}",
        headers={"X-CSRF-Token": sales_csrf},
        json={"title": "已编辑申报", "amount": "120", "version_id": tree["version_id"], "note": "补充依据"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["amount"] == "120.00"
    assert edited.json()["version_id"] == tree["version_id"]
    assert edited.json()["applicant_name"] == "申报编辑人"
    forbidden = client.patch(
        f"/api/funds/applications/{application['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"title": "管理员不能代改"},
    )
    assert forbidden.status_code == 403
    assert sales_client.patch(
        f"/api/funds/applications/{application['id']}/status",
        headers={"X-CSRF-Token": sales_csrf},
        json={"status": "submitted"},
    ).status_code == 200
    locked = sales_client.patch(
        f"/api/funds/applications/{application['id']}",
        headers={"X-CSRF-Token": sales_csrf},
        json={"note": "提交后不可修改"},
    )
    assert locked.status_code == 409

    artifact = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stage": 3,
            "category": "task_book",
            "title": "版本任务书",
        },
    ).json()
    filtered = client.get(
        f"/api/artifacts?annual_plan_id={tree['plan_id']}&version_id={tree['version_id']}&stage=3"
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [artifact["id"]]
    assert post(client, f"/api/versions/{tree['version_id']}/freeze", csrf, {}).status_code == 200
    delete_locked = client.delete(
        f"/api/artifacts/{artifact['id']}", headers={"X-CSRF-Token": csrf}
    )
    assert delete_locked.status_code == 409

    operation = post(
        client,
        "/api/operations",
        csrf,
        {
            "project_id": tree["project_id"],
            "title": "新增功能建议",
            "content": "建议补充批量能力",
            "feedback_type": "improvement",
        },
    )
    assert operation.status_code == 201
    assert operation.json()["feedback_type"] == "improvement"
    sales_client.close()


def test_csv_formula_safety_zero_adjustment_and_health_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    assert client.patch(
        f"/api/projects/{tree['project_id']}",
        headers={"X-CSRF-Token": csrf},
        json={"name": "=HYPERLINK(\"https://invalid\")"},
    ).status_code == 200
    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-CSV",
            "title": "@SUM(1+1)",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    ).json()
    exported = client.get(f"/api/exports/project-progress.csv?project_id={tree['project_id']}")
    text = exported.content.decode("utf-8-sig")
    assert "'=HYPERLINK" in text
    assert "'@SUM" in text
    for value in ("=1+1", "+cmd", "-cmd", "@sum", "  =trimmed"):
        assert str(csv_safe_cell(value)).startswith("'")

    zero_adjustment = post(
        client,
        "/api/funds/entries",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "requirement_id": requirement["id"],
            "entry_type": "adjustment",
            "amount": "0",
        },
    )
    assert zero_adjustment.status_code == 400

    def broken_connect():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(engine, "connect", broken_connect)
    health = client.get("/health")
    assert health.status_code == 503
    assert health.json()["status"] == "degraded"


def test_annual_target_artifact_approval_and_acceptance_milestone(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf)
    updated_plan = client.patch(
        f"/api/plans/{tree['plan_id']}",
        headers={"X-CSRF-Token": csrf},
        json={"target": "完成核心流程上线并通过验收"},
    )
    assert updated_plan.status_code == 200, updated_plan.text
    assert updated_plan.json()["target"] == "完成核心流程上线并通过验收"
    plans = client.get(f"/api/plans?project_id={tree['project_id']}").json()
    assert plans[0]["target"] == "完成核心流程上线并通过验收"

    requirement = post(
        client,
        "/api/requirements",
        csrf,
        {
            "code": "REQ-ACCEPTANCE",
            "title": "验收审批闭环",
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stakeholder_role": "manager",
        },
    ).json()
    for status in ("planning", "scheduled", "developing", "acceptance"):
        transitioned = post(
            client,
            f"/api/requirements/{requirement['id']}/transition",
            csrf,
            {"status": status, "note": f"推进到 {status}"},
        )
        assert transitioned.status_code == 200, transitioned.text

    artifact = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stage": 5,
            "category": "acceptance_report",
            "title": "项目验收报告",
        },
    ).json()
    assert artifact["approval_status"] == "draft"
    reminders = client.get(f"/api/milestones?project_id={tree['project_id']}").json()["reminders"]
    assert any(item["type"] == "acceptance_artifact_required" for item in reminders)

    submitted = client.post(
        f"/api/artifacts/{artifact['id']}/submit",
        headers={"X-CSRF-Token": csrf},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["approval_status"] == "submitted"
    self_approval = client.patch(
        f"/api/artifacts/{artifact['id']}/decision",
        headers={"X-CSRF-Token": csrf},
        json={"approved": True, "note": "不能自审"},
    )
    assert self_approval.status_code == 409
    assert self_approval.json()["detail"]["code"] == "ARTIFACT_SELF_APPROVAL"

    reviewer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "artifact_reviewer",
            "full_name": "成果物审批人",
            "role": "manager",
            "initial_password": "Artifact@123",
        },
    ).json()
    reviewer_client = TestClient(app, base_url="http://testserver")
    reviewer_csrf = login(reviewer_client, "artifact_reviewer", "Artifact@123")
    assert reviewer_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": reviewer_csrf},
        json={"current_password": "Artifact@123", "new_password": "Artifact@456"},
    ).status_code == 200
    approved = reviewer_client.patch(
        f"/api/artifacts/{artifact['id']}/decision",
        headers={"X-CSRF-Token": reviewer_csrf},
        json={"approved": True, "note": "验收材料完整"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "approved"
    assert approved.json()["reviewed_by"] == reviewer["id"]
    assert approved.json()["review_note"] == "验收材料完整"
    reviewer_client.close()

    reminders = client.get(f"/api/milestones?project_id={tree['project_id']}").json()["reminders"]
    assert not any(item["type"] == "acceptance_artifact_required" for item in reminders)
    exported = client.get(f"/api/exports/artifacts.csv?project_id={tree['project_id']}")
    assert "approved" in exported.content.decode("utf-8-sig")
    with SessionLocal() as db:
        actions = set(
            db.scalars(
                select(AuditLog.action).where(
                    AuditLog.entity_type == "artifact",
                    AuditLog.entity_id == str(artifact["id"]),
                )
            )
        )
    assert {"create", "submit", "approve"} <= actions


def test_role_dashboard_layout_persistence_validation_and_rbac(client: TestClient):
    csrf = ready_admin(client)
    all_components = [
        "metrics",
        "status_distribution",
        "budget_distribution",
        "recent_requirements",
        "tasks",
    ]
    current = client.get("/api/dashboard-layout")
    assert current.status_code == 200
    assert current.json() == {
        "role": "admin",
        "component_keys": all_components,
        "updated_by": None,
        "updated_at": None,
        "is_custom": False,
    }

    layouts = client.get("/api/dashboard-layouts")
    assert layouts.status_code == 200
    defaults = {item["role"]: item["component_keys"] for item in layouts.json()}
    assert defaults == {
        "admin": all_components,
        "customer": ["metrics", "recent_requirements", "tasks"],
        "sales": ["metrics", "budget_distribution", "recent_requirements", "tasks"],
        "manager": all_components,
        "developer": ["metrics", "status_distribution", "tasks", "recent_requirements"],
        "operator": ["metrics", "tasks", "status_distribution"],
        "leader": all_components,
    }

    custom_keys = ["tasks", "recent_requirements", "metrics"]
    saved = client.patch(
        "/api/dashboard-layouts/developer",
        headers={"X-CSRF-Token": csrf},
        json={"component_keys": custom_keys},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["component_keys"] == custom_keys
    assert saved.json()["updated_by"] == 1
    assert saved.json()["updated_at"]
    assert saved.json()["is_custom"] is True

    invalid_role = client.patch(
        "/api/dashboard-layouts/unknown",
        headers={"X-CSRF-Token": csrf},
        json={"component_keys": ["metrics"]},
    )
    assert invalid_role.status_code == 400
    assert invalid_role.json()["detail"]["code"] == "DASHBOARD_ROLE_INVALID"
    for component_keys in (
        [],
        ["metrics", "metrics"],
        ["metrics", "unknown_component"],
    ):
        invalid_keys = client.patch(
            "/api/dashboard-layouts/customer",
            headers={"X-CSRF-Token": csrf},
            json={"component_keys": component_keys},
        )
        assert invalid_keys.status_code == 422

    developer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "layout_developer",
            "full_name": "布局研发",
            "role": "developer",
            "initial_password": "LayoutDev@123",
        },
    ).json()
    developer_client = TestClient(app, base_url="http://testserver")
    developer_csrf = login(developer_client, "layout_developer", "LayoutDev@123")
    assert developer_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": developer_csrf},
        json={"current_password": "LayoutDev@123", "new_password": "LayoutDev@456"},
    ).status_code == 200
    own_layout = developer_client.get("/api/dashboard-layout")
    assert own_layout.status_code == 200
    assert own_layout.json()["role"] == "developer"
    assert own_layout.json()["component_keys"] == custom_keys
    assert own_layout.json()["updated_by"] == 1
    assert developer_client.get("/api/dashboard-layouts").status_code == 403
    forbidden_update = developer_client.patch(
        "/api/dashboard-layouts/developer",
        headers={"X-CSRF-Token": developer_csrf},
        json={"component_keys": ["metrics"]},
    )
    assert forbidden_update.status_code == 403
    developer_client.close()
    assert developer["role"] == "developer"

    refreshed = {
        item["role"]: item for item in client.get("/api/dashboard-layouts").json()
    }
    assert refreshed["developer"]["component_keys"] == custom_keys
    with SessionLocal() as db:
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "update",
                AuditLog.entity_type == "dashboard_layout",
                AuditLog.entity_id == "developer",
            )
        )
    assert audit is not None
    assert audit.after_data["component_keys"] == custom_keys


def test_customer_requirement_surfaces_are_requester_scoped(client: TestClient):
    csrf = ready_admin(client)
    tree = make_project_tree(client, csrf, second_version=True)
    internal_requirements = []
    for suffix, plan_id, version_id in (
        ("A", tree["plan_id"], tree["version_id"]),
        ("B", tree["second_plan_id"], tree["second_version_id"]),
    ):
        internal_requirements.append(
            post(
                client,
                "/api/requirements",
                csrf,
                {
                    "code": f"REQ-INTERNAL-{suffix}",
                    "stable_key": "INTERNAL-SHARED",
                    "title": f"内部隔离机密 {suffix}",
                    "project_id": tree["project_id"],
                    "annual_plan_id": plan_id,
                    "version_id": version_id,
                    "stakeholder_role": "manager",
                },
            ).json()
        )

    customer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "scope_customer",
            "full_name": "隔离客户",
            "role": "customer",
            "initial_password": "Customer@789",
            "project_ids": [tree["project_id"]],
        },
    ).json()
    customer_client = TestClient(app, base_url="http://testserver")
    customer_csrf = login(customer_client, "scope_customer", "Customer@789")
    assert customer_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": customer_csrf},
        json={"current_password": "Customer@789", "new_password": "Customer@987"},
    ).status_code == 200

    own_requirements = []
    for suffix, plan_id, version_id in (
        ("A", tree["plan_id"], tree["version_id"]),
        ("B", tree["second_plan_id"], tree["second_version_id"]),
    ):
        created = post(
            customer_client,
            "/api/requirements",
            customer_csrf,
            {
                "code": f"REQ-CUSTOMER-{suffix}",
                "title": f"客户隔离可见 {suffix}",
                "project_id": tree["project_id"],
                "annual_plan_id": plan_id,
                "version_id": version_id,
                "stakeholder_role": "customer",
            },
        )
        assert created.status_code == 201, created.text
        own_requirements.append(created.json())

    internal_artifact = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "requirement_id": internal_requirements[0]["id"],
            "stage": 6,
            "category": "operation_feedback",
            "title": "@内部支持成果",
        },
    ).json()
    own_artifact = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "requirement_id": own_requirements[0]["id"],
            "stage": 6,
            "category": "operation_feedback",
            "title": "@客户可见支持成果",
        },
    ).json()
    shared_artifact = post(
        client,
        "/api/artifacts",
        csrf,
        {
            "project_id": tree["project_id"],
            "annual_plan_id": tree["plan_id"],
            "version_id": tree["version_id"],
            "stage": 4,
            "category": "bid_document",
            "title": "@内部招投标支持材料",
        },
    ).json()
    assert customer_client.get(
        f"/api/artifacts?project_id={tree['project_id']}"
    ).json() == []
    draft_artifact = customer_client.get(
        f"/api/artifacts/{own_artifact['id']}/download"
    )
    assert draft_artifact.status_code == 403
    assert draft_artifact.json()["detail"]["code"] == "ARTIFACT_NOT_APPROVED"

    for artifact in (internal_artifact, own_artifact, shared_artifact):
        submitted = client.post(
            f"/api/artifacts/{artifact['id']}/submit",
            headers={"X-CSRF-Token": csrf},
        )
        assert submitted.status_code == 200, submitted.text
    reviewer = post(
        client,
        "/api/users",
        csrf,
        {
            "username": "scope_reviewer",
            "full_name": "隔离成果审批人",
            "role": "manager",
            "initial_password": "ScopeReview@123",
        },
    ).json()
    reviewer_client = TestClient(app, base_url="http://testserver")
    reviewer_csrf = login(reviewer_client, "scope_reviewer", "ScopeReview@123")
    assert reviewer_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": reviewer_csrf},
        json={"current_password": "ScopeReview@123", "new_password": "ScopeReview@456"},
    ).status_code == 200
    for artifact in (internal_artifact, own_artifact, shared_artifact):
        approved = reviewer_client.patch(
            f"/api/artifacts/{artifact['id']}/decision",
            headers={"X-CSRF-Token": reviewer_csrf},
            json={"approved": True, "note": "审批通过"},
        )
        assert approved.status_code == 200, approved.text
    reviewer_client.close()
    assert reviewer["role"] == "manager"

    internal_operation = post(
        client,
        "/api/operations",
        csrf,
        {
            "project_id": tree["project_id"],
            "version_id": tree["version_id"],
            "requirement_id": internal_requirements[0]["id"],
            "title": "=内部支持运营",
            "content": "内部处理记录",
            "feedback_type": "issue",
        },
    ).json()
    own_operation = post(
        customer_client,
        "/api/operations",
        customer_csrf,
        {
            "project_id": tree["project_id"],
            "version_id": tree["version_id"],
            "requirement_id": own_requirements[0]["id"],
            "title": "=客户可见支持运营",
            "content": "客户提交记录",
            "feedback_type": "question",
        },
    ).json()

    for version_id in (tree["version_id"], tree["second_version_id"]):
        assert post(client, f"/api/versions/{version_id}/freeze", csrf, {}).status_code == 200

    customer_baseline = customer_client.get(
        f"/api/versions/{tree['version_id']}/baselines"
    )
    assert customer_baseline.status_code == 200
    customer_snapshot = customer_baseline.json()[0]["snapshot"]
    assert customer_snapshot["artifacts"] == []
    assert {item["requester_id"] for item in customer_snapshot["requirements"]} == {
        customer["id"]
    }

    listed = customer_client.get(
        f"/api/requirements?project_id={tree['project_id']}"
    ).json()
    assert {item["id"] for item in listed} == {item["id"] for item in own_requirements}
    hidden_detail = customer_client.get(
        f"/api/requirements/{internal_requirements[0]['id']}"
    )
    assert hidden_detail.status_code == 403
    assert hidden_detail.json()["detail"]["code"] == "REQUIREMENT_FORBIDDEN"

    artifacts = customer_client.get(
        f"/api/artifacts?project_id={tree['project_id']}"
    ).json()
    assert {item["id"] for item in artifacts} == {own_artifact["id"]}
    hidden_artifact = customer_client.get(
        f"/api/artifacts/{internal_artifact['id']}/download"
    )
    assert hidden_artifact.status_code == 403
    hidden_shared_artifact = customer_client.get(
        f"/api/artifacts/{shared_artifact['id']}/download"
    )
    assert hidden_shared_artifact.status_code == 403
    artifact_csv = customer_client.get(
        f"/api/exports/artifacts.csv?project_id={tree['project_id']}"
    ).content.decode("utf-8-sig")
    assert "'@客户可见支持成果" in artifact_csv
    assert internal_artifact["title"] not in artifact_csv
    assert shared_artifact["title"] not in artifact_csv

    operations = customer_client.get(
        f"/api/operations?project_id={tree['project_id']}"
    ).json()
    assert {item["id"] for item in operations} == {own_operation["id"]}
    operation_csv = customer_client.get(
        f"/api/exports/operations.csv?project_id={tree['project_id']}"
    ).content.decode("utf-8-sig")
    assert "'=客户可见支持运营" in operation_csv
    assert internal_operation["title"] not in operation_csv

    support_results = customer_client.get("/api/search?q=支持").json()["results"]
    assert own_artifact["id"] in {
        item["id"] for item in support_results if item["type"] == "artifact"
    }
    assert internal_artifact["id"] not in {
        item["id"] for item in support_results if item["type"] == "artifact"
    }
    assert shared_artifact["id"] not in {
        item["id"] for item in support_results if item["type"] == "artifact"
    }
    assert own_operation["id"] in {
        item["id"] for item in support_results if item["type"] == "operation"
    }
    assert internal_operation["id"] not in {
        item["id"] for item in support_results if item["type"] == "operation"
    }

    search_results = customer_client.get("/api/search?q=隔离").json()["results"]
    requirement_results = [item for item in search_results if item["type"] == "requirement"]
    assert {item["id"] for item in requirement_results} == {
        item["id"] for item in own_requirements
    }
    assert {item["project_name"] for item in requirement_results} == {"全流程项目"}
    assert {item["version_name"] for item in requirement_results} == {"首版", "跨年版"}
    for item in requirement_results:
        assert "estimated_budget" not in item
        assert "allocated_budget" not in item
        assert "actual_cost" not in item
    requirement_csv = customer_client.get(
        f"/api/exports/requirements.csv?project_id={tree['project_id']}"
    ).content.decode("utf-8-sig")
    progress_csv = customer_client.get(
        f"/api/exports/project-progress.csv?project_id={tree['project_id']}"
    ).content.decode("utf-8-sig")
    for own in own_requirements:
        assert own["code"] in requirement_csv
        assert own["code"] in progress_csv
    for internal in internal_requirements:
        assert internal["code"] not in requirement_csv
        assert internal["code"] not in progress_csv

    dashboard = customer_client.get(
        f"/api/dashboard?project_id={tree['project_id']}"
    ).json()
    assert dashboard["metrics"]["requirements"] == 2
    assert dashboard["metrics"]["artifacts"] == 1
    assert dashboard["metrics"]["open_operations"] == 1
    assert {item["id"] for item in dashboard["recent_requirements"]} == {
        item["id"] for item in own_requirements
    }
    for item in dashboard["recent_requirements"]:
        assert item["version_id"] in {tree["version_id"], tree["second_version_id"]}
        assert item["assignee_id"] is None
        assert item["updated_at"]
        assert "estimated_budget" not in item
    milestones = customer_client.get(
        f"/api/milestones?project_id={tree['project_id']}"
    ).json()
    assert milestones["stages"][5]["artifact_count"] == 1
    assert milestones["reminders"] == []
    comparison = customer_client.get(
        f"/api/versions/compare?left_id={tree['version_id']}&right_id={tree['second_version_id']}"
    )
    assert comparison.status_code == 200, comparison.text
    differences = comparison.json()["requirements"]
    visible_items = differences["added"] + differences["removed"]
    visible_items += [item["left"] for item in differences["changed"]]
    visible_items += [item["right"] for item in differences["changed"]]
    assert visible_items
    assert {item["requester_id"] for item in visible_items} == {customer["id"]}
    assert not any(item["code"].startswith("REQ-INTERNAL") for item in visible_items)

    hidden_project = post(
        client,
        "/api/projects",
        csrf,
        {"code": "PRJ-HIDDEN", "name": "未授权项目"},
    ).json()
    assert customer_client.get(
        f"/api/exports/artifacts.csv?project_id={hidden_project['id']}"
    ).status_code == 403
    assert customer_client.get(
        f"/api/exports/operations.csv?project_id={hidden_project['id']}"
    ).status_code == 403
    customer_client.close()


def test_production_initial_admin_bootstrap_rules():
    isolated_engine = create_engine("sqlite://")
    Base.metadata.create_all(isolated_engine)
    weak = Settings(
        _env_file=None,
        app_env="production",
        database_url="sqlite://",
        admin_username="Admin",
        admin_password="weak-password",
    )
    with Session(isolated_engine) as db:
        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD is invalid"):
            seed_database(db, weak)
        db.rollback()

    strong = Settings(
        _env_file=None,
        app_env="production",
        database_url="sqlite://",
        admin_username="Admin",
        admin_password="Bootstrap@123",
    )
    with Session(isolated_engine) as db:
        assert seed_database(db, strong) is True
        admin = db.scalar(select(User).where(User.username == "admin"))
        assert admin is not None and admin.role == "admin" and admin.must_change_password is True
        strong.admin_password = ""
        assert seed_database(db, strong) is False

    changed_name = Settings(
        _env_file=None,
        app_env="production",
        database_url="sqlite://",
        admin_username="another_admin",
        admin_password="AnotherAdmin@123",
    )
    with Session(isolated_engine) as db:
        with pytest.raises(RuntimeError, match="does not match the initialized administrator"):
            seed_database(db, changed_name)
