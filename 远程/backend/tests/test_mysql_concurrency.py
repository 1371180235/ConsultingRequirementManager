from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import re
import sys
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url


MYSQL_TEST_URL = os.environ.get("MYSQL_CONCURRENCY_DATABASE_URL", "")


@pytest.mark.skipif(
    not MYSQL_TEST_URL,
    reason="set MYSQL_CONCURRENCY_DATABASE_URL to a dedicated MySQL test database",
)
def test_two_allocations_cannot_exceed_version_budget():
    database_name = make_url(MYSQL_TEST_URL).database or ""
    assert re.fullmatch(r".+_test\d*", database_name), (
        "MYSQL_CONCURRENCY_DATABASE_URL must point to a disposable database "
        "whose name ends with _test or _test followed by digits"
    )
    assert "app.main" not in sys.modules, (
        "run this MySQL test in its own pytest process: "
        "pytest tests/test_mysql_concurrency.py -q"
    )
    os.environ.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": MYSQL_TEST_URL,
            "COOKIE_SECURE": "false",
            "AUTO_CREATE_TABLES": "false",
            "AUTO_SEED": "false",
            "ADMIN_PASSWORD": "Admin@123456",
        }
    )

    from fastapi.testclient import TestClient

    from app.database import Base, SessionLocal, engine
    from app.main import app
    from app.models import Requirement, User, UserSession
    from app.seed import seed_database

    def login(client: TestClient, username: str, password: str) -> str:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200, response.text
        return response.json()["csrf_token"]

    def post(client: TestClient, path: str, csrf: str, payload: dict):
        return client.post(path, headers={"X-CSRF-Token": csrf}, json=payload)

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        with SessionLocal() as db:
            seed_database(db)
        with TestClient(app, base_url="http://testserver") as admin_client:
            admin_csrf = login(admin_client, "admin", "Admin@123456")
            changed = admin_client.post(
                "/api/auth/change-password",
                headers={"X-CSRF-Token": admin_csrf},
                json={
                    "current_password": "Admin@123456",
                    "new_password": "Changed@123456",
                },
            )
            assert changed.status_code == 200, changed.text
            project = post(
                admin_client,
                "/api/projects",
                admin_csrf,
                {
                    "code": "MYSQL-CONCURRENCY",
                    "name": "MySQL 双连接资金测试",
                    "total_budget": "100",
                },
            ).json()
            plan = post(
                admin_client,
                "/api/plans",
                admin_csrf,
                {
                    "project_id": project["id"],
                    "year": 2026,
                    "name": "并发年度",
                    "budget": "100",
                },
            ).json()
            version = post(
                admin_client,
                "/api/versions",
                admin_csrf,
                {
                    "annual_plan_id": plan["id"],
                    "code": "V-CONCURRENT",
                    "name": "并发版本",
                    "budget": "100",
                },
            ).json()
            requirements = [
                post(
                    admin_client,
                    "/api/requirements",
                    admin_csrf,
                    {
                        "code": f"MYSQL-REQ-{index}",
                        "title": f"并发需求 {index}",
                        "project_id": project["id"],
                        "annual_plan_id": plan["id"],
                        "version_id": version["id"],
                        "stakeholder_role": "manager",
                    },
                ).json()
                for index in (1, 2)
            ]
            for index in (1, 2):
                created = post(
                    admin_client,
                    "/api/users",
                    admin_csrf,
                    {
                        "username": f"mysql_manager_{index}",
                        "full_name": f"并发项目经理 {index}",
                        "role": "manager",
                        "initial_password": f"Manager{index}@123",
                    },
                )
                assert created.status_code == 201, created.text

        login_clients = [
            TestClient(app, base_url="http://testserver"),
            TestClient(app, base_url="http://testserver"),
        ]
        login_barrier = Barrier(2)

        def login_same_account(index: int):
            login_barrier.wait(timeout=10)
            return login_clients[index].post(
                "/api/auth/login",
                json={"username": "admin", "password": "Changed@123456"},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            login_responses = list(executor.map(login_same_account, (0, 1)))
        assert [response.status_code for response in login_responses] == [200, 200]
        assert sorted(
            client.get("/api/auth/me").status_code for client in login_clients
        ) == [200, 401]
        with SessionLocal() as db:
            admin_id = db.scalar(select(User.id).where(User.username == "admin"))
            sessions = db.scalars(
                select(UserSession).where(UserSession.user_id == admin_id)
            ).all()
            assert len(sessions) == 1
        for client in login_clients:
            client.close()

        clients = [
            TestClient(app, base_url="http://testserver"),
            TestClient(app, base_url="http://testserver"),
        ]
        csrf_tokens = []
        for index, client in enumerate(clients, 1):
            csrf = login(client, f"mysql_manager_{index}", f"Manager{index}@123")
            changed = client.post(
                "/api/auth/change-password",
                headers={"X-CSRF-Token": csrf},
                json={
                    "current_password": f"Manager{index}@123",
                    "new_password": f"Manager{index}@456",
                },
            )
            assert changed.status_code == 200, changed.text
            csrf_tokens.append(csrf)

        barrier = Barrier(2)

        def allocate(index: int):
            barrier.wait(timeout=10)
            return post(
                clients[index],
                "/api/funds/entries",
                csrf_tokens[index],
                {
                    "project_id": project["id"],
                    "annual_plan_id": plan["id"],
                    "version_id": version["id"],
                    "requirement_id": requirements[index]["id"],
                    "entry_type": "allocation",
                    "amount": "60",
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(allocate, (0, 1)))
        assert sorted(response.status_code for response in responses) == [201, 409]
        conflict = next(response for response in responses if response.status_code == 409)
        assert conflict.json()["detail"]["code"] == "VERSION_ALLOCATION_EXCEEDED"
        with SessionLocal() as db:
            stored = db.scalars(
                select(Requirement).where(
                    Requirement.id.in_([item["id"] for item in requirements])
                )
            ).all()
            assert sum(
                (Decimal(item.allocated_budget or 0) for item in stored), Decimal(0)
            ) == Decimal("60")
        for client in clients:
            client.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
