"""allow long natural-language rubric instructions

Revision ID: c9e2f1a4b7d8
Revises: b7c8d9e0f1a2
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9e2f1a4b7d8"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("rubric_points") as batch_op:
        batch_op.alter_column(
            "label",
            existing_type=sa.String(length=200),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("rubric_points") as batch_op:
        batch_op.alter_column(
            "label",
            existing_type=sa.Text(),
            type_=sa.String(length=200),
            existing_nullable=False,
        )
