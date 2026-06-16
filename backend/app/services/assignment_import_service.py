"""担当管理のCSV一括エクスポート/取り込み（生成・解析・検証）。

ユーザー管理CSV(user_import_service)と同じ方針:
- CSVはUTF-8(BOM付き)で出力しExcelでの文字化けを防ぐ。取り込みはUTF-8/Shift-JISを自動判定。
- 講師・保護者・生徒の紐づけを表す。講師=講師No、保護者=保護者No で指定する。

取り込みの挙動:
- 照合キー = (講師No, 生徒名)。一致する担当があれば保護者の紐づけを上書き更新、無ければ新規作成。
  （既存システムは「同一講師の下で生徒名は重複不可」のため、講師No＋生徒名で1件に特定できる。）
- 保護者No 空欄 = 保護者未設定／記入かつ該当する保護者が居ない（または保護者ロールでない）= エラー。
- リマインダー・承認スキップ等の設定はCSVでは扱わない（担当管理画面で個別設定）。
"""
import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User

# CSVの列見出し（単一の定義元）。エクスポート出力・取り込み解析の双方で使用する。
TUTOR_NO = "講師No"            # 照合キー（講師の user_no）
TUTOR_NAME_REF = "講師名(参考)"  # 出力のみ・取り込み時は無視
STUDENT_NAME = "生徒名"        # 照合キー
PARENT_NO = "保護者No"         # 取込で適用（保護者の user_no。空欄=未設定）
PARENT_NAME_REF = "保護者名(参考)"  # 出力のみ・取り込み時は無視
STATUS_REF = "状態(参考)"      # 出力のみ・取り込み時は無視
CREATED_REF = "登録日(参考)"   # 出力のみ・取り込み時は無視


def headers() -> list[str]:
    return [TUTOR_NO, TUTOR_NAME_REF, STUDENT_NAME, PARENT_NO, PARENT_NAME_REF, STATUS_REF, CREATED_REF]


def _row_from_assignment(a) -> dict:
    """担当を1行のCSV辞書へ変換する（エクスポート用）。"""
    tutor = a.tutor
    parent = a.parent
    return {
        TUTOR_NO: (tutor.user_no if tutor else "") or "",
        TUTOR_NAME_REF: (tutor.display_name if tutor else "") or "",
        STUDENT_NAME: a.student_name or "",
        PARENT_NO: (parent.user_no if parent else "") or "",
        PARENT_NAME_REF: (parent.display_name if parent else "") or "",
        STATUS_REF: "有効" if a.is_active else "無効",
        CREATED_REF: a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "",
    }


def build_export_csv(assignments) -> bytes:
    """現在の担当を UTF-8(BOM) のCSVで返す。空でもヘッダーのみ出力（取込テンプレートを兼ねる）。"""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers())
    writer.writeheader()
    for a in assignments:
        writer.writerow(_row_from_assignment(a))
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
    """記入例/コメント行（講師Noが先頭#）または全列空白なら True。"""
    if row.get(TUTOR_NO, "").startswith("#"):
        return True
    return not any(value for value in row.values())


def _find_user_by_no_role(db: Session, no: str, role: str) -> tuple[User | None, bool]:
    """user_no一致の未削除legacyユーザー（指定ロールを持つ）を一意に特定する。(user, 複数該当) を返す。"""
    candidates = [
        u for u in db.scalars(select(User).where(User.deleted_at.is_(None), User.user_no == no)).all()
        if "legacy" in (u.allowed_systems or []) and role in (list(u.roles or []) or ([u.role] if u.role else []))
    ]
    if not candidates:
        return None, False
    return candidates[0], len(candidates) > 1


def resolve_row(db: Session, row: dict) -> tuple[dict | None, list[str]]:
    """1行を {'tutor','student_name','parent'} へ解決する。エラーは (None, [理由...])。"""
    errors: list[str] = []
    tutor_no = row.get(TUTOR_NO, "")
    student_name = row.get(STUDENT_NAME, "")
    parent_no = row.get(PARENT_NO, "")

    tutor = None
    if not tutor_no:
        errors.append(f"{TUTOR_NO}は必須です")
    else:
        tutor, multiple = _find_user_by_no_role(db, tutor_no, "tutor")
        if multiple:
            errors.append(f"{TUTOR_NO}「{tutor_no}」が複数の講師に一致します")
        elif not tutor:
            errors.append(f"{TUTOR_NO}「{tutor_no}」の講師が見つかりません")

    if not student_name:
        errors.append(f"{STUDENT_NAME}は必須です")
    elif len(student_name) > 100:
        errors.append(f"{STUDENT_NAME}は100文字以内で入力してください")

    parent = None
    if parent_no:
        parent, multiple = _find_user_by_no_role(db, parent_no, "parent")
        if multiple:
            errors.append(f"{PARENT_NO}「{parent_no}」が複数の保護者に一致します")
        elif not parent:
            errors.append(f"{PARENT_NO}「{parent_no}」の保護者が見つかりません")

    if errors:
        return None, errors
    return {"tutor": tutor, "student_name": student_name, "parent": parent}, []
