"""学校の締め日設定CSVの一括エクスポート/取り込み（改修依頼 202607161332）。

ユーザーCSV（user_import_service）・契約CSVと同じ方針:
- CSVはUTF-8(BOM付き)で出力しExcelでの文字化けを防ぐ。取り込みはUTF-8/Shift-JISを自動判定。
- 照合キー: 学校No（user_no・schoolロールのユーザー）。学校Noが先頭#、または全列空白の行は
  コメント行として取り込み対象外。
- 1件でも検証エラーがあれば全件中止（何も保存しない）。

行の単位は「学校×対象年」（同じ学校の複数年を複数行で指定できる）。
- 各月列（1月〜12月）はその月の締め日を「日」（例: 25）または「対象月内の日付」
  （YYYY-MM-DD / YYYY/M/D）で指定する。**空欄はその月の締め日を削除**
  （エクスポート→編集→取り込みの往復で、ファイルの内容がそのまま反映される）。
- 早期チェック（ON/OFF）・通知日数(日前)は学校単位の設定。空欄なら現状維持。
  同じ学校の複数行で異なる値を指定した場合はエラー。
- 締め日の変更は画面と同じく送信済みガードを解除する（save_school_settings 経由）。
"""
import calendar
import csv
import io
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shared import User
from app.services import school_deadline_service

# CSVの列見出し（単一の定義元）。エクスポート出力・取り込み解析の双方で使用する。
NO = "学校No"  # 照合キー（user_no・schoolロール）
NAME_REF = "学校名(参考)"  # 出力のみ・取り込み時は無視
YEAR = "対象年"  # 締め日12ヶ月分の対象年（例: 2026）
EARLY = "早期チェック"  # ON/OFF。空欄=現状維持
DAYS = "通知日数(日前)"  # 0〜60。空欄=現状維持
MONTH_COLUMNS = [f"{month}月" for month in range(1, 13)]

_TRUE_VALUES = {"on", "true", "1"}
_FALSE_VALUES = {"off", "false", "0"}
_DAY_RE = re.compile(r"^\d{1,2}$")
_DATE_RE = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$")


def headers() -> list[str]:
    return [NO, NAME_REF, YEAR, EARLY, DAYS, *MONTH_COLUMNS]


def build_export_csv(db: Session, schools: list[User], year: int) -> bytes:
    """学校ごとの締め日設定（指定年）を UTF-8(BOM) のCSVで返す。

    行は学校×対象年。各月列は締め日の「日」を出力する（未設定は空欄）。
    学校が0件でもヘッダーのみ出力（取込テンプレートを兼ねる）。
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers())
    writer.writeheader()
    for school in schools:
        setting = school_deadline_service.get_school_setting(db, school.id)
        by_month = {
            row.target_month: row
            for row in school_deadline_service.deadlines_for_year(db, school.id, year)
        }
        record = {
            NO: school.user_no or "",
            NAME_REF: school.display_name or "",
            YEAR: str(year),
            EARLY: "ON" if (setting and setting.early_check_enabled) else "OFF",
            DAYS: str(
                setting.notice_days_before if setting else school_deadline_service.DEFAULT_NOTICE_DAYS_BEFORE
            ),
        }
        for month_num in range(1, 13):
            row = by_month.get(f"{year:04d}-{month_num:02d}")
            record[f"{month_num}月"] = str(row.deadline_date.day) if row else ""
        writer.writerow(record)
    return buf.getvalue().encode("utf-8-sig")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文字コードを判定できません。UTF-8またはShift-JISで保存してください。")


def parse_rows(data: bytes) -> list[dict]:
    """CSVバイト列を辞書行リストに変換する（ヘッダー不足はValueError）。"""
    reader = csv.DictReader(io.StringIO(_decode(data)))
    if reader.fieldnames is None:
        raise ValueError("CSVが空です。")
    actual = {(h or "").strip() for h in reader.fieldnames}
    missing = [h for h in headers() if h not in actual]
    if missing:
        raise ValueError("CSVの見出しがテンプレートと一致しません。不足列: " + " / ".join(missing))
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def is_skip_row(row: dict) -> bool:
    """記入例/コメント行（学校Noが先頭#）または全列空白なら True。"""
    no = row.get(NO, "")
    if no.startswith("#"):
        return True
    return not any(value for value in row.values())


def _find_school_by_no(db: Session, no: str) -> tuple[User | None, str | None]:
    """学校No(user_no)で新システムの学校ユーザーを一意に特定する。"""
    if not no:
        return None, f"{NO}は必須です"
    candidates = [
        u for u in db.scalars(
            select(User).where(User.deleted_at.is_(None), User.user_no == no)
        ).all()
        if "new" in (u.allowed_systems or [])
    ]
    if not candidates:
        return None, f"{NO}「{no}」のユーザーが見つかりません"
    if len(candidates) > 1:
        return None, f"{NO}「{no}」が複数のユーザーに一致します"
    school = candidates[0]
    roles = list(school.roles or []) or ([school.role] if school.role else [])
    if "school" not in roles:
        return None, f"{NO}「{no}」は学校ユーザーではありません（ロール: {'/'.join(roles) or '-'}）"
    return school, None


def _parse_year(value: str) -> tuple[int | None, str | None]:
    if not value:
        return None, f"{YEAR}は必須です（例: 2026）"
    if not value.isdigit() or not (2000 <= int(value) <= 2100):
        return None, f"{YEAR}「{value}」は2000〜2100の西暦で指定してください"
    return int(value), None


