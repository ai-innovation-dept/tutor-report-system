"""契約管理のCSV一括エクスポート/取り込み（生成・解析・検証）。

- エクスポート/取り込みCSVはUTF-8(BOM付き)で出力しExcelでの文字化けを防ぐ。
- 取り込みCSVはUTF-8/Shift-JISの双方を自動判定して読み込む。
- 識別子(必須): 講師=講師番号(user_no/tutor_no)、学校=学校番号(user_no)。氏名・学校名は参考列(照合に未使用)。
- 「講師番号」が空、または先頭が「#」の行はコメント行として取り込み対象外。
- (講師番号, 学校番号)が一致する契約は上書き更新、一致しなければ新規追加（upsert）。
- 担当業務は前期・後期のうち少なくとも1期（設定する期は名称・適用期間が必須。202607170952で
  両期必須から緩和）。期別のコマ設定・使用/未使用はCSV対象外（取込時も保持）。
- 契約管理番号は参考列（自動発番のため取込では無視。旧テンプレート＝列なしも取込可）。
"""
import csv
import io
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.shared import User
from app.schemas.contracts import (
    MAX_MAIN_TASKS,
    MAX_SUB_TASKS,
    TERM_LABELS,
    ContractCreate,
    ContractTask,
    ContractWorkloadCase,
    term_payload_errors,
)

# CSVの列見出し（単一の定義元）。エクスポート出力・解析の双方で使用する。
# 照合は番号(ID)で行う: 講師=講師番号(user_no/tutor_no)、学校=学校番号(user_no)。氏名・学校名は参考列。
TUTOR_NO = "講師番号"
TUTOR_NAME_REF = "講師氏名(参考)"
SCHOOL_NO = "学校番号"
SCHOOL_NAME = "学校名(参考)"
# 契約管理番号は作成順の自動発番のため参考列（取込では無視）。旧テンプレート（列なし）も取込可。
CONTRACT_NO_REF = "契約管理番号(参考)"
# 取込時に無くてもエラーにしない列（参考・自動発番の列）。エクスポートには常に含める。
OPTIONAL_HEADERS = {CONTRACT_NO_REF}
CUSTOMER_ID = "お客様ID"
OUR_STAFF = "弊社担当"
DISPATCH_ADDRESS = "事業所の所在地"
WORK_LOCATION = "就業場所"
CLASSROOM_NAME = "教室名"
CONTRACT_START = "契約開始(YYYY-MM-DD)"
CONTRACT_END = "契約終了(YYYY-MM-DD)"
SHIFT_NOTE = "スケジュール欄"  # 変数名は旧称shift_note由来（DBカラム互換のため）
WORK_CONTENT = "従事業務内容"
SCORING_ENABLED = "採点を追加する(有/無)"
SCORING_LABEL = "採点 項目名"
SCORING_UNIT = "採点 単位"
SCORING_TASK_ID = "採点 委託業務ID"
SCORING_CONTRACT_ID = "採点 個別契約ID"

_CIRCLED = "①②③④⑤"


def _term_h(index: int) -> str:
    return TERM_LABELS[index]  # 1=前期 / 2=後期


def _main_name_h(i: int) -> str:
    return f"担当業務({_term_h(i)})名"


def _main_id_h(i: int) -> str:
    return f"担当業務({_term_h(i)})ID"


def _main_contract_id_h(i: int) -> str:
    return f"担当業務({_term_h(i)})個別契約ID"


def _monthly_minutes_h(i: int) -> str:
    return f"月時間(分)({_term_h(i)})"


def _weekly_lessons_h(i: int) -> str:
    return f"週コマ({_term_h(i)})"


def _case_start_h(i: int) -> str:
    return f"適用開始({_term_h(i)})(YYYY-MM-DD)"


def _case_end_h(i: int) -> str:
    return f"適用終了({_term_h(i)})(YYYY-MM-DD)"


def _sub_name_h(i: int) -> str:
    return f"副業務{_CIRCLED[i - 1]}名"


