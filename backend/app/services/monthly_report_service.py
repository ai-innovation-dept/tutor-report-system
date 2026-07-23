# === 指導月報 START ===
"""指導月報（原本: docs/イスト勤怠レポート for 代々木進学会/原本_月報.pdf）のサービス層。

- 担当（assignment）×対象月で1件。講師が承認依頼前に作成・更新する。
- 承認依頼（保護者への提出）時に月報の存在＋必須項目（次月に向けての問題点と対策のみ。
  学年ほか他の項目は任意＝改修 202607231755 ④）を強制する。
- 保護者は承認時に保護者記入欄（parent_note）を記入する（月報が存在する月は必須。講師は記入不可）。
- form_data の構造・教科の並びは本ファイルの定数が唯一の定義源（画面・PDFもこれに従う）。
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, LessonReport, MonthlyReport, ReportStatus

# 最近のテスト結果の教科の並び（原本準拠）。
# 模試/実力テスト = 国語・算数・2科・社会・理科・4科 ／ 学校 = 英語・算数・国語・社会・理科
MOCK_SUBJECTS = ["国語", "算数", "2科", "社会", "理科", "4科"]
SCHOOL_SUBJECTS = ["英語", "算数", "国語", "社会", "理科"]

# 講師が編集できる（＝承認依頼前の）報告書ステータス。
# 対象月の報告書がすべてこの状態（または報告書なし）の間だけ月報を作成・更新できる。
_EDITABLE_REPORT_STATUSES = {ReportStatus.draft.value, ReportStatus.returned_to_tutor.value}

_ISSUE_ROWS = 5  # 次月に向けての問題点と対策 1〜5
_SCHOOL_ROWS = 5  # 現時点での志望校 1〜5


def _text(value, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _int_or_none(value, minimum: int, maximum: int) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if minimum <= number <= maximum else None


def _text_list(values, rows: int, limit: int = 200) -> list[str]:
    items = list(values) if isinstance(values, (list, tuple)) else []
    return [_text(items[i] if i < len(items) else "", limit) for i in range(rows)]


def _score_list(values, subjects: list[str]) -> list[dict]:
    items = list(values) if isinstance(values, (list, tuple)) else []
    scores: list[dict] = []
    for i in range(len(subjects)):
        row = items[i] if i < len(items) and isinstance(items[i], dict) else {}
        scores.append({"score": _text(row.get("score"), 10), "deviation": _text(row.get("deviation"), 10)})
    return scores


def _day_list(values) -> list[int]:
    items = values if isinstance(values, (list, tuple)) else []
    days = sorted({d for d in (_int_or_none(v, 1, 31) for v in items) if d is not None})
    return days


def _choice(value, allowed: set[str]) -> str:
    text = _text(value, 10)
    return text if text in allowed else ""


def normalize_form_data(raw: dict | None) -> dict:
    """入力 form_data を既知のキー・型へ正規化する（未知キーは破棄・下書き保存は緩く許容）。"""
    src = raw if isinstance(raw, dict) else {}
    mock = src.get("test_mock") if isinstance(src.get("test_mock"), dict) else {}
    school = src.get("test_school") if isinstance(src.get("test_school"), dict) else {}
    retro = src.get("retrospect") if isinstance(src.get("retrospect"), dict) else {}
    late = retro.get("late") if isinstance(retro.get("late"), dict) else {}
    change = retro.get("schedule_change") if isinstance(retro.get("schedule_change"), dict) else {}
    reason = retro.get("change_reason") if isinstance(retro.get("change_reason"), dict) else {}
    makeup = retro.get("makeup") if isinstance(retro.get("makeup"), dict) else {}
    plans_src = makeup.get("plans") if isinstance(makeup.get("plans"), (list, tuple)) else []
    plans = []
    for plan in plans_src[:10]:  # 振替予定は自由に追加できる（保存は最大10組）
        if not isinstance(plan, dict):
            continue
        normalized = {
            "from_month": _int_or_none(plan.get("from_month"), 1, 12),
            "from_day": _int_or_none(plan.get("from_day"), 1, 31),
            "to_month": _int_or_none(plan.get("to_month"), 1, 12),
            "to_day": _int_or_none(plan.get("to_day"), 1, 31),
        }
        if any(v is not None for v in normalized.values()):
            plans.append(normalized)
    return {
        "issues": _text_list(src.get("issues"), _ISSUE_ROWS),
        "target_schools": _text_list(src.get("target_schools"), _SCHOOL_ROWS),
        "test_mock": {
            "name": _text(mock.get("name"), 100),
            "exam_month": _int_or_none(mock.get("exam_month"), 1, 12),
            "exam_day": _int_or_none(mock.get("exam_day"), 1, 31),
            "scores": _score_list(mock.get("scores"), MOCK_SUBJECTS),
        },
        "test_school": {
            "term": _text(school.get("term"), 50),
            "term_type": _choice(school.get("term_type"), {"中間", "期末"}),
            "scores": _score_list(school.get("scores"), SCHOOL_SUBJECTS),
        },
        "lesson_days": _day_list(src.get("lesson_days")),
        "next_month_plan_days": _day_list(src.get("next_month_plan_days")),
        "total_hours": _text(src.get("total_hours"), 10),
        "retrospect": {
            "late": {
                "answer": _choice(late.get("answer"), {"A", "B"}),
                "count": _text(late.get("count"), 10),
                "informed": _choice(late.get("informed"), {"a", "b"}),
            },
            "schedule_change": {
                "answer": _choice(change.get("answer"), {"A", "B"}),
                "count": _text(change.get("count"), 10),
                "informed": _choice(change.get("informed"), {"a", "b"}),
            },
            "change_reason": {
                "answer": _choice(reason.get("answer"), {"A", "B"}),
                "reason": _text(reason.get("reason"), 500),
            },
            "makeup": {"answer": _choice(makeup.get("answer"), {"A", "B", "C"}), "plans": plans},
        },
        "notes": _text(src.get("notes"), 2000),
    }


def get_monthly_report(db: Session, assignment_id, target_month: str) -> MonthlyReport | None:
    return db.scalar(
        select(MonthlyReport).where(
            MonthlyReport.assignment_id == assignment_id,
            MonthlyReport.target_month == target_month,
        )
    )


def month_reports(db: Session, assignment_id, target_month: str) -> list[LessonReport]:
    return list(
        db.scalars(
            select(LessonReport).where(
                LessonReport.assignment_id == assignment_id,
                LessonReport.target_month == target_month,
            )
        )
    )


def editable_state(reports: list[LessonReport]) -> tuple[bool, str | None]:
    """講師が月報を編集できるか（＝対象月が承認依頼前か）を返す。

    対象月の報告書がすべて下書き・差戻し中（または報告書なし）の間のみ編集できる。
    承認依頼後はロックし、差戻しで報告書が講師へ戻れば再び編集できる。
    """
    locked = [r for r in reports if r.status not in _EDITABLE_REPORT_STATUSES]
    if not locked:
        return True, None
    if all(r.status == ReportStatus.closed.value for r in locked):
        return False, "クローズ済みの月のため編集できません"
    return False, "承認依頼済みのため編集できません（差戻しされると再び編集できます）"


def missing_required_reason(monthly: MonthlyReport | None) -> str | None:
    """承認依頼を止める理由（月報の未作成・必須項目の未入力）。問題なければ None。

    必須項目 = 次月に向けての問題点と対策（1件以上）のみ。学年ほか他の項目は任意
    （改修 202607231755 ④）。
    """
    if monthly is None:
        return "指導月報が未作成です"
    issues = (monthly.form_data or {}).get("issues") or []
    if not any(_text(issue) for issue in issues):
        return "指導月報の「次月に向けての問題点と対策」が未入力です"
    return None


def assert_monthly_reports_ready(db: Session, reports: list[LessonReport]) -> None:
    """承認依頼（保護者への提出）前ガード。対象の担当×月ごとに月報の完成を検証する。

    月報が未作成、または必須項目（問題点と対策）が未入力の場合は 422 で提出をブロックする。
    """
    for assignment_id, target_month in sorted(
        {(r.assignment_id, r.target_month) for r in reports}, key=lambda key: str(key)
    ):
        monthly = get_monthly_report(db, assignment_id, target_month)
        reason = missing_required_reason(monthly)
        if reason:
            assignment = db.get(Assignment, assignment_id)
            student = assignment.student_name if assignment else "生徒"
            year, month = target_month.split("-")
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{reason}（{student}さん・{year}年{int(month)}月分）。"
                    "「月報作成」画面で入力してから承認依頼してください。"
                ),
            )


def apply_parent_note(db: Session, reports: list[LessonReport], parent: "object", parent_note: str | None) -> None:
    """保護者承認時の保護者記入欄（ご要望/連絡事項）を検証・保存する。

    月報が存在する担当×月は記入必須（新規入力または入力済み）。未入力なら 422 で承認をブロックする。
    月報が存在しない月（本機能リリース前に提出済みの月など）は従来どおり承認できる。
    """
    note = _text(parent_note, 2000)
    for assignment_id, target_month in {(r.assignment_id, r.target_month) for r in reports}:
        monthly = get_monthly_report(db, assignment_id, target_month)
        if monthly is None:
            continue
        if note:
            monthly.parent_note = note
            monthly.parent_note_by = parent.id
            monthly.parent_note_at = datetime.now(timezone.utc)
        elif not _text(monthly.parent_note):
            raise HTTPException(
                status_code=422,
                detail="指導月報の保護者記入欄（ご要望/連絡事項）を入力してから承認してください。",
            )
# === 指導月報 END ===
