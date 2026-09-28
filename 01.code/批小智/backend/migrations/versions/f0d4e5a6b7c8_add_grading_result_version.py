"""add optimistic-lock version to grading results

Revision ID: f0d4e5a6b7c8
Revises: c184af109b7f
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0d4e5a6b7c8"
down_revision: Union[str, None] = "c184af109b7f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "grading_results",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("grading_results", "version")
