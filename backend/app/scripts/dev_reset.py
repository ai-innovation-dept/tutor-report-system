import os
import sys

from app.database import SessionLocal
from app.models import (
    Assignment,
    ChatMessage,
    ChatRead,
    Invitation,
    LessonReport,
    Notification,
    ReportEvent,
    User,
)
from app.scripts.seed import create_initial_users


if os.getenv("ENVIRONMENT", "development") == "production":
    print("本番環境では実行できません")
    sys.exit(1)


def delete_all(db, model) -> int:
    return db.query(model).delete(synchronize_session=False)


def main() -> None:
    db = SessionLocal()
    try:
        delete_all(db, ChatRead)
        delete_all(db, ChatMessage)
        delete_all(db, Notification)
        delete_all(db, ReportEvent)
        report_count = delete_all(db, LessonReport)
        invitation_count = delete_all(db, Invitation)
        delete_all(db, Assignment)
        user_count = delete_all(db, User)

        create_initial_users(db)
        db.commit()

        print("=== 開発用リセット完了 ===")
        print(f"削除: 報告書 {report_count}件 / ユーザー {user_count}件 / 招待 {invitation_count}件")
        print("作成: 運営3件 / 講師2件")
        print("========================")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
