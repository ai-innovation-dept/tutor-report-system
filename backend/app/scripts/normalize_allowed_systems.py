"""既存ユーザーの allowed_systems を安全に正規化する一回限りの保守スクリプト。

背景:
    両システムは共有 `users` テーブルを参照し、所属は `allowed_systems` を唯一の基準とする。
    ログイン認可・ユーザー一覧の両方がこの値に依存するため、値が未設定(NULL)のままだと
    そのユーザーはどちらのシステムにもログインできず、一覧にも出てこなくなる。
    本番DBはリセットできないため、デプロイ時にこのスクリプトで既存ユーザーを補正する。

方針（冪等・非破壊）:
    - 既に値があるユーザーは尊重する（新システム専用ユーザーに legacy を足す等はしない）。
      ※新システムは登録時に必ず allowed_systems を設定するため、NULL は「既存システム由来」と確定できる。
    - 値が無い(NULL/空)ユーザーのみ、ロールから所属を推定する。
      legacy ロール(tutor/parent/admin_receiver/admin_reviewer) → "legacy"
      new ロール(school/sales/office)                          → "new"
      判定不能                                                  → "legacy"（安全側）
    - admin_master は常に両システム所属を保証する（ポリシー）。
    - 何度実行しても同じ結果になる。
"""
from sqlalchemy import select

from app.database import SessionLocal
from app.models import User

LEGACY_ROLES = {"tutor", "parent", "admin_receiver", "admin_reviewer"}
NEW_ROLES = {"school", "sales", "office"}


def _roles_of(user: User) -> set[str]:
    return set(user.roles or ([user.role] if user.role else []))


def desired_systems(user: User) -> list[str]:
    roles = _roles_of(user)
    existing = list(user.allowed_systems or [])

    if existing:
        # 既存値は尊重する（誤って権限を増やさない）。
        result = set(existing)
    else:
        # NULL/空のみロールから推定。
        result: set[str] = set()
        if roles & LEGACY_ROLES:
            result.add("legacy")
        if roles & NEW_ROLES:
            result.add("new")
        if not result:
            result.add("legacy")

    # ポリシー: admin_master は常に両システム。
    if "admin_master" in roles:
        result |= {"legacy", "new"}

    # 決定論的な順序で返す。
    return [s for s in ("legacy", "new") if s in result]


def main() -> None:
    db = SessionLocal()
    try:
        users = db.scalars(select(User)).all()
        changed = 0
        null_before = 0
        for user in users:
            if not user.allowed_systems:
                null_before += 1
            target = desired_systems(user)
            if set(user.allowed_systems or []) != set(target):
                user.allowed_systems = target
                changed += 1
        db.commit()
        print(f"対象ユーザー: {len(users)} 件")
        print(f"allowed_systems 未設定だった: {null_before} 件")
        print(f"補正したユーザー: {changed} 件")
    finally:
        db.close()


if __name__ == "__main__":
    main()
