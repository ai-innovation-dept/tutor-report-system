"""契約管理番号（contract_no）の発番（202607170952）。

契約（work_assignment_profiles）の作成順に 1 から連番で発番する（現在の最大値 + 1）。
途中の契約を物理削除してできた欠番は再利用しない（ユーザーNoの最小空き番号再利用＝案Bとは
方針が異なる管理番号）。なお番号が最大の契約（＝直近作成分）を物理削除した場合に限り、
最大値+1 の性質上その番号が次の新規契約へ再割当てされる。

契約を新規作成するすべての経路（契約管理の新規登録・CSV取込のupsert新規・
/api/w/admin/profiles）で本関数を呼ぶこと。更新（upsert既存・PATCH）では再発番しない。
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.work import WorkAssignmentProfile


def issue_contract_no(db: Session) -> int:
    """次の契約管理番号を返す（現在の最大値+1）。

    CSV取込のように1トランザクション内で複数契約を作成する場合でも連番になるよう、
    採番前に flush して同一セッション内の未確定 INSERT/UPDATE を max 計算へ反映する。
    """
    db.flush()
    current_max = db.scalar(select(func.max(WorkAssignmentProfile.contract_no)))
    return int(current_max or 0) + 1
