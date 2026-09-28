"""add question-level answer photo OCR records

Revision ID: 9d2e7f8a1b3c
Revises: f0d4e5a6b7c8
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9d2e7f8a1b3c"
down_revision: Union[str, None] = "f0d4e5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "answer_photos" not in inspector.get_table_names():
        op.create_table(
            "answer_photos",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("batch_id", sa.String(length=64), nullable=False),
            sa.Column("assignment_group_id", sa.String(length=64), nullable=False),
            sa.Column("source_page_id", sa.String(length=64), nullable=False),
            sa.Column("question_id", sa.String(length=64), nullable=False),
            sa.Column("task_id", sa.String(length=64), nullable=True),
            sa.Column("original_name", sa.String(length=255), nullable=False),
            sa.Column("original_storage_key", sa.String(length=500), nullable=False),
            sa.Column("processed_storage_key", sa.String(length=500), nullable=True),
            sa.Column("raw_output_storage_key", sa.String(length=500), nullable=True),
            sa.Column("mime_type", sa.String(length=120), nullable=False),
            sa.Column("extension", sa.String(length=16), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("recognition_mode", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("answer_text", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Numeric(precision=8, scale=5), nullable=True),
            sa.Column("workflow_json", sa.Text(), nullable=True),
            sa.Column("error_code", sa.String(length=80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.Column("updated_at", sa.String(length=40), nullable=False),
            sa.ForeignKeyConstraint(["assignment_group_id"], ["assignment_groups.id"]),
            sa.ForeignKeyConstraint(["batch_id"], ["assignment_batches.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["question_id"], ["batch_questions.id"]),
            sa.ForeignKeyConstraint(["source_page_id"], ["source_pages.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("original_storage_key"),
        )
        op.create_index("ix_answer_photos_task_status", "answer_photos", ["task_id", "status"])
        op.create_index("ix_answer_photos_group_question", "answer_photos", ["assignment_group_id", "question_id"])

    # SQLite 不能直接 ALTER TABLE 增加外键；batch mode 会安全地重建表并保留已有答案版本。
    answer_version_columns = {column["name"] for column in sa.inspect(bind).get_columns("answer_versions")}
    answer_version_foreign_keys = {
        fk.get("constrained_columns", [None])[0]
        for fk in sa.inspect(bind).get_foreign_keys("answer_versions")
    }
    if "answer_photo_id" not in answer_version_columns or "answer_photo_id" not in answer_version_foreign_keys:
        with op.batch_alter_table("answer_versions", recreate="always") as batch_op:
            if "answer_photo_id" not in answer_version_columns:
                batch_op.add_column(sa.Column("answer_photo_id", sa.String(length=64), nullable=True))
            batch_op.create_foreign_key("fk_answer_versions_answer_photo_id", "answer_photos", ["answer_photo_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("answer_versions", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_answer_versions_answer_photo_id", type_="foreignkey")
        batch_op.drop_column("answer_photo_id")
    op.drop_index("ix_answer_photos_group_question", table_name="answer_photos")
    op.drop_index("ix_answer_photos_task_status", table_name="answer_photos")
    op.drop_table("answer_photos")
