"""make saved rubric versions immediately usable

Revision ID: b7c8d9e0f1a2
Revises: 9d2e7f8a1b3c
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "9d2e7f8a1b3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE rubric_versions SET status = 'published' WHERE status = 'draft'"))


def downgrade() -> None:
    # This is a data-normalization migration. Reverting all published versions
    # would also demote versions that were already usable before the migration.
    pass
