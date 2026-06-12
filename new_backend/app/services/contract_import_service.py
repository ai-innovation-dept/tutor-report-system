"""契約管理のCSV一括登録（テンプレート生成・解析・検証）。

- テンプレートはUTF-8(BOM付き)で出力しExcelでの文字化けを防ぐ。
- 取り込みCSVはUTF-8/Shift-JISの双方を自動判定して読み込む。
- 識別子: 講師=講師番号(user_no/tutor_no)、学校=学校名(display_name)。
- 「講師番号」が空、または先頭が「#」の行は記入例/コメントとして取り込み対象外。
"""
import csv
import io
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.shared import User
from app.schemas.contracts import MAX_MAIN_TASKS, MAX_SUB_TASKS, ContractCreate, ContractTask

# CSVの列見出し（単一の定義元）。テンプレート出力・解析の双方で使用する。
TUTOR_NO = "講師番号"
TUTOR_NAME_REF = "講師氏名(参考・未使用)"
SCHOOL_NAME = "学校名"
CUSTOMER_ID = "お客様ID"
OUR_STAFF = "弊社担当"
CONTRACT_START = "契約開始(YYYY-MM-DD)"
CONTRACT_END = "契約終了(YYYY-MM-DD)"
MONTHLY_MINUTES = "月時間(分)"
WEEKLY_LESSONS = "週コマ"
SHIFT_NOTE = "シフト指定欄"
WORK_CONTENT = "従事業務内容"
SCORING_ENABLED = "採点を追加する(有/無)"
SCORING_LABEL = "採点 項目名"
SCORING_UNIT = "採点 単位"
SCORING_TASK_ID = "採点 委託業務ID"
SCORING_CONTRACT_ID = "採点 個別契約ID"

_CIRCLED = "①②③④⑤"


def _main_name_h(i: int) -> str:
    return f"メイン業務{_CIRCLED[i - 1]}名"


def _main_id_h(i: int) -> str:
    return f"メイン業務{_CIRCLED[i - 1]}ID"


def _main_contract_id_h(i: int) -> str:
    return f"メイン個別契約{_CIRCLED[i - 1]}ID"


def _sub_name_h(i: int) -> str:
    return f"サブ業務{_CIRCLED[i - 1]}名"


def _sub_id_h(i: int) -> str:
    return f"サブ業務{_CIRCLED[i - 1]}ID"


def _sub_contract_id_h(i: int) -> str:
    return f"サブ個別契約{_CIRCLED[i - 1]}ID"


def headers() -> list[str]:
    cols = [TUTOR_NO, TUTOR_NAME_REF, SCHOOL_NAME, CUSTOMER_ID, OUR_STAFF,
            CONTRACT_START, CONTRACT_END, MONTHLY_MINUTES, WEEKLY_LESSONS,
            SHIFT_NOTE, WORK_CONTENT]
    for i in range(1, MAX_MAIN_TASKS + 1):
        cols += [_main_name_h(i), _main_id_h(i), _main_contract_id_h(i)]
    for i in range(1, MAX_SUB_TASKS + 1):
        cols += [_sub_name_h(i), _sub_id_h(i), _sub_contract_id_h(i)]
    cols += [SCORING_ENABLED, SCORING_LABEL, SCORING_UNIT, SCORING_TASK_ID, SCORING_CONTRACT_ID]
    return cols


def _example_row() -> list[str]:
    # 講師番号の先頭が「#」の行は記入例として取り込まれない（削除しても可）。
    row = ["#T0001", "山田太郎", "渋谷高校", "9999", "佐藤麻子",
           "2026-04-01", "2027-03-31", "600", "3", "月9:30-", "数学指導"]
    row += ["数学科指導", "11111", "99992601"]       # メイン①
    row += ["", "", ""] * (MAX_MAIN_TASKS - 1)       # メイン②③
    row += ["教科会", "33333", ""]                    # サブ①
    row += ["", "", ""] * (MAX_SUB_TASKS - 1)        # サブ②〜⑤
    row += ["有", "採点", "回", "22222", "99992602"]  # 採点
    return row


