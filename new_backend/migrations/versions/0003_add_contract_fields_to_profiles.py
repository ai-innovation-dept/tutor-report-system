"""add contract fields to work_assignment_profiles (第1弾 契約管理)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-04

work_assignment_profiles に契約情報を追加する。
- tutor_id / school_id: NOT NULL（既存行は assignment から backfill）
- UNIQUE(tutor_id, school_id)
- 既存カラム（assignment_id 等）は変更しない
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 講師・学校（まずは NULL 許可で追加し、既存行を backfill してから NOT NULL 化）
    op.add_column("work_assignment_profiles", sa.Column("tutor_id", sa.Uuid(), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("school_id", sa.Uuid(), nullable=True))

    # 既存行があれば assignment から講師・学校を補完する
    op.execute(
        """
        UPDATE work_assignment_profiles p
        SET tutor_id = a.tutor_id, school_id = a.parent_id
        FROM assignments a
        WHERE p.assignment_id = a.id
        """
    )
    # 学校が補完できなかった行は契約として不完全なため削除（運用上ほぼ存在しない想定）
    op.execute("DELETE FROM work_assignment_profiles WHERE tutor_id IS NULL OR school_id IS NULL")

    op.alter_column("work_assignment_profiles", "tutor_id", nullable=False)
    op.alter_column("work_assignment_profiles", "school_id", nullable=False)
    op.create_foreign_key(
        "fk_work_profile_tutor", "work_assignment_profiles", "users", ["tutor_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_work_profile_school", "work_assignment_profiles", "users", ["school_id"], ["id"]
    )
    op.create_index("ix_work_assignment_profiles_tutor_id", "work_assignment_profiles", ["tutor_id"])
    op.create_index("ix_work_assignment_profiles_school_id", "work_assignment_profiles", ["school_id"])
    op.create_unique_constraint(
        "uq_work_profile_tutor_school", "work_assignment_profiles", ["tutor_id", "school_id"]
    )

    # 契約詳細
    op.add_column("work_assignment_profiles", sa.Column("customer_id", sa.String(50), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("our_staff", sa.String(100), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("contract_start", sa.Date(), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("contract_end", sa.Date(), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("monthly_minutes", sa.Integer(), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("weekly_lessons", sa.Integer(), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("shift_note", sa.Text(), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("work_content", sa.Text(), nullable=True))
    op.add_column(
        "work_assignment_profiles",
        sa.Column("has_scoring", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for i in range(1, 6):
        op.add_column("work_assignment_profiles", sa.Column(f"task_name_{i}", sa.String(100), nullable=True))
        op.add_column("work_assignment_profiles", sa.Column(f"task_id_{i}", sa.String(50), nullable=True))
        op.add_column("work_assignment_profiles", sa.Column(f"contract_id_{i}", sa.String(50), nullable=True))


def downgrade() -> None:
    for i in range(1, 6):
        op.drop_column("work_assignment_profiles", f"contract_id_{i}")
        op.drop_column("work_assignment_profiles", f"task_id_{i}")
        op.drop_column("work_assignment_profiles", f"task_name_{i}")
    op.drop_column("work_assignment_profiles", "has_scoring")
    op.drop_column("work_assignment_profiles", "work_content")
    op.drop_column("work_assignment_profiles", "shift_note")
    op.drop_column("work_assignment_profiles", "weekly_lessons")
    op.drop_column("work_assignment_profiles", "monthly_minutes")
    op.drop_column("work_assignment_profiles", "contract_end")
    op.drop_column("work_assignment_profiles", "contract_start")
    op.drop_column("work_assignment_profiles", "our_staff")
    op.drop_column("work_assignment_profiles", "customer_id")
    op.drop_constraint("uq_work_profile_tutor_school", "work_assignment_profiles", type_="unique")
    op.drop_index("ix_work_assignment_profiles_school_id", table_name="work_assignment_profiles")
    op.drop_index("ix_work_assignment_profiles_tutor_id", table_name="work_assignment_profiles")
    op.drop_constraint("fk_work_profile_school", "work_assignment_profiles", type_="foreignkey")
    op.drop_constraint("fk_work_profile_tutor", "work_assignment_profiles", type_="foreignkey")
    op.drop_column("work_assignment_profiles", "school_id")
    op.drop_column("work_assignment_profiles", "tutor_id")
