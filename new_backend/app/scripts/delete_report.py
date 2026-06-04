"""指定した生徒名・対象月の work_reports を関連データごと削除する保守用スクリプト。

進捗パイプラインに残った不要な報告データ（例: 差戻し中のまま放置されたテストデータ）を
安全に削除する。削除対象: work_chat_reads / work_chat_messages /
work_report_events / work_notifications / work_reports。

Usage:
    # 対象の確認のみ（削除しない）
    docker compose exec new_backend python -m app.scripts.delete_report 生徒一郎 2026-06

    # 実際に削除する
    docker compose exec new_backend python -m app.scripts.delete_report 生徒一郎 2026-06 --yes
"""
import argparse

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models.shared import Assignment, User
from app.models.work import (
    WorkChatMessage,
    WorkChatRead,
    WorkNotification,
    WorkReport,
    WorkReportEvent,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="work_reportsの削除")
    parser.add_argument("student_name", help="assignmentsの生徒名（完全一致）")
    parser.add_argument("target_month", help="対象月 YYYY-MM")
    parser.add_argument("--yes", action="store_true", help="実際に削除する（省略時は確認のみ）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        reports = db.scalars(
            select(WorkReport)
            .join(Assignment, Assignment.id == WorkReport.assignment_id)
            .where(
                Assignment.student_name == args.student_name,
                WorkReport.target_month == args.target_month,
            )
        ).all()
        if not reports:
            print(f"該当する報告はありません: {args.student_name} / {args.target_month}")
            return

        for report in reports:
            tutor = db.get(User, report.tutor_id)
            print(
                f"report_id={report.id} status={report.status} "
                f"tutor={tutor.display_name if tutor else '-'} month={report.target_month}"
            )

        if not args.yes:
            print(f"\n{len(reports)}件が対象です。削除するには --yes を付けて再実行してください。")
            return

        report_ids = [report.id for report in reports]
        message_ids = db.scalars(
            select(WorkChatMessage.id).where(WorkChatMessage.report_id.in_(report_ids))
        ).all()
        if message_ids:
            db.execute(delete(WorkChatRead).where(WorkChatRead.message_id.in_(message_ids)))
            db.execute(delete(WorkChatMessage).where(WorkChatMessage.id.in_(message_ids)))
        db.execute(delete(WorkReportEvent).where(WorkReportEvent.report_id.in_(report_ids)))
        db.execute(delete(WorkNotification).where(WorkNotification.report_id.in_(report_ids)))
        db.execute(delete(WorkReport).where(WorkReport.id.in_(report_ids)))
        db.commit()
        print(f"{len(reports)}件の報告と関連データを削除しました。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
