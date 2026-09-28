"""add question paper role and question prompts

Revision ID: c184af109b7f
Revises: 7c1f7b1d4a92
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c184af109b7f"
down_revision: Union[str, None] = "7c1f7b1d4a92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "source_files",
        sa.Column("file_role", sa.String(length=32), nullable=False, server_default="student_work"),
    )
    op.add_column("batch_questions", sa.Column("question_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("batch_questions", "question_prompt")
    op.drop_column("source_files", "file_role")
