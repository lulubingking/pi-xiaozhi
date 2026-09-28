"""persist confirmed question layout templates

Revision ID: e1f2a3b4c5d6
Revises: c9e2f1a4b7d8
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "c9e2f1a4b7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_layouts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("source_page_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="confirmed"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_pages_json", sa.Text(), nullable=False),
        sa.Column("regions_json", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["assignment_batches.id"]),
        sa.ForeignKeyConstraint(["source_page_id"], ["source_pages.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", name="uq_question_layouts_batch"),
    )
    op.create_index("ix_question_layouts_batch_status", "question_layouts", ["batch_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_question_layouts_batch_status", table_name="question_layouts")
    op.drop_table("question_layouts")
