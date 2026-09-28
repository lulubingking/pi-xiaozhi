"""add latest-result lookup index

Revision ID: g1h2i3j4k5l6
Revises: e1f2a3b4c5d6
"""

from typing import Sequence, Union

from alembic import op


revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_grading_results_task_updated",
        "grading_results",
        ["grading_task_id", "updated_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_grading_results_task_updated", table_name="grading_results")
