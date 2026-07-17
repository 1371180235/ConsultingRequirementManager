"""add persistent role dashboard layouts

Revision ID: 20260716_0004
Revises: 20260716_0003
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260716_0004"
down_revision: Union[str, Sequence[str], None] = "20260716_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "role_dashboard_layouts"
ROLE_INDEX = "ix_role_dashboard_layouts_role"
UPDATED_BY_FK = "fk_role_dashboard_layouts_updated_by_users"
NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
}


def upgrade() -> None:
    bind = op.get_bind()
    table_exists = False
    if not context.is_offline_mode():
        table_exists = TABLE_NAME in sa.inspect(bind).get_table_names()

    if context.is_offline_mode() or not table_exists:
        op.create_table(
            TABLE_NAME,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("component_keys", sa.JSON(), nullable=False),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["updated_by"],
                ["users.id"],
                name=UPDATED_BY_FK,
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(ROLE_INDEX, TABLE_NAME, ["role"], unique=True)
        return

    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns(TABLE_NAME)}
    required_columns = {"id", "role", "component_keys", "updated_by", "updated_at"}
    if missing := required_columns - columns:
        raise RuntimeError(
            "Partially applied dashboard-layout migration is missing columns: "
            + ", ".join(sorted(missing))
        )
    foreign_key = next(
        (
            item
            for item in inspector.get_foreign_keys(TABLE_NAME)
            if item.get("constrained_columns") == ["updated_by"]
        ),
        None,
    )
    if foreign_key:
        ondelete = str(foreign_key.get("options", {}).get("ondelete", "")).upper()
        if foreign_key.get("referred_table") != "users" or ondelete != "SET NULL":
            raise RuntimeError("Dashboard-layout updated_by foreign key is incompatible")
    else:
        recreate = "always" if bind.dialect.name == "sqlite" else "auto"
        with op.batch_alter_table(
            TABLE_NAME, recreate=recreate, naming_convention=NAMING_CONVENTION
        ) as batch_op:
            batch_op.create_foreign_key(
                UPDATED_BY_FK,
                "users",
                ["updated_by"],
                ["id"],
                ondelete="SET NULL",
            )
    indexes = {item.get("name"): item for item in inspector.get_indexes(TABLE_NAME)}
    role_index = indexes.get(ROLE_INDEX)
    if role_index and not role_index.get("unique"):
        raise RuntimeError("Dashboard-layout role index must be unique")
    if not role_index:
        op.create_index(ROLE_INDEX, TABLE_NAME, ["role"], unique=True)


def downgrade() -> None:
    op.drop_index(ROLE_INDEX, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