def build_template_csv() -> bytes:
    """ヘッダー＋記入例1行のテンプレートCSVを UTF-8(BOM) で返す。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers())
    writer.writerow(_example_row())
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


def _find_school(db: Session, school_name: str) -> tuple[User | None, str | None]:
    candidates = [
        u for u in db.scalars(
            select(User).where(User.deleted_at.is_(None), User.display_name == school_name)
        ).all()
        if _has_role(u, "school")
    ]
    if not candidates:
        return None, f"学校名「{school_name}」の学校が見つかりません"
    if len(candidates) > 1:
        return None, f"学校名「{school_name}」が複数の学校に一致します"
    return candidates[0], None


def _parse_date(value: str, label: str, errors: list[str]) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.replace("/", "-"))
    except ValueError:
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


def _collect_tasks(row: dict, name_h, id_h, contract_id_h, max_count: int) -> list[ContractTask]:
    tasks: list[ContractTask] = []
    for i in range(1, max_count + 1):
        name = row.get(name_h(i), "")
        task_id = row.get(id_h(i), "")
        contract_id = row.get(contract_id_h(i), "")
        if name or task_id or contract_id:
            tasks.append(ContractTask(task_name=name or None, task_id=task_id or None, contract_id=contract_id or None))
    return tasks


def row_to_payload(db: Session, row: dict) -> tuple[ContractCreate | None, list[str]]:
    """1行をContractCreateへ変換。エラーがあれば (None, [理由...]) を返す。"""
    errors: list[str] = []

    tutor_no = row.get(TUTOR_NO, "")
    school_name = row.get(SCHOOL_NAME, "")
    tutor = school = None
    if not tutor_no:
        errors.append(f"{TUTOR_NO}は必須です")
    else:
        tutor, err = _find_tutor(db, tutor_no)
        if err:
            errors.append(err)
    if not school_name:
        errors.append(f"{SCHOOL_NAME}は必須です")
    else:
        school, err = _find_school(db, school_name)
        if err:
            errors.append(err)

    contract_start = _parse_date(row.get(CONTRACT_START, ""), CONTRACT_START, errors)
    contract_end = _parse_date(row.get(CONTRACT_END, ""), CONTRACT_END, errors)
    monthly_minutes = _parse_int(row.get(MONTHLY_MINUTES, ""), MONTHLY_MINUTES, errors)
    weekly_lessons = _parse_int(row.get(WEEKLY_LESSONS, ""), WEEKLY_LESSONS, errors)

    tasks = _collect_tasks(row, _main_name_h, _main_id_h, _main_contract_id_h, MAX_MAIN_TASKS)
    sub_tasks = _collect_tasks(row, _sub_name_h, _sub_id_h, _sub_contract_id_h, MAX_SUB_TASKS)
    if not tasks:
        errors.append("メイン業務①は必須です")

    if errors or not tutor or not school:
        return None, errors

    payload = ContractCreate(
        tutor_id=tutor.id,
        school_id=school.id,
        customer_id=row.get(CUSTOMER_ID) or None,
        our_staff=row.get(OUR_STAFF) or None,
        contract_start=contract_start,
        contract_end=contract_end,
        monthly_minutes=monthly_minutes,
        weekly_lessons=weekly_lessons,
        shift_note=row.get(SHIFT_NOTE) or None,
        work_content=row.get(WORK_CONTENT) or None,
        scoring_enabled=_parse_bool(row.get(SCORING_ENABLED, "")),
        scoring_label=row.get(SCORING_LABEL) or None,
        scoring_unit=row.get(SCORING_UNIT) or None,
        scoring_task_id=row.get(SCORING_TASK_ID) or None,
        scoring_contract_id=row.get(SCORING_CONTRACT_ID) or None,
        tasks=tasks,
        sub_tasks=sub_tasks,
    )
    return payload, []
