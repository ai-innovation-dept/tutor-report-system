"""本番用クリーン投入スクリプト（破壊的）。

既存の全データ（ユーザー・報告書・契約・招待・通知・チャット等）を削除し、
検証用のサンプルユーザーだけの状態にする。スキーマ（テーブル）と Alembic の
バージョン管理テーブル（alembic_version / work_alembic_version）は保持する。

両システム（指導実績=legacy / 業務連絡表=new）は users テーブルを共有し email は一意のため、
1メール=1ユーザー行として、両系で使えるロールを併せ持たせる（例: +school1 は legacy=保護者 /
new=学校）。宛先はすべて実在の Gmail 受信箱（kintaikanri.tutor1@gmail.com）に届く
プラスエイリアスのため、バウンスが発生せず送信者評価を傷つけない。

使い方（本番でのマイグレーション適用後に実行。**全データが消えます**）:
    docker compose exec backend python -m app.scripts.seed_production --yes
"""
import sys

from sqlalchemy import text

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import User

PASSWORD = "Passw0rd!"

# 保持するテーブル（Alembic 管理）。これ以外の public テーブルは全消去する。
_KEEP_TABLES = {"alembic_version", "work_alembic_version"}

_BASE = "kintaikanri.tutor1"
_DOMAIN = "gmail.com"

# (email, 表示名, roles[新系→旧系の順], primary role, user_no, tutor_no)
# allowed_systems は全員 ["legacy", "new"]（両システムで利用可能）。
_SAMPLE_USERS = [
    (f"{_BASE}@{_DOMAIN}",            "講師太郎",   ["tutor"],                    "tutor",        "10001", "10001"),
    (f"{_BASE}+school1@{_DOMAIN}",    "保護者花子", ["school", "parent"],         "school",       "40001", None),
    (f"{_BASE}+office1@{_DOMAIN}",    "受付太郎",   ["office", "admin_receiver"], "office",       "50001", None),
    (f"{_BASE}+sales1@{_DOMAIN}",     "再鑑花子",   ["sales", "admin_reviewer"],  "sales",        "50002", None),
    (f"{_BASE}+master1@{_DOMAIN}",    "管理太郎",   ["admin_master"],             "admin_master", "50003", None),
    (f"{_BASE}+supervisor@{_DOMAIN}", "管責花子",   ["admin_chief"],              "admin_chief",  "90001", None),
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


def _create_sample_users(db) -> None:
    password_hash = hash_password(PASSWORD)
    for email, name, roles, role, user_no, tutor_no in _SAMPLE_USERS:
        db.add(
            User(
                email=email,
                display_name=name,
                role=role,
                roles=roles,
                user_no=user_no,
                tutor_no=tutor_no,
                allowed_systems=["legacy", "new"],
                password_hash=password_hash,
                is_active=True,
                must_change_password=False,
            )
        )


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--yes" not in argv:
        print("!!! 破壊的操作です: 全データを削除し、サンプルユーザーのみの状態にします。")
        print("    実行するには --yes を付けてください:")
        print("      python -m app.scripts.seed_production --yes")
        return
    db = SessionLocal()
    try:
        wiped = _wipe_all_data(db)
        _create_sample_users(db)
        db.commit()
        print(f"wiped tables ({len(wiped)}): {', '.join(wiped)}")
        print(f"created {len(_SAMPLE_USERS)} sample users:")
        for email, name, roles, *_ in _SAMPLE_USERS:
            print(f"  - {email}  {name}  roles={roles}")
        print("seed_production complete")
    finally:
        db.close()


if __name__ == "__main__":
    main()
