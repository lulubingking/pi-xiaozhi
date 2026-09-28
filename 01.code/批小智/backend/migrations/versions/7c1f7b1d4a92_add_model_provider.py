"""add generic model provider to app settings

Revision ID: 7c1f7b1d4a92
Revises: 4a7e31f0d8c2
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c1f7b1d4a92"
down_revision: Union[str, None] = "4a7e31f0d8c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("model_provider", sa.String(length=120), nullable=False, server_default="DeepSeek"),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "model_provider")
