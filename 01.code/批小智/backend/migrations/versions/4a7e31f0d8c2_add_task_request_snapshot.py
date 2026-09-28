"""add task request snapshot

Revision ID: 4a7e31f0d8c2
Revises: 2bcb2dd21e29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4a7e31f0d8c2"
down_revision: Union[str, None] = "2bcb2dd21e29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("request_hash", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("options_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "options_json")
    op.drop_column("tasks", "request_hash")
