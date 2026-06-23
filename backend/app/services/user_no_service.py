"""ユーザーID（users.user_no）の採番ロジックを集約する。

採番ポリシー（数値5桁・先頭桁がロール区分）:
  講師 (tutor)                          : 1nnnn  (10001〜)  ※新旧システム共通の通し番号
  保護者 (parent)                       : 2nnnn  (20001〜)
  受付・再鑑・管理者 (admin_*)           : 3nnnn  (30001〜)
  管理責任者 (admin_chief)              : 9nnnn  (90001〜)
  （新システムの 学校=4nnnn / 事務・営業・経理=5nnnn は new_backend 側で採番）

講師は user_no と tutor_no を同値（数値）に揃える。リレーションは全て UUID(id) で結合するため、
番号の振り直しは参照整合性に影響しない。物理カラム users.user_no は migration 0002 で追加済み。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invitation, User

# 役割（ユーザー管理画面の「役割」列の表示区分）
ROLE_CATEGORY = {
    "tutor": "講師",
    "parent": "保護者",
    "admin_receiver": "運営スタッフ",
    "admin_reviewer": "運営スタッフ",
    "admin_master": "運営スタッフ",
    "admin_chief": "管理責任者",
}

# ロール → 番号帯の先頭（10000=講師 / 20000=保護者 / 30000=運営スタッフ / 90000=管理責任者）
_BAND = {
    "tutor": 10000,
    "parent": 20000,
    "admin_receiver": 30000,
    "admin_reviewer": 30000,
    "admin_master": 30000,
    "admin_chief": 90000,
}


def band_for_role(role: str) -> int:
    return _BAND.get(role, 30000)


def _seq_in_band(no: str | None, band: int) -> int:
    """user_no/tutor_no 文字列が当該バンド(band+1〜band+9999)なら連番部分を返す。範囲外は0。"""
    s = str(no) if no else ""
    if not s.isdigit():
        return 0
    value = int(s)
    if band < value < band + 10000:
        return value - band
    return 0


def generate_user_no(db: Session, role: str) -> str:
    """当該ロールの番号帯で「未使用の最小番号」を採番する（新旧システム共通の統一ポリシー）。

    帯内で歯抜けになっている若い番号があれば優先して埋める（max+1 ではない）。
    削除済み（ソフトデリート）ユーザーのNoは解放済みとして扱い、再利用の対象に含める
    （＝「使用済み」集合に入れない＝即・再利用可能）。承認履歴等はソフトデリートで保持される。
    未受諾招待の予約番号は使用済みとして扱う。
    ※ new_backend/app/services/user_service.generate_user_no と同一方針。変更時は両方を更新すること。
    """
    band = band_for_role(role)
    # 削除済みユーザーのNoは予約しない（即・再利用可能にする）。有効ユーザーと未受諾招待のみ「使用済み」。
    candidates: list[str | None] = list(
        db.scalars(select(User.user_no).where(User.user_no.is_not(None), User.deleted_at.is_(None))).all()
    )
    candidates += list(
        db.scalars(select(User.tutor_no).where(User.tutor_no.is_not(None), User.deleted_at.is_(None))).all()
    )
    candidates += list(
        db.scalars(
            select(Invitation.tutor_no).where(
                Invitation.tutor_no.is_not(None),
                Invitation.accepted_at.is_(None),
            )
        ).all()
    )
    used = {seq for seq in (_seq_in_band(no, band) for no in candidates) if seq}
    # 帯の先頭(連番1)から走査し、未使用の最小番号を返す。
    seq = 1
    while seq in used:
        seq += 1
    return str(band + seq)


def user_no_for_new_user(db: Session, role: str, tutor_no: str | None = None) -> str:
    """新規ユーザーの user_no を決定する。講師は事前採番済みの数値 tutor_no があれば流用。"""
    if role == "tutor" and tutor_no and str(tutor_no).isdigit():
        return str(tutor_no)
    return generate_user_no(db, role)


def assign_missing_user_nos(db: Session) -> int:
    """既存システム(legacy)所属で user_no 未設定のユーザーへ番号を割り当てる（冪等）。

    新システム専用ユーザー（allowed_systems に 'legacy' を含まない）は対象外。
    講師は tutor_no も user_no と同値（数値）に揃える。
    """
    users = db.scalars(select(User).order_by(User.created_at)).all()
    count = 0
    for user in users:
        if user.user_no:
            continue
        if "legacy" not in (user.allowed_systems or []):
            continue
        user.user_no = user_no_for_new_user(db, user.role, user.tutor_no)
        if user.role == "tutor":
            user.tutor_no = user.user_no
        db.flush()  # 後続の採番が今割り当てた番号を考慮できるようにする
        count += 1
    return count
