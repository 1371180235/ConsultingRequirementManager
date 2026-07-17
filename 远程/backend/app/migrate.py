from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine.reflection import Inspector

from . import models  # noqa: F401 - registers SQLAlchemy metadata
from .database import Base, engine


LEGACY_REVISION = "20260716_0001"
STABLE_KEY_REVISION = "20260716_0002"
ARTIFACT_APPROVAL_REVISION = "20260716_0003"
DASHBOARD_LAYOUT_REVISION = "20260716_0004"
CHANGE_BASELINE_REVISION = "20260716_0005"
HEAD_REVISION = "20260717_0006"
ARTIFACT_DIGEST_COLUMNS = {"sha256_hex"}
POST_BASELINE_TABLES = {"role_dashboard_layouts", "artifact_change_uploads"}
POST_BASELINE_COLUMNS = {
    "requirements": {"stable_key"},
    "annual_plans": {"target"},
    "deliverables": {"approval_status", "reviewed_by", "reviewed_at", "review_note"},
    "change_requests": {"expected_baseline_sequence"},
}


def alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[1]
    return Config(str(backend_dir / "alembic.ini"))


def _classify_unversioned_schema(inspector: Inspector) -> str:
    existing_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables)
    managed_tables = existing_tables & expected_tables
    if not managed_tables:
        return "empty"
    baseline_tables = expected_tables - POST_BASELINE_TABLES
    missing_tables = baseline_tables - managed_tables
    if missing_tables:
        raise RuntimeError(
            "Unversioned database has an incomplete schema; missing tables: "
            + ", ".join(sorted(missing_tables))
        )

    missing_columns = []
    for table_name in sorted(baseline_tables):
        table = Base.metadata.tables[table_name]
        existing_columns = {
            item["name"] for item in inspector.get_columns(table_name)
        }
        baseline_columns = {
            column.name for column in table.columns
        } - POST_BASELINE_COLUMNS.get(table_name, set())
        missing_columns.extend(
            f"{table_name}.{column_name}"
            for column_name in sorted(baseline_columns - existing_columns)
        )
    if missing_columns:
        raise RuntimeError(
            "Unversioned database is missing baseline columns: "
            + ", ".join(missing_columns)
        )

    requirement_columns = {item["name"]: item for item in inspector.get_columns("requirements")}
    if "stable_key" not in requirement_columns:
        legacy_columns = {
            column.name for column in Base.metadata.tables["requirements"].columns
        } - {"stable_key"}
        missing_columns = legacy_columns - set(requirement_columns)
        if missing_columns:
            raise RuntimeError(
                "Unversioned requirements table is not the supported legacy schema; missing columns: "
                + ", ".join(sorted(missing_columns))
            )
        return "legacy"

    stable_key = requirement_columns["stable_key"]
    annual_plan = requirement_columns.get("annual_plan_id")
    unique_columns = {
        tuple(item.get("column_names") or ()) for item in inspector.get_unique_constraints("requirements")
    }
    if stable_key.get("nullable") or not annual_plan or not annual_plan.get("nullable"):
        raise RuntimeError("Unversioned requirements table has a partially applied planning-pool migration")
    if ("version_id", "stable_key") not in unique_columns:
        raise RuntimeError("Unversioned requirements table is missing the stable-key uniqueness constraint")
    annual_columns = {item["name"]: item for item in inspector.get_columns("annual_plans")}
    deliverable_columns = {item["name"]: item for item in inspector.get_columns("deliverables")}
    artifact_fields = {"approval_status", "reviewed_by", "reviewed_at", "review_note"}
    if "target" not in annual_columns or not artifact_fields.issubset(deliverable_columns):
        return "stable_key"
    if annual_columns["target"].get("nullable") or deliverable_columns["approval_status"].get("nullable"):
        return "stable_key"
    reviewer_fk_exists = any(
        item.get("constrained_columns") == ["reviewed_by"]
        and item.get("referred_table") == "users"
        for item in inspector.get_foreign_keys("deliverables")
    )
    approval_index_exists = any(
        item.get("name") == "ix_deliverables_approval_status"
        and item.get("column_names") == ["approval_status"]
        for item in inspector.get_indexes("deliverables")
    )
    if not reviewer_fk_exists or not approval_index_exists:
        return "stable_key"

    layout_table = Base.metadata.tables["role_dashboard_layouts"]
    if layout_table.name not in existing_tables:
        return "artifact_approval"
    layout_columns = {
        item["name"]: item
        for item in inspector.get_columns("role_dashboard_layouts")
    }
    required_layout_columns = {
        column.name for column in layout_table.columns
    }
    if missing_layout_columns := required_layout_columns - set(layout_columns):
        raise RuntimeError(
            "Unversioned dashboard-layout table is missing columns: "
            + ", ".join(sorted(missing_layout_columns))
        )
    if any(
        layout_columns[name].get("nullable")
        for name in ("id", "role", "component_keys", "updated_at")
    ):
        raise RuntimeError("Unversioned dashboard-layout table has incompatible nullability")
    layout_fk_exists = any(
        item.get("constrained_columns") == ["updated_by"]
        and item.get("referred_table") == "users"
        and str(item.get("options", {}).get("ondelete", "")).upper() == "SET NULL"
        for item in inspector.get_foreign_keys("role_dashboard_layouts")
    )
    layout_role_index_exists = any(
        item.get("name") == "ix_role_dashboard_layouts_role"
        and item.get("column_names") == ["role"]
        and item.get("unique")
        for item in inspector.get_indexes("role_dashboard_layouts")
    )
    if not layout_fk_exists or not layout_role_index_exists:
        return "artifact_approval"

    change_columns = {
        item["name"]: item for item in inspector.get_columns("change_requests")
    }
    expected_baseline = change_columns.get("expected_baseline_sequence")
    baseline_sequence_unique = any(
        tuple(item.get("column_names") or ()) == ("version_id", "sequence")
        for item in inspector.get_unique_constraints("version_baselines")
    )
    if (
        not expected_baseline
        or expected_baseline.get("nullable")
        or not baseline_sequence_unique
    ):
        return "dashboard_layout"
    upload_table = Base.metadata.tables["artifact_change_uploads"]
    if upload_table.name not in existing_tables:
        return "dashboard_layout"
    upload_columns = {
        item["name"]: item
        for item in inspector.get_columns("artifact_change_uploads")
    }
    required_upload_columns = {
        column.name for column in upload_table.columns
    } - ARTIFACT_DIGEST_COLUMNS
    if required_upload_columns - set(upload_columns):
        return "dashboard_layout"
    if any(
        upload_columns[name].get("nullable")
        for name in (
            "id",
            "token",
            "version_id",
            "expected_baseline_sequence",
            "change_request_id",
            "stage",
            "category",
            "title",
            "original_filename",
            "storage_key",
            "content_type",
            "size_bytes",
            "uploaded_by",
            "created_at",
        )
    ):
        return "dashboard_layout"
    upload_indexes = {
        item.get("name"): item
        for item in inspector.get_indexes("artifact_change_uploads")
    }
    expected_upload_indexes = {
        "ix_artifact_change_uploads_change_request_id": ("change_request_id", False),
        "ix_artifact_change_uploads_requirement_id": ("requirement_id", False),
        "ix_artifact_change_uploads_token": ("token", True),
        "ix_artifact_change_uploads_uploaded_by": ("uploaded_by", False),
        "ix_artifact_change_uploads_version_id": ("version_id", False),
    }
    if any(
        name not in upload_indexes
        or tuple(upload_indexes[name].get("column_names") or ()) != (column,)
        or bool(upload_indexes[name].get("unique")) != unique
        for name, (column, unique) in expected_upload_indexes.items()
    ):
        return "dashboard_layout"
    upload_foreign_keys = inspector.get_foreign_keys("artifact_change_uploads")
    expected_upload_foreign_keys = {
        "version_id": ("delivery_versions", "CASCADE"),
        "change_request_id": ("change_requests", "CASCADE"),
        "requirement_id": ("requirements", "SET NULL"),
        "uploaded_by": ("users", ""),
    }
    if any(
        not any(
            item.get("constrained_columns") == [column]
            and item.get("referred_table") == table
            and str(item.get("options", {}).get("ondelete", "")).upper()
            == ondelete
            for item in upload_foreign_keys
        )
        for column, (table, ondelete) in expected_upload_foreign_keys.items()
    ):
        return "dashboard_layout"
    storage_unique = any(
        tuple(item.get("column_names") or ()) == ("storage_key",)
        for item in inspector.get_unique_constraints("artifact_change_uploads")
    )
    if not storage_unique:
        return "dashboard_layout"
    if not ARTIFACT_DIGEST_COLUMNS.issubset(upload_columns):
        return "change_baseline"
    return "current"


