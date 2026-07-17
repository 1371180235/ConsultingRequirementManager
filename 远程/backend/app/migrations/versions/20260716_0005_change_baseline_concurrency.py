"""add optimistic concurrency control for version changes

Revision ID: 20260716_0005
Revises: 20260716_0004
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260716_0005"
down_revision: Union[str, Sequence[str], None] = "20260716_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EXPECTED_COLUMN = "expected_baseline_sequence"
BASELINE_UNIQUE = "uq_version_baseline_sequence"
UPLOAD_TABLE = "artifact_change_uploads"


def _backfill_expected_baselines() -> None:
    op.execute(
        sa.text(
            "UPDATE change_requests "
            "SET expected_baseline_sequence = COALESCE(("
            "SELECT MAX(version_baselines.sequence) FROM version_baselines "
            "WHERE version_baselines.version_id = change_requests.version_id"
            "), 0) "
            "WHERE expected_baseline_sequence IS NULL "
            "OR expected_baseline_sequence = 0"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    offline = context.is_offline_mode()
    dialect = bind.dialect.name
    change_columns: set[str] = set()
    baseline_uniques: dict[str | None, tuple[str, ...]] = {}
    table_names: set[str] = set()
    if not offline:
        inspector = sa.inspect(bind)
        table_names = set(inspector.get_table_names())
        change_columns = {
            item["name"] for item in inspector.get_columns("change_requests")
        }
        baseline_uniques = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints("version_baselines")
        }

    if offline or EXPECTED_COLUMN not in change_columns:
        op.add_column(
            "change_requests",
            sa.Column(
                EXPECTED_COLUMN,
                sa.Integer(),
                nullable=True,
                server_default=sa.text("0"),
            ),
        )
    _backfill_expected_baselines()

    if dialect == "sqlite" and not offline:
        with op.batch_alter_table("change_requests", recreate="always") as batch_op:
            batch_op.alter_column(
                EXPECTED_COLUMN,
                existing_type=sa.Integer(),
                nullable=False,
                server_default=None,
            )
    else:
        op.alter_column(
            "change_requests",
            EXPECTED_COLUMN,
            existing_type=sa.Integer(),
            nullable=False,
            server_default=None,
        )

    existing_sequence_unique = next(
        (
            name
            for name, columns in baseline_uniques.items()
            if columns == ("version_id", "sequence")
        ),
        None,
    )
    if not offline and existing_sequence_unique and existing_sequence_unique != BASELINE_UNIQUE:
        raise RuntimeError("Version baseline sequence unique constraint has an incompatible name")
    if offline or not existing_sequence_unique:
        if dialect == "sqlite" and not offline:
            with op.batch_alter_table("version_baselines", recreate="always") as batch_op:
                batch_op.create_unique_constraint(
                    BASELINE_UNIQUE, ["version_id", "sequence"]
                )
        else:
            op.create_unique_constraint(
                BASELINE_UNIQUE,
                "version_baselines",
                ["version_id", "sequence"],
            )

    if offline or UPLOAD_TABLE not in table_names:
        op.create_table(
            UPLOAD_TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("token", sa.String(length=64), nullable=False),
            sa.Column("version_id", sa.Integer(), nullable=False),
            sa.Column("expected_baseline_sequence", sa.Integer(), nullable=False),
            sa.Column("change_request_id", sa.Integer(), nullable=False),
            sa.Column("stage", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(length=50), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("requirement_id", sa.Integer(), nullable=True),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("storage_key", sa.String(length=500), nullable=False),
            sa.Column("content_type", sa.String(length=150), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("uploaded_by", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["change_request_id"],
                ["change_requests.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["requirement_id"], ["requirements.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"]),
            sa.ForeignKeyConstraint(
                ["version_id"], ["delivery_versions.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("storage_key"),
        )
        op.create_index(
            "ix_artifact_change_uploads_change_request_id",
            UPLOAD_TABLE,
            ["change_request_id"],
            unique=False,
        )
        op.create_index(
            "ix_artifact_change_uploads_requirement_id",
            UPLOAD_TABLE,
            ["requirement_id"],
            unique=False,
        )
        op.create_index(
            "ix_artifact_change_uploads_token",
            UPLOAD_TABLE,
            ["token"],
            unique=True,
        )
        op.create_index(
            "ix_artifact_change_uploads_uploaded_by",
            UPLOAD_TABLE,
            ["uploaded_by"],
            unique=False,
        )
        op.create_index(
            "ix_artifact_change_uploads_version_id",
            UPLOAD_TABLE,
            ["version_id"],
            unique=False,
        )
    elif not offline:
        upload_inspector = sa.inspect(bind)
        upload_column_items = {
            item["name"]: item
            for item in upload_inspector.get_columns(UPLOAD_TABLE)
        }
        upload_columns = set(upload_column_items)
        required_columns = {
            "id",
            "token",
            "version_id",
            "expected_baseline_sequence",
            "change_request_id",
            "stage",
            "category",
            "title",
            "requirement_id",
            "original_filename",
            "storage_key",
            "content_type",
            "size_bytes",
            "uploaded_by",
            "created_at",
        }
        if missing := required_columns - upload_columns:
            raise RuntimeError(
                "Partially applied artifact-change upload migration is missing columns: "
                + ", ".join(sorted(missing))
            )
        required_nonnullable = required_columns - {"requirement_id"}
        if invalid := {
            name
            for name in required_nonnullable
            if upload_column_items[name].get("nullable")
        }:
            raise RuntimeError(
                "Artifact-change upload columns must be non-nullable: "
                + ", ".join(sorted(invalid))
            )
        foreign_keys = upload_inspector.get_foreign_keys(UPLOAD_TABLE)
        expected_foreign_keys = {
            "version_id": ("delivery_versions", "CASCADE"),
            "change_request_id": ("change_requests", "CASCADE"),
            "requirement_id": ("requirements", "SET NULL"),
            "uploaded_by": ("users", ""),
        }
        for column, (table, ondelete) in expected_foreign_keys.items():
            foreign_key = next(
                (
                    item
                    for item in foreign_keys
                    if item.get("constrained_columns") == [column]
                ),
                None,
            )
            actual_delete = str(
                (foreign_key or {}).get("options", {}).get("ondelete", "")
            ).upper()
            if (
                not foreign_key
                or foreign_key.get("referred_table") != table
                or actual_delete != ondelete
            ):
                raise RuntimeError(
                    f"Artifact-change upload foreign key {column} is incompatible"
                )
        storage_unique = any(
            tuple(item.get("column_names") or ()) == ("storage_key",)
            for item in upload_inspector.get_unique_constraints(UPLOAD_TABLE)
        )
        if not storage_unique:
            if dialect == "sqlite":
                with op.batch_alter_table(UPLOAD_TABLE, recreate="always") as batch_op:
                    batch_op.create_unique_constraint(
                        "uq_artifact_change_uploads_storage_key", ["storage_key"]
                    )
            else:
                op.create_unique_constraint(
                    "uq_artifact_change_uploads_storage_key",
                    UPLOAD_TABLE,
                    ["storage_key"],
                )
        indexes = {
            item.get("name"): item
            for item in upload_inspector.get_indexes(UPLOAD_TABLE)
        }
        expected_indexes = {
            "ix_artifact_change_uploads_change_request_id": (
                "change_request_id",
                False,
            ),
            "ix_artifact_change_uploads_requirement_id": ("requirement_id", False),
            "ix_artifact_change_uploads_token": ("token", True),
            "ix_artifact_change_uploads_uploaded_by": ("uploaded_by", False),
            "ix_artifact_change_uploads_version_id": ("version_id", False),
        }
        for name, (column, unique) in expected_indexes.items():
            existing = indexes.get(name)
            if existing and (
                tuple(existing.get("column_names") or ()) != (column,)
                or bool(existing.get("unique")) != unique
            ):
                raise RuntimeError(f"Artifact-change upload index {name} is incompatible")
            if not existing:
                op.create_index(name, UPLOAD_TABLE, [column], unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table(UPLOAD_TABLE)
    if bind.dialect.name == "sqlite" and not context.is_offline_mode():
        with op.batch_alter_table("version_baselines", recreate="always") as batch_op:
            batch_op.drop_constraint(BASELINE_UNIQUE, type_="unique")
        with op.batch_alter_table("change_requests", recreate="always") as batch_op:
            batch_op.drop_column(EXPECTED_COLUMN)
        return
    op.drop_constraint(BASELINE_UNIQUE, "version_baselines", type_="unique")
    op.drop_column("change_requests", EXPECTED_COLUMN)
