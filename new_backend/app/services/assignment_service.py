"""紐付け（assignment）の解決・作成ロジック。

新システムの assignment は (講師, 学校) ごとに1件。講師が学校を選んで報告書を作る
フロー（for-school）と、経理が契約を登録するフローの双方から共有する。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shared import Assignment, User


def get_or_create_new_assignment(db: Session, tutor: User, school: User) -> Assignment:
    """(講師, 学校) の新システム紐付けを取得、無ければ作成して返す。

    student_name には学校名を入れる（新システムは学校単位で運用するため）。
    無効化されていた場合は再有効化する。commit は呼び出し元の責任。
    """
    existing = db.scalar(
        select(Assignment).where(
            Assignment.tutor_id == tutor.id,
            Assignment.parent_id == school.id,
            Assignment.system_type == "new",
        )
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
        return existing

    assignment = Assignment(
        tutor_id=tutor.id,
        parent_id=school.id,
        student_name=school.display_name,
        system_type="new",
        is_active=True,
    )
    db.add(assignment)
    db.flush()
    return assignment