def _validate_unversioned_schema() -> str:
    with engine.connect() as connection:
        inspector = inspect(connection)
        try:
            return _classify_unversioned_schema(inspector)
        finally:
            inspector.clear_cache()
            connection.rollback()


def migrate_database() -> None:
    config = alembic_config()
    current_revision = None
    schema_state = None
    try:
        with engine.connect() as connection:
            current_revision = MigrationContext.configure(connection).get_current_revision()
            connection.rollback()
        if current_revision is None:
            schema_state = _validate_unversioned_schema()
    finally:
        # Alembic uses its own NullPool engine. Release every inspection
        # connection before it starts schema-changing DDL.
        engine.dispose()

    if current_revision is None:
        if schema_state == "legacy":
            command.stamp(config, LEGACY_REVISION)
            print(f"Adopted unversioned legacy schema at {LEGACY_REVISION}.")
        elif schema_state == "stable_key":
            command.stamp(config, STABLE_KEY_REVISION)
            print(f"Adopted unversioned stable-key schema at {STABLE_KEY_REVISION}.")
        elif schema_state == "artifact_approval":
            command.stamp(config, ARTIFACT_APPROVAL_REVISION)
            print(
                "Adopted unversioned artifact-approval schema at "
                f"{ARTIFACT_APPROVAL_REVISION}."
            )
        elif schema_state == "dashboard_layout":
            command.stamp(config, DASHBOARD_LAYOUT_REVISION)
            print(
                "Adopted unversioned dashboard-layout schema at "
                f"{DASHBOARD_LAYOUT_REVISION}."
            )
        elif schema_state == "change_baseline":
            command.stamp(config, CHANGE_BASELINE_REVISION)
            print(
                "Adopted unversioned change-baseline schema at "
                f"{CHANGE_BASELINE_REVISION}."
            )
        elif schema_state == "current":
            command.stamp(config, HEAD_REVISION)
            print(f"Adopted unversioned current schema at {HEAD_REVISION}.")
    command.upgrade(config, "head")
    print(f"Database migration is at {HEAD_REVISION}.")


if __name__ == "__main__":
    migrate_database()