def _sub_id_h(i: int) -> str:
    return f"副業務{_CIRCLED[i - 1]}ID"


def _sub_contract_id_h(i: int) -> str:
    return f"副個別契約{_CIRCLED[i - 1]}ID"


def headers() -> list[str]:
    cols = [TUTOR_NO, TUTOR_NAME_REF, SCHOOL_NO, SCHOOL_NAME, CONTRACT_NO_REF, CUSTOMER_ID, OUR_STAFF,
            DISPATCH_ADDRESS, WORK_LOCATION, CLASSROOM_NAME, CONTRACT_START, CONTRACT_END,
            SHIFT_NOTE, WORK_CONTENT]
    for i in range(1, MAX_MAIN_TASKS + 1):
        cols += [
            _main_name_h(i), _main_id_h(i), _main_contract_id_h(i),
            _monthly_minutes_h(i), _weekly_lessons_h(i), _case_start_h(i), _case_end_h(i),
        ]
    for i in range(1, MAX_SUB_TASKS + 1):
        cols += [_sub_name_h(i), _sub_id_h(i), _sub_contract_id_h(i)]
    cols += [SCORING_ENABLED, SCORING_LABEL, SCORING_UNIT, SCORING_TASK_ID, SCORING_CONTRACT_ID]
    return cols


def _case_for_term(profile, index: int) -> dict | None:
    """担当業務（task_index=index）の期別設定ケースを返す（旧データの task_index 無しは前期扱い）。"""
    cases = [c for c in (profile.workload_cases or []) if isinstance(c, dict)]
    return next((c for c in cases if int(c.get("task_index") or 1) == index), None)


def _row_from_profile(profile) -> dict:
    """契約(プロファイル)を1行のCSV辞書へ変換する（エクスポート用。氏名・学校名も補完）。"""
    tutor = getattr(profile, "tutor", None)
    school = getattr(profile, "school", None)
    row = {
        TUTOR_NO: (tutor.tutor_no or tutor.user_no or "") if tutor else "",
        TUTOR_NAME_REF: (tutor.display_name or "") if tutor else "",
        SCHOOL_NO: (school.user_no or "") if school else "",
        SCHOOL_NAME: (school.display_name or "") if school else "",
        CONTRACT_NO_REF: f"{profile.contract_no:05d}" if profile.contract_no is not None else "",
        CUSTOMER_ID: profile.customer_id or "",
        OUR_STAFF: profile.our_staff or "",
        DISPATCH_ADDRESS: profile.dispatch_place_address or "",
        WORK_LOCATION: profile.work_location or "",
        CLASSROOM_NAME: profile.classroom_name or "",
        CONTRACT_START: profile.contract_start.isoformat() if profile.contract_start else "",
        CONTRACT_END: profile.contract_end.isoformat() if profile.contract_end else "",
        SHIFT_NOTE: profile.shift_note or "",
        WORK_CONTENT: profile.work_content or "",
        SCORING_ENABLED: "有" if profile.scoring_enabled else "",
        SCORING_LABEL: profile.scoring_label or "",
        SCORING_UNIT: profile.scoring_unit or "",
        SCORING_TASK_ID: profile.scoring_task_id or "",
        SCORING_CONTRACT_ID: profile.scoring_contract_id or "",
    }
    for i in range(1, MAX_MAIN_TASKS + 1):
        case = _case_for_term(profile, i)
        row[_main_name_h(i)] = getattr(profile, f"task_name_{i}") or ""
        row[_main_id_h(i)] = getattr(profile, f"task_id_{i}") or ""
        row[_main_contract_id_h(i)] = getattr(profile, f"contract_id_{i}") or ""
        row[_monthly_minutes_h(i)] = str(case["monthly_minutes"]) if case and case.get("monthly_minutes") is not None else ""
        row[_weekly_lessons_h(i)] = str(case["weekly_lessons"]) if case and case.get("weekly_lessons") is not None else ""
        row[_case_start_h(i)] = str(case.get("start_date") or "") if case else ""
        row[_case_end_h(i)] = str(case.get("end_date") or "") if case else ""
    for i in range(1, MAX_SUB_TASKS + 1):
        row[_sub_name_h(i)] = getattr(profile, f"sub_task_name_{i}") or ""
        row[_sub_id_h(i)] = getattr(profile, f"sub_task_id_{i}") or ""
        row[_sub_contract_id_h(i)] = getattr(profile, f"sub_contract_id_{i}") or ""
    return row


