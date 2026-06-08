"""ユーザーNo（users.user_no）の採番ロジックを集約する。

既存システムのユーザーNoは、ロール区分ごとに番号帯を分けた T プレフィックス形式。
  講師 (tutor)                          : T1001〜  (band 1000)
  保護者 (parent)                       : T2001〜  (band 2000)
  運営スタッフ (受付/再鑑/管理者)        : T3001〜  (band 3000)

講師の番号は既存の tutor_no（T001 / T1001 等）から導出して維持する（例 T003 → T1003）。
物理カラム users.user_no は new_backend のマイグレーション 0002 で追加済み。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User

# 役割（ユーザー管理画面の「役割」列の表示区分）
ROLE_CATEGORY = {
    "tutor": "講師",
    "parent": "保護者",
    "admin_receiver": "運営スタッフ",
    "admin_reviewer": "運営スタッフ",
    "admin_master": "運営スタッフ",
}


def band_for_role(role: str) -> int:
    if role == "tutor":
        return 1000
    if role == "parent":
        return 2000
    return 3000  # 運営スタッフ（受付・再鑑・管理者）


def _seq_in_band(no: str | None, band: int) -> int:
    """user_no 文字列が当該バンドに属していれば連番部分（1〜999）を返す。範囲外は0。"""
    if not no or not no.startswith("T"):
        return 0
    digits = no[1:]
    if not digits.isdigit():
        return 0
    value = int(digits)
    if band <= value < band + 1000:
        return value - band
    return 0


def derive_user_no_from_tutor_no(tutor_no: str | None) -> str | None:
    """講師の tutor_no（T001 / T1001）から banded な user_no を導出する。冪等。"""
    if not tutor_no or not tutor_no.startswith("T"):
        return None
    digits = tutor_no[1:]
    if not digits.isdigit():
        return None
    value = int(digits)
    if value < 1000:
        value += 1000
    return f"T{value}"


def generate_user_no(db: Session, role: str) -> str:
    """当該ロールの番号帯で、既存 user_no と衝突しない次の番号を採番する。"""
    band = band_for_role(role)
    existing = db.scalars(select(User.user_no).where(User.user_no.is_not(None))).all()
    max_seq = max((_seq_in_band(no, band) for no in existing), default=0)
    return f"T{band + max_seq + 1}"


def user_no_for_new_user(db: Session, role: str, tutor_no: str | None = None) -> str:
    """新規ユーザーの user_no を決定する。講師は tutor_no から導出、それ以外は採番。"""
    if role == "tutor":
        derived = derive_user_no_from_tutor_no(tutor_no)
        if derived:
            return derived
    return generate_user_no(db, role)


def assign_missing_user_nos(db: Session) -> int:
    """既存システム(legacy)所属で user_no 未設定のユーザーへ番号を割り当てる（冪等）。

    新システム専用ユーザー（allowed_systems に 'legacy' を含まない）は対象外。
    新システムは独自の数値番号帯（20001〜等）で user_no を採番するため、Tプレフィックスを付けない。
    """
    users = db.scalars(select(User).order_by(User.created_at)).all()
    count = 0
    for user in users:
        if user.user_no:
            continue
        if "legacy" not in (user.allowed_systems or []):
            continue
        user.user_no = user_no_for_new_user(db, user.role, user.tutor_no)
        db.flush()  # 後続の採番が今割り当てた番号を考慮できるようにする
        count += 1
    return count
