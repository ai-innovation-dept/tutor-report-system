"""検証環境用のユーザー初期化スクリプト（破壊的）。

全データ（ユーザー・報告書・契約・招待・通知・チャット等）を削除し、下記の指定ユーザー
だけの状態にする。スキーマと Alembic バージョン管理テーブルは保持する。

両システム（指導実績=legacy / 業務連絡表=new）は users テーブルを共有し email は一意。
本スクリプトの各アカウントは **単一ロール・単一システム**（allowed_systems が legacy か new の
どちらか一方）なので、ログイン時にロール選択画面が出ず、他システムのロール混在による不具合も
起きない。パスワードは Passw0rd!（must_change_password=False＝初回変更を求めない）。

No(user_no) は各ロール帯の若い順に固定割当：
  講師=1nnnn / 保護者=2nnnn / 受付・再鑑=3nnnn / 学校=4nnnn / 事務・営業=5nnnn

使い方（本番でのマイグレーション適用後に実行。**全データが消えます**）:
    docker compose exec backend python -m app.scripts.seed_env_users --yes
"""
import sys

from sqlalchemy import text

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import User

PASSWORD = "Passw0rd!"

# 保持するテーブル（Alembic 管理）。これ以外の public テーブルは全消去する。
_KEEP_TABLES = {"alembic_version", "work_alembic_version"}

# (email, 表示名, roles, role, allowed_systems, user_no, tutor_no)
_USERS = [
    # --- 指導実績報告システム（legacy） ---
    ("0b036500.nxtech.co.jp@jp.teams.ms", "再鑑花子",   ["admin_reviewer"], "admin_reviewer", ["legacy"], "30001", None),
    ("a3f2c6d3.nxtech.co.jp@jp.teams.ms", "受付花子",   ["admin_receiver"], "admin_receiver", ["legacy"], "30002", None),
    ("3972054a.nxtech.co.jp@jp.teams.ms", "保護者花子", ["parent"],         "parent",         ["legacy"], "20001", None),
    ("1bdfc1de.nxtech.co.jp@jp.teams.ms", "講師花子",   ["tutor"],          "tutor",          ["legacy"], "10001", "10001"),
    # --- 業務連絡表システム（new） ---
    ("21009ee9.nxtech.co.jp@jp.teams.ms", "営業太郎",   ["sales"],          "sales",          ["new"],    "50001", None),
    ("s.ohashi2@nxtech.co.jp",            "大橋悟史",   ["office"],         "office",         ["new"],    "50002", None),
    ("s.takeda@emps.jp",                  "武田 州平",  ["school"],         "school",         ["new"],    "40001", None),
    ("s.takeda@nxtech.co.jp",             "武田 州平2", ["tutor"],          "tutor",          ["new"],    "10002", "10002"),
]


def _wipe_all_data(db) -> list[str]:
    """Alembic バージョン以外の全 public テーブルを TRUNCATE する（FK は CASCADE）。"""
    tables = db.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    ).scalars().all()
    targets = sorted(t for t in tables if t not in _KEEP_TABLES)
    if targets:
        joined = ", ".join(f'"{t}"' for t in targets)
        db.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    return targets


def _create_users(db) -> None:
    password_hash = hash_password(PASSWORD)
    for email, name, roles, role, allowed_systems, user_no, tutor_no in _USERS:
        db.add(
            User(
                email=email,
                display_name=name,
                role=role,
                roles=roles,
                user_no=user_no,
                tutor_no=tutor_no,
                allowed_systems=allowed_systems,
                password_hash=password_hash,
                is_active=True,
                must_change_password=False,
            )
        )


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--yes" not in argv:
        print("!!! 破壊的操作です: 全データを削除し、指定ユーザーのみの状態にします。")
        print("    実行するには --yes を付けてください:")
        print("      python -m app.scripts.seed_env_users --yes")
        return
    db = SessionLocal()
    try:
        wiped = _wipe_all_data(db)
        _create_users(db)
        db.commit()
        print(f"wiped tables ({len(wiped)}): {', '.join(wiped)}")
        print(f"created {len(_USERS)} users:")
        for email, name, roles, role, systems, user_no, _ in _USERS:
            print(f"  - No={user_no:<6} {email}  {name}  role={role}  systems={systems}")
        print("seed_env_users complete")
    finally:
        db.close()


if __name__ == "__main__":
    main()