def build_export_csv(profiles) -> bytes:
    """現在の契約一覧を UTF-8(BOM) のCSVで返す。空でもヘッダーのみ出力（取込テンプレートを兼ねる）。

    表示項目フラグ(show_*)・期別コマ設定はCSV対象外（ドロワー管理・取込時も保持）。
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers())
    writer.writeheader()
    for profile in profiles:
        writer.writerow(_row_from_profile(profile))
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
    # 参考・自動発番の列（OPTIONAL_HEADERS）は旧テンプレートに無くても取込可
    missing = [h for h in headers() if h not in actual and h not in OPTIONAL_HEADERS]
    if missing:
        raise ValueError("CSVの見出しがテンプレートと一致しません。不足列: " + " / ".join(missing))
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def is_skip_row(row: dict) -> bool:
    """記入例/コメント行（講師番号が空 or 先頭#）または全列空白なら True。"""
    tutor_no = row.get(TUTOR_NO, "")
    if tutor_no.startswith("#"):
        return True
    return not any(value for value in row.values())


def _has_role(user: User | None, role: str) -> bool:
    if not user:
        return False
    return role in (list(user.roles or []) or ([user.role] if user.role else []))


def _find_tutor(db: Session, tutor_no: str) -> tuple[User | None, str | None]:
    candidates = [
        u for u in db.scalars(
            select(User).where(
                User.deleted_at.is_(None),
                or_(User.user_no == tutor_no, User.tutor_no == tutor_no),
            )
        ).all()
        if _has_role(u, "tutor")
    ]
    if not candidates:
        return None, f"講師番号「{tutor_no}」の講師が見つかりません"
    if len(candidates) > 1:
        return None, f"講師番号「{tutor_no}」が複数の講師に一致します"
    return candidates[0], None


def _find_school(db: Session, school_no: str) -> tuple[User | None, str | None]:
    candidates = [
        u for u in db.scalars(
            select(User).where(User.deleted_at.is_(None), User.user_no == school_no)
        ).all()
        if _has_role(u, "school")
    ]
    if not candidates:
        return None, f"学校番号「{school_no}」の学校が見つかりません"
    if len(candidates) > 1:
        return None, f"学校番号「{school_no}」が複数の学校に一致します"
    return candidates[0], None


def _parse_date(value: str, label: str, errors: list[str]) -> date | None:
    if not value:
        return None
    text = value.strip()
    # Excelは日付列を「2026/6/1」等（区切り「/」・ゼロ詰めなし）へ変換しがち。
    # date.fromisoformat はゼロ詰め必須で「2026-6-1」を弾くため、strptime で区切り・ゼロ詰めの揺れを吸収する。
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    errors.append(f"{label}「{value}」は日付(YYYY-MM-DD)で入力してください")
    return None


def _parse_int(value: str, label: str, errors: list[str]) -> int | None:
    if not value:
        return None
    normalized = value.replace(",", "")
    if not normalized.lstrip("-").isdigit():
        errors.append(f"{label}「{value}」は数値で入力してください")
        return None
    return int(normalized)


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"有", "true", "1", "○", "〇", "yes", "はい", "y"}


def _collect_sub_tasks(row: dict) -> list[ContractTask]:
    tasks: list[ContractTask] = []
    for i in range(1, MAX_SUB_TASKS + 1):
        name = row.get(_sub_name_h(i), "")
        task_id = row.get(_sub_id_h(i), "")
        contract_id = row.get(_sub_contract_id_h(i), "")
        if name or task_id or contract_id:
            tasks.append(ContractTask(task_name=name or None, task_id=task_id or None, contract_id=contract_id or None))
    return tasks


