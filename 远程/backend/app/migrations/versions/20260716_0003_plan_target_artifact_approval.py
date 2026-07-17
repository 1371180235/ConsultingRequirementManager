"""add annual target and artifact approval workflow

Revision ID: 20260716_0003
Revises: 20260716_0002
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260716_0003"
down_revision: Union[str, Sequence[str], None] = "20260716_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REVIEWER_FK = "fk_deliverables_reviewed_by_users"
APPROVAL_INDEX = "ix_deliverables_approval_status"
NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _column_state(table_name: str) -> dict[str, dict]:
    if context.is_offline_mode():
        return {}
    return {item["name"]: item for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    annual_columns = _column_state("annual_plans")
    if "target" not in annual_columns:
        op.add_column("annual_plans", sa.Column("target", sa.Text(), nullable=True))

    deliverable_columns = _column_state("deliverables")
    additions = {
        "approval_status": sa.Column("approval_status", sa.String(length=20), nullable=True),
        "reviewed_by": sa.Column("reviewed_by", sa.Integer(), nullable=True),
        "reviewed_at": sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        "review_note": sa.Column("review_note", sa.Text(), nullable=True),
    }
    for name, column in additions.items():
        if name not in deliverable_columns:
            op.add_column("deliverables", column)

    op.execute(sa.text("UPDATE annual_plans SET target = '' WHERE target IS NULL"))
    op.execute(sa.text("UPDATE deliverables SET approval_status = 'draft' WHERE approval_status IS NULL"))

    bind = op.get_bind()
    if context.is_offline_mode():
        target_nullable = True
        approval_nullable = True
        has_reviewer_fk = False
        has_approval_index = False
    else:
        inspector = sa.inspect(bind)
        annual_columns = {item["name"]: item for item in inspector.get_columns("annual_plans")}
        deliverable_columns = {item["name"]: item for item in inspector.get_columns("deliverables")}
        target_nullable = bool(annual_columns["target"].get("nullable"))
        approval_nullable = bool(deliverable_columns["approval_status"].get("nullable"))
        has_reviewer_fk = any(
            item.get("constrained_columns") == ["reviewed_by"]
            for item in inspector.get_foreign_keys("deliverables")
        )
        has_approval_index = any(
            item.get("name") == APPROVAL_INDEX
            for item in inspector.get_indexes("deliverables")
        )

    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    if target_nullable:
        with op.batch_alter_table("annual_plans", recreate=recreate) as batch_op:
            batch_op.alter_column("target", existing_type=sa.Text(), nullable=False)
    if approval_nullable or not has_reviewer_fk:
        with op.batch_alter_table(
            "deliverables", recreate=recreate, naming_convention=NAMING_CONVENTION
        ) as batch_op:
            if approval_nullable:
                batch_op.alter_column(
                    "approval_status", existing_type=sa.String(length=20), nullable=False
                )
            if not has_reviewer_fk:
                batch_op.create_foreign_key(
                    REVIEWER_FK,
                    "users",
                    ["reviewed_by"],
                    ["id"],
                    ondelete="SET NULL",
                )
    if not has_approval_index:
        op.create_index(APPROVAL_INDEX, "deliverables", ["approval_status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    op.drop_index(APPROVAL_INDEX, table_name="deliverables")
    with op.batch_alter_table(
        "deliverables", recreate=recreate, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(REVIEWER_FK, type_="foreignkey")
        batch_op.drop_column("review_note")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("approval_status")
    with op.batch_alter_table("annual_plans", recreate=recreate) as batch_op:
        batch_op.drop_column("target")
