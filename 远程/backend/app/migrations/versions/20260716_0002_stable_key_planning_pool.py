"""add stable requirement identity and project-only planning pool

Revision ID: 20260716_0002
Revises: 20260716_0001
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260716_0002"
down_revision: Union[str, Sequence[str], None] = "20260716_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "fk_requirements_annual_plan_id_annual_plans"
NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _annual_plan_fk_name() -> str:
    if context.is_offline_mode():
        return FK_NAME
    for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys("requirements"):
        if foreign_key.get("constrained_columns") == ["annual_plan_id"]:
            return foreign_key.get("name") or FK_NAME
    raise RuntimeError("requirements.annual_plan_id foreign key was not found")


def upgrade() -> None:
    if context.is_offline_mode():
        stable_exists = False
    else:
        stable_exists = "stable_key" in {
            item["name"] for item in sa.inspect(op.get_bind()).get_columns("requirements")
        }
    if not stable_exists:
        op.add_column("requirements", sa.Column("stable_key", sa.String(length=64), nullable=True))
    op.execute(sa.text("UPDATE requirements SET stable_key = code WHERE stable_key IS NULL"))

    bind = op.get_bind()
    if context.is_offline_mode():
        stable_nullable = True
        annual_plan_nullable = False
        has_unique = False
        replace_foreign_key = True
    else:
        inspector = sa.inspect(bind)
        columns = {item["name"]: item for item in inspector.get_columns("requirements")}
        stable_nullable = bool(columns["stable_key"].get("nullable"))
        annual_plan_nullable = bool(columns["annual_plan_id"].get("nullable"))
        unique_columns = {
            tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints("requirements")
        }
        has_unique = ("version_id", "stable_key") in unique_columns
        annual_plan_fk = next(
            (
                item
                for item in inspector.get_foreign_keys("requirements")
                if item.get("constrained_columns") == ["annual_plan_id"]
            ),
            None,
        )
        ondelete = str((annual_plan_fk or {}).get("options", {}).get("ondelete", "")).upper()
        replace_foreign_key = ondelete != "SET NULL"

    if not any((stable_nullable, not annual_plan_nullable, not has_unique, replace_foreign_key)):
        return
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "requirements", recreate=recreate, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        if replace_foreign_key:
            batch_op.drop_constraint(_annual_plan_fk_name(), type_="foreignkey")
        if stable_nullable:
            batch_op.alter_column("stable_key", existing_type=sa.String(length=64), nullable=False)
        if not annual_plan_nullable:
            batch_op.alter_column("annual_plan_id", existing_type=sa.Integer(), nullable=True)
        if replace_foreign_key:
            batch_op.create_foreign_key(
                FK_NAME,
                "annual_plans",
                ["annual_plan_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if not has_unique:
            batch_op.create_unique_constraint(
                "uq_requirement_version_stable_key", ["version_id", "stable_key"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "requirements", recreate=recreate, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("uq_requirement_version_stable_key", type_="unique")
        batch_op.drop_constraint(_annual_plan_fk_name(), type_="foreignkey")
        batch_op.alter_column("annual_plan_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            FK_NAME,
            "annual_plans",
            ["annual_plan_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("stable_key")
