from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


def test_alembic_initial_migration_matches_models(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    migrated_engine = create_engine(database_url)
    table_names = set(inspect(migrated_engine).get_table_names())
    assert {
        "alembic_version",
        "users",
        "requirements",
        "audit_logs",
        "deliverables",
        "role_dashboard_layouts",
        "artifact_change_uploads",
    } <= table_names
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260717_0006"

    inspector = inspect(migrated_engine)
    plan_columns = {item["name"]: item for item in inspector.get_columns("annual_plans")}
    artifact_columns = {item["name"]: item for item in inspector.get_columns("deliverables")}
    artifact_foreign_keys = inspector.get_foreign_keys("deliverables")
    artifact_indexes = inspector.get_indexes("deliverables")
    change_columns = {
        item["name"]: item for item in inspector.get_columns("change_requests")
    }
    staged_artifact_columns = {
        item["name"]: item
        for item in inspector.get_columns("artifact_change_uploads")
    }
    baseline_uniques = inspector.get_unique_constraints("version_baselines")
    layout_columns = {
        item["name"]: item
        for item in inspector.get_columns("role_dashboard_layouts")
    }
    layout_foreign_keys = inspector.get_foreign_keys("role_dashboard_layouts")
    layout_indexes = inspector.get_indexes("role_dashboard_layouts")
    assert plan_columns["target"]["nullable"] is False
    assert artifact_columns["approval_status"]["nullable"] is False
    assert {"reviewed_by", "reviewed_at", "review_note"} <= set(artifact_columns)
    assert any(item.get("constrained_columns") == ["reviewed_by"] for item in artifact_foreign_keys)
    assert any(item.get("name") == "ix_deliverables_approval_status" for item in artifact_indexes)
    assert change_columns["expected_baseline_sequence"]["nullable"] is False
    assert staged_artifact_columns["sha256_hex"]["type"].length == 64
    assert any(
        item.get("name") == "uq_version_baseline_sequence"
        and tuple(item.get("column_names") or ()) == ("version_id", "sequence")
        for item in baseline_uniques
    )
    assert {"id", "role", "component_keys", "updated_by", "updated_at"} == set(layout_columns)
    assert any(item.get("constrained_columns") == ["updated_by"] for item in layout_foreign_keys)
    assert any(
        item.get("name") == "ix_role_dashboard_layouts_role" and item.get("unique")
        for item in layout_indexes
    )

    check = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert check.returncode == 0, check.stdout + check.stderr


def test_unversioned_legacy_database_is_adopted_and_upgraded(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    baseline = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260716_0001"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    legacy_engine = create_engine(database_url)
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects "
                "(id, code, name, description, total_budget, status, current_stage, created_at, updated_at) "
                "VALUES (1, 'P-LEGACY', '旧项目', '', 100, 'active', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, full_name, password_hash, role, is_active, must_change_password, "
                "failed_login_count, created_at, updated_at) "
                "VALUES (1, 'legacy_admin', '旧管理员', 'hash', 'admin', 1, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO annual_plans "
                "(id, project_id, year, name, budget, pain_points, created_at, updated_at) "
                "VALUES (1, 1, 2026, '旧年度', 100, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO requirements "
                "(id, code, title, description, project_id, annual_plan_id, version_id, requester_id, "
                "stakeholder_role, estimated_budget, allocated_budget, actual_cost, status, priority, "
                "estimated_hours, actual_hours, created_at, updated_at) "
                "VALUES (1, 'REQ-LEGACY', '旧需求', '', 1, 1, NULL, 1, 'manager', 0, 0, 0, "
                "'draft', 'medium', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(text("DROP TABLE alembic_version"))

    upgrade = subprocess.run(
        [sys.executable, "-m", "app.migrate"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    assert "Database migration is at 20260717_0006." in upgrade.stdout
    with legacy_engine.connect() as connection:
        row = connection.execute(
            text("SELECT code, stable_key, annual_plan_id FROM requirements WHERE id = 1")
        ).one()
        assert row == ("REQ-LEGACY", "REQ-LEGACY", 1)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260717_0006"
    requirement_columns = {item["name"]: item for item in inspect(legacy_engine).get_columns("requirements")}
    assert requirement_columns["stable_key"]["nullable"] is False
    assert requirement_columns["annual_plan_id"]["nullable"] is True
    assert "role_dashboard_layouts" in inspect(legacy_engine).get_table_names()


def test_unversioned_incomplete_legacy_schema_is_rejected(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "incomplete-legacy.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    baseline = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260716_0001"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    legacy_engine = create_engine(database_url)
    with legacy_engine.begin() as connection:
        connection.execute(text("ALTER TABLE projects DROP COLUMN description"))
        connection.execute(text("DROP TABLE alembic_version"))

    upgrade = subprocess.run(
        [sys.executable, "-m", "app.migrate"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgrade.returncode != 0
    assert "projects.description" in upgrade.stdout + upgrade.stderr


def test_partially_applied_planning_pool_migration_is_completed(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "partial-planning-pool.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    baseline = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260716_0001"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    partial_engine = create_engine(database_url)
    with partial_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE requirements ADD COLUMN stable_key VARCHAR(64)")
        )
        connection.execute(
            text("UPDATE requirements SET stable_key = code WHERE stable_key IS NULL")
        )

    upgrade = subprocess.run(
        [sys.executable, "-m", "app.migrate"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    inspector = inspect(partial_engine)
    requirement_columns = {
        item["name"]: item for item in inspector.get_columns("requirements")
    }
    requirement_uniques = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints("requirements")
    }
    assert requirement_columns["stable_key"]["nullable"] is False
    assert requirement_columns["annual_plan_id"]["nullable"] is True
    assert ("version_id", "stable_key") in requirement_uniques
    with partial_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260717_0006"


def test_partially_applied_latest_migration_is_completed(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "partial.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    baseline = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260716_0002"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    partial_engine = create_engine(database_url)
    with partial_engine.begin() as connection:
        connection.execute(text("ALTER TABLE annual_plans ADD COLUMN target TEXT"))
        connection.execute(text("ALTER TABLE deliverables ADD COLUMN approval_status VARCHAR(20)"))
        connection.execute(text("UPDATE annual_plans SET target = ''"))
        connection.execute(text("UPDATE deliverables SET approval_status = 'draft'"))

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    inspector = inspect(partial_engine)
    plan_columns = {item["name"]: item for item in inspector.get_columns("annual_plans")}
    artifact_columns = {item["name"]: item for item in inspector.get_columns("deliverables")}
    assert plan_columns["target"]["nullable"] is False
    assert artifact_columns["approval_status"]["nullable"] is False
    assert {"reviewed_by", "reviewed_at", "review_note"} <= set(artifact_columns)
    assert any(
        item.get("constrained_columns") == ["reviewed_by"]
        for item in inspector.get_foreign_keys("deliverables")
    )
    assert any(
        item.get("name") == "ix_deliverables_approval_status"
        for item in inspector.get_indexes("deliverables")
    )
    assert "role_dashboard_layouts" in inspector.get_table_names()


def test_unversioned_current_schema_with_missing_index_is_repaired(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "unversioned-partial.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    partial_engine = create_engine(database_url)
    with partial_engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_deliverables_approval_status"))
        connection.execute(text("DROP TABLE alembic_version"))

    repaired = subprocess.run(
        [sys.executable, "-m", "app.migrate"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    inspector = inspect(partial_engine)
    assert any(
        item.get("name") == "ix_deliverables_approval_status"
        for item in inspector.get_indexes("deliverables")
    )
    with partial_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260717_0006"


def test_unversioned_change_baseline_schema_only_runs_digest_migration(
    tmp_path: Path,
):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "unversioned-change-baseline.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    baseline = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260716_0005"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    old_engine = create_engine(database_url)
    with old_engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    old_engine.dispose()

    upgraded = subprocess.run(
        [sys.executable, "-m", "app.migrate"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    assert (
        "Adopted unversioned change-baseline schema at 20260716_0005."
        in upgraded.stdout
    )
    assert "20260716_0005 -> 20260717_0006" in upgraded.stderr

    migrated_engine = create_engine(database_url)
    assert "sha256_hex" in {
        item["name"]
        for item in inspect(migrated_engine).get_columns("artifact_change_uploads")
    }
    with migrated_engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "20260717_0006"
    migrated_engine.dispose()


def test_unversioned_current_schema_is_adopted_at_true_head(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "unversioned-current.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    baseline = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    current_engine = create_engine(database_url)
    with current_engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    current_engine.dispose()

    adopted = subprocess.run(
        [sys.executable, "-m", "app.migrate"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert adopted.returncode == 0, adopted.stdout + adopted.stderr
    assert (
        "Adopted unversioned current schema at 20260717_0006."
        in adopted.stdout
    )
    assert "Running upgrade" not in adopted.stderr

    adopted_engine = create_engine(database_url)
    with adopted_engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "20260717_0006"
    adopted_engine.dispose()


def test_unversioned_artifact_approval_schema_adds_dashboard_layouts(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "unversioned-artifact-approval.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    baseline = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260716_0003"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    partial_engine = create_engine(database_url)
    with partial_engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))

    upgrade = subprocess.run(
        [sys.executable, "-m", "app.migrate"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    partial_engine.dispose()
    inspector = inspect(partial_engine)
    assert "role_dashboard_layouts" in inspector.get_table_names()
    with partial_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260717_0006"


def test_partially_applied_dashboard_layout_migration_is_completed(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "partial-dashboard-layout.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    baseline = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260716_0003"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    partial_engine = create_engine(database_url)
    with partial_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE role_dashboard_layouts ("
                "id INTEGER PRIMARY KEY, role VARCHAR(20) NOT NULL, "
                "component_keys JSON NOT NULL, updated_by INTEGER, "
                "updated_at DATETIME NOT NULL)"
            )
        )

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    partial_engine.dispose()
    inspector = inspect(partial_engine)
    assert any(
        item.get("constrained_columns") == ["updated_by"]
        for item in inspector.get_foreign_keys("role_dashboard_layouts")
    )
    assert any(
        item.get("name") == "ix_role_dashboard_layouts_role" and item.get("unique")
        for item in inspector.get_indexes("role_dashboard_layouts")
    )


def test_unversioned_dashboard_schema_backfills_change_baseline_sequence(
    tmp_path: Path,
):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "unversioned-dashboard.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    baseline = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260716_0004"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    old_engine = create_engine(database_url)
    with old_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, full_name, password_hash, role, is_active, "
                "must_change_password, failed_login_count, created_at, updated_at) "
                "VALUES (1, 'migration_admin', '迁移管理员', 'hash', 'admin', 1, 0, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects "
                "(id, code, name, description, total_budget, status, current_stage, created_at, updated_at) "
                "VALUES (1, 'P-MIGRATION', '迁移项目', '', 100, 'active', 3, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO annual_plans "
                "(id, project_id, year, name, target, budget, pain_points, created_at, updated_at) "
                "VALUES (1, 1, 2026, '迁移年度', '', 100, '', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO delivery_versions "
                "(id, annual_plan_id, code, name, target, budget, status, frozen_at, created_at, updated_at) "
                "VALUES (1, 1, 'V1', '迁移版本', '', 100, 'frozen', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO version_baselines "
                "(id, version_id, sequence, snapshot, created_by, created_at) "
                "VALUES (1, 1, 1, '{}', 1, CURRENT_TIMESTAMP), "
                "(2, 1, 7, '{}', 1, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO change_requests "
                "(id, version_id, title, reason, change_type, payload, status, requested_by, created_at, updated_at) "
                "VALUES (1, 1, '旧变更', '迁移测试', 'scope_update', '{}', 'pending', 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(text("DROP TABLE alembic_version"))

    upgraded = subprocess.run(
        [sys.executable, "-m", "app.migrate"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    inspector = inspect(old_engine)
    change_columns = {
        item["name"]: item for item in inspector.get_columns("change_requests")
    }
    assert change_columns["expected_baseline_sequence"]["nullable"] is False
    assert any(
        item.get("name") == "uq_version_baseline_sequence"
        and tuple(item.get("column_names") or ()) == ("version_id", "sequence")
        for item in inspector.get_unique_constraints("version_baselines")
    )
    with old_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT expected_baseline_sequence FROM change_requests WHERE id = 1"
            )
        ) == 7
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "20260717_0006"


def test_migrations_compile_for_mysql_84(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": "mysql+pymysql://crm:Password123@mysql:3306/consulting?charset=utf8mb4",
        "AUTO_CREATE_TABLES": "false",
        "AUTO_SEED": "false",
    }
    generated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=backend_dir,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    assert "stable_key" in generated.stdout
    assert "annual_plans ADD COLUMN target TEXT" in generated.stdout
    assert "deliverables ADD COLUMN approval_status VARCHAR(20)" in generated.stdout
    assert "CREATE TABLE role_dashboard_layouts" in generated.stdout
    assert "CREATE TABLE artifact_change_uploads" in generated.stdout
    assert "artifact_change_uploads ADD COLUMN sha256_hex VARCHAR(64)" in generated.stdout
    assert "expected_baseline_sequence" in generated.stdout
    assert "uq_version_baseline_sequence" in generated.stdout
    assert "20260717_0006" in generated.stdout


def test_unversioned_validation_releases_reflection_connection(monkeypatch):
    from app import migrate

    events = []

    class FakeConnection:
        def __enter__(self):
            events.append("connection_enter")
            return self

        def __exit__(self, *_):
            events.append("connection_exit")

        def rollback(self):
            events.append("connection_rollback")

    class FakeEngine:
        def connect(self):
            events.append("engine_connect")
            return FakeConnection()

    class FakeInspector:
        def get_table_names(self):
            events.append("get_table_names")
            return []

        def clear_cache(self):
            events.append("inspector_clear")

    monkeypatch.setattr(migrate, "engine", FakeEngine())
    monkeypatch.setattr(
        migrate,
        "inspect",
        lambda connection: events.append("inspect") or FakeInspector(),
    )

    assert migrate._validate_unversioned_schema() == "empty"
    assert events == [
        "engine_connect",
        "connection_enter",
        "inspect",
        "get_table_names",
        "inspector_clear",
        "connection_rollback",
        "connection_exit",
    ]


def test_migrate_disposes_inspection_pool_before_alembic_upgrade(monkeypatch):
    from app import migrate

    events = []

    class FakeConnection:
        def __enter__(self):
            events.append("connection_enter")
            return self

        def __exit__(self, *_):
            events.append("connection_exit")

        def rollback(self):
            events.append("connection_rollback")

    class FakeEngine:
        def connect(self):
            events.append("engine_connect")
            return FakeConnection()

        def dispose(self):
            events.append("engine_dispose")

    class FakeMigrationContext:
        def get_current_revision(self):
            events.append("get_current_revision")
            return None

    def validate():
        events.append("validate")
        return "empty"

    def upgrade(_config, revision):
        events.append(f"upgrade_{revision}")

    monkeypatch.setattr(migrate, "engine", FakeEngine())
    monkeypatch.setattr(
        migrate.MigrationContext,
        "configure",
        lambda connection: events.append("configure") or FakeMigrationContext(),
    )
    monkeypatch.setattr(migrate, "_validate_unversioned_schema", validate)
    monkeypatch.setattr(migrate.command, "upgrade", upgrade)

    migrate.migrate_database()

    assert events.index("engine_dispose") < events.index("upgrade_head")
