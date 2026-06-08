"""既存ユーザーへ user_no（T1xxx=講師 / T2xxx=保護者 / T3xxx=運営スタッフ）を割り当てる保守スクリプト。

冪等: 既に user_no があるユーザーは変更しない。未設定のユーザーのみ、ロール区分の
番号帯で採番する（講師は既存 tutor_no から導出して番号を維持）。
本番デプロイ時に1回実行すれば、全ユーザーへNoが付与される。
"""
from app.database import SessionLocal
from app.services.user_no_service import assign_missing_user_nos


def main() -> None:
    db = SessionLocal()
    try:
        assigned = assign_missing_user_nos(db)
        db.commit()
        print(f"user_no を割り当てたユーザー: {assigned} 件")
    finally:
        db.close()


if __name__ == "__main__":
    main()