def _collect_terms(row: dict, errors: list[str]) -> tuple[list[ContractTask], list[ContractWorkloadCase]]:
    """前期・後期の担当業務と期別設定（月時間・週コマ・適用期間）をCSV行から収集する。"""
    tasks: list[ContractTask] = []
    cases: list[ContractWorkloadCase] = []
    for i in range(1, MAX_MAIN_TASKS + 1):
        name = row.get(_main_name_h(i), "")
        task_id = row.get(_main_id_h(i), "")
        contract_id = row.get(_main_contract_id_h(i), "")
        tasks.append(ContractTask(task_name=name or None, task_id=task_id or None, contract_id=contract_id or None))
        monthly = _parse_int(row.get(_monthly_minutes_h(i), ""), _monthly_minutes_h(i), errors)
        weekly = _parse_int(row.get(_weekly_lessons_h(i), ""), _weekly_lessons_h(i), errors)
        start = _parse_date(row.get(_case_start_h(i), ""), _case_start_h(i), errors)
        end = _parse_date(row.get(_case_end_h(i), ""), _case_end_h(i), errors)
        if start and end and end < start:
            errors.append(f"担当業務({_term_h(i)})の適用期間は終了日を開始日以降にしてください")
        if monthly is not None or weekly is not None or start or end:
            cases.append(ContractWorkloadCase(
                task_index=i, monthly_minutes=monthly, weekly_lessons=weekly,
                start_date=start, end_date=end,
            ))
    return tasks, cases


def row_to_payload(db: Session, row: dict) -> tuple[ContractCreate | None, list[str]]:
    """1行をContractCreateへ変換。エラーがあれば (None, [理由...]) を返す。"""
    errors: list[str] = []

    tutor_no = row.get(TUTOR_NO, "")
    school_no = row.get(SCHOOL_NO, "")
    tutor = school = None
    if not tutor_no:
        errors.append(f"{TUTOR_NO}は必須です")
    else:
        tutor, err = _find_tutor(db, tutor_no)
        if err:
            errors.append(err)
    if not school_no:
        errors.append(f"{SCHOOL_NO}は必須です")
    else:
        school, err = _find_school(db, school_no)
        if err:
            errors.append(err)

    contract_start = _parse_date(row.get(CONTRACT_START, ""), CONTRACT_START, errors)
    contract_end = _parse_date(row.get(CONTRACT_END, ""), CONTRACT_END, errors)

    tasks, cases = _collect_terms(row, errors)
    sub_tasks = _collect_sub_tasks(row)
    # 前期・後期の必須（名称・適用期間・重複なし）は画面保存と同じ共通検証を使う
    errors.extend(term_payload_errors(tasks, cases))

    if errors or not tutor or not school:
        return None, errors

    payload = ContractCreate(
        tutor_id=tutor.id,
        school_id=school.id,
        customer_id=row.get(CUSTOMER_ID) or None,
        our_staff=row.get(OUR_STAFF) or None,
        dispatch_place_address=row.get(DISPATCH_ADDRESS) or None,
        work_location=row.get(WORK_LOCATION) or None,
        classroom_name=row.get(CLASSROOM_NAME) or None,
        contract_start=contract_start,
        contract_end=contract_end,
        shift_note=row.get(SHIFT_NOTE) or None,
        work_content=row.get(WORK_CONTENT) or None,
        scoring_enabled=_parse_bool(row.get(SCORING_ENABLED, "")),
        scoring_label=row.get(SCORING_LABEL) or None,
        scoring_unit=row.get(SCORING_UNIT) or None,
        scoring_task_id=row.get(SCORING_TASK_ID) or None,
        scoring_contract_id=row.get(SCORING_CONTRACT_ID) or None,
        tasks=tasks,
        sub_tasks=sub_tasks,
        workload_cases=cases,
    )
    return payload, []