def _parse_early(value: str) -> tuple[bool | None, str | None]:
    if not value:
        return None, None  # 空欄=現状維持
    lowered = value.lower()
    if lowered in _TRUE_VALUES:
        return True, None
    if lowered in _FALSE_VALUES:
        return False, None
    return None, f"{EARLY}「{value}」はON/OFFで指定してください"


def _parse_days(value: str) -> tuple[int | None, str | None]:
    if not value:
        return None, None  # 空欄=現状維持
    if not value.isdigit() or not (0 <= int(value) <= 60):
        return None, f"{DAYS}「{value}」は0〜60の日数で指定してください"
    return int(value), None


def _parse_month_cell(value: str, year: int, month_num: int) -> tuple[date | None, str | None]:
    """月列のセルを締め日に変換する。空欄は (None, None)=削除。エラーは (None, メッセージ)。

    指定できるのは「日」（1〜月末）または対象月内の日付（YYYY-MM-DD / YYYY/M/D）のみ
    （202607161332: N月分はN月の日付のみ）。
    """
    if not value:
        return None, None
    last_day = calendar.monthrange(year, month_num)[1]
    if _DAY_RE.match(value):
        day = int(value)
        if not (1 <= day <= last_day):
            return None, f"{month_num}月の締め日「{value}」は1〜{last_day}の日で指定してください"
        return date(year, month_num, day), None
    matched = _DATE_RE.match(value)
    if matched:
        y, m, d = map(int, matched.groups())
        try:
            parsed = date(y, m, d)
        except ValueError:
            return None, f"{month_num}月の締め日「{value}」は存在しない日付です"
        if (y, m) != (year, month_num):
            return None, f"{month_num}月の締め日「{value}」は{year}年{month_num}月内の日付で指定してください"
        return parsed, None
    return None, f"{month_num}月の締め日「{value}」の形式が不正です（日（例: 25）または {year}-{month_num:02d}-25 形式）"


def rows_to_plan(db: Session, rows: list[dict]) -> tuple[list[dict], list[str]]:
    """CSV行を学校ごとの保存計画に変換する。エラーは「N行目: 理由」の一覧で返す。

    返り値 plan の各要素:
    {"school": User, "early": bool|None, "days": int|None, "deadlines": {対象月: date|None}}
    （early/days の None は現状維持。deadlines の None はその月の締め日を削除）
    """
    errors: list[str] = []
    plans_by_school: dict = {}
    seen_school_year: dict[tuple, int] = {}

    for offset, row in enumerate(rows):
        line_no = offset + 2  # ヘッダー(1行目)の次から
        if is_skip_row(row):
            continue
        row_errors: list[str] = []

        school, err = _find_school_by_no(db, row.get(NO, ""))
        if err:
            row_errors.append(err)
        year, err = _parse_year(row.get(YEAR, ""))
        if err:
            row_errors.append(err)
        early, err = _parse_early(row.get(EARLY, ""))
        if err:
            row_errors.append(err)
        days, err = _parse_days(row.get(DAYS, ""))
        if err:
            row_errors.append(err)

        deadlines: dict[str, date | None] = {}
        if year is not None:
            for month_num in range(1, 13):
                value, err = _parse_month_cell(row.get(f"{month_num}月", ""), year, month_num)
                if err:
                    row_errors.append(err)
                    continue
                deadlines[f"{year:04d}-{month_num:02d}"] = value

        if school is not None and year is not None:
            key = (school.id, year)
            if key in seen_school_year:
                row_errors.append(
                    f"{NO}「{row.get(NO, '')}」×{YEAR}「{year}」が{seen_school_year[key]}行目と重複しています"
                )
            else:
                seen_school_year[key] = line_no

        if row_errors:
            errors.extend(f"{line_no}行目: {message}" for message in row_errors)
            continue

        plan = plans_by_school.get(school.id)
        if plan is None:
            plan = {"school": school, "early": None, "days": None, "deadlines": {}}
            plans_by_school[school.id] = plan
        # 早期チェック・通知日数は学校単位＝複数行（複数年）で異なる値はエラー
        for field, value, label in (("early", early, EARLY), ("days", days, DAYS)):
            if value is None:
                continue
            if plan[field] is not None and plan[field] != value:
                errors.append(f"{line_no}行目: {label}が同じ学校の他の行と一致しません")
            else:
                plan[field] = value
        plan["deadlines"].update(deadlines)

    return list(plans_by_school.values()), errors


def apply_plans(db: Session, plans: list[dict]) -> int:
    """保存計画を適用する（コミットは呼び出し側）。戻り値は設定した締め日の総数。"""
    saved = 0
    for plan in plans:
        school = plan["school"]
        setting = school_deadline_service.get_school_setting(db, school.id)
        early = plan["early"]
        if early is None:
            early = setting.early_check_enabled if setting else False
        days = plan["days"]
        if days is None:
            days = setting.notice_days_before if setting else school_deadline_service.DEFAULT_NOTICE_DAYS_BEFORE
        school_deadline_service.save_school_settings(
            db,
            school,
            early_check_enabled=early,
            notice_days_before=days,
            deadlines=plan["deadlines"],
        )
        saved += sum(1 for value in plan["deadlines"].values() if value is not None)
    return saved
