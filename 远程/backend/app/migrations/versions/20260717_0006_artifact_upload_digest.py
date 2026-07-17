"""record staged artifact upload digests

Revision ID: 20260717_0006
Revises: 20260716_0005
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260717_0006"
down_revision: Union[str, Sequence[str], None] = "20260716_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMN = "sha256_hex"
TABLE = "artifact_change_uploads"


def upgrade() -> None:
    bind = op.get_bind()
    offline = context.is_offline_mode()
    columns: set[str] = set()
    if not offline:
        inspector = sa.inspect(bind)
        columns = {item["name"] for item in inspector.get_columns(TABLE)}
    if offline or COLUMN not in columns:
        op.add_column(
            TABLE,
            sa.Column(COLUMN, sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not context.is_offline_mode():
        with op.batch_alter_table(TABLE, recreate="always") as batch_op:
            batch_op.drop_column(COLUMN)
        return
    op.drop_column(TABLE, COLUMN)
