"""ユーザー管理のCSV一括エクスポート/取り込み（生成・解析・検証）。

契約管理（contract_import_service）と同じ方針:
- CSVはUTF-8(BOM付き)で出力しExcelでの文字化けを防ぐ。取り込みはUTF-8/Shift-JISを自動判定。
- 照合キー: No（user_no）。Noが先頭#、または全列空白の行はコメント行として取り込み対象外。

取り込みの挙動:
- No一致の既存ユーザー → 「メールアドレス」「氏名」のみ上書き更新（ロール・状態・No・採番帯は変更しない）。
- No空欄の行 → 新規作成。「ロール」「メールアドレス」「氏名」が必須。user_no は自動採番し、
  初期パスワード(Passw0rd!)を設定して初回ログイン時のパスワード変更を必須にする（must_change_password）。
"""
import csv
import io
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shared import User

# CSVの列見出し（単一の定義元）。エクスポート出力・取り込み解析の双方で使用する。
NO = "No"  # 照合キー（user_no）
EMAIL = "メールアドレス"  # 更新・新規作成で適用
NAME = "氏名"  # 更新・新規作成で適用
ROLE = "ロール"  # 新規作成行(No空欄)では必須。既存更新では無視（参考）。
STATUS_REF = "状態(参考)"  # 出力のみ・取り込み時は無視
SKIP_APPROVAL_REF = "学校承認スキップ(参考)"  # 出力のみ・取り込み時は無視
CREATED_REF = "登録日(参考)"  # 出力のみ・取り込み時は無視

# 新規作成で指定できるロール（招待フローと同一）。エクスポートのロール列と同じ表記（raw key）で入力する。
CREATABLE_ROLES = {"tutor", "school", "sales", "office", "admin_master", "admin_chief"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def headers() -> list[str]:
    return [NO, EMAIL, NAME, ROLE, STATUS_REF, SKIP_APPROVAL_REF, CREATED_REF]


def _roles_of(user: User) -> list[str]:
    return list(user.roles or []) or ([user.role] if user.role else [])


def _row_from_user(user: User) -> dict:
    """ユーザーを1行のCSV辞書へ変換する（エクスポート用）。"""
    return {
        NO: user.user_no or "",
        EMAIL: user.email or "",
        NAME: user.display_name or "",
        ROLE: ",".join(_roles_of(user)),
        STATUS_REF: "有効" if user.is_active else "無効",
        SKIP_APPROVAL_REF: "有" if user.skip_parent_approval else "無",
        CREATED_REF: user.created_at.strftime("%Y-%m-%d %H:%M") if user.created_at else "",
    }


def build_export_csv(users) -> bytes:
    """現在の登録ユーザーを UTF-8(BOM) のCSVで返す。空でもヘッダーのみ出力（取込テンプレートを兼ねる）。"""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers())
    writer.writeheader()
    for user in users:
        writer.writerow(_row_from_user(user))
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
    """記入例/コメント行（Noが先頭#）または全列空白なら True。"""
    no = row.get(NO, "")
    if no.startswith("#"):
        return True
    return not any(value for value in row.values())


def is_new_row(row: dict) -> bool:
    """No空欄＝新規作成行。"""
    return not row.get(NO, "")


def _validate_email_name(email: str, name: str, errors: list[str]) -> None:
    if not email:
        errors.append(f"{EMAIL}は必須です")
    elif not _EMAIL_RE.match(email):
        errors.append(f"{EMAIL}「{email}」の形式が正しくありません")
    if not name:
        errors.append(f"{NAME}は必須です")
    elif len(name) > 100:
        errors.append(f"{NAME}は100文字以内で入力してください")


def _find_user_by_no(db: Session, no: str) -> tuple[User | None, str | None]:
    """No(user_no)で新システムの既存ユーザーを一意に特定する。"""
    candidates = [
        u for u in db.scalars(
            select(User).where(User.deleted_at.is_(None), User.user_no == no)
        ).all()
        if "new" in (u.allowed_systems or [])
    ]
    if not candidates:
        return None, f"No「{no}」のユーザーが見つかりません"
    if len(candidates) > 1:
        return None, f"No「{no}」が複数のユーザーに一致します"
    return candidates[0], None


def row_to_update(db: Session, row: dict) -> tuple[dict | None, list[str]]:
    """No一致の既存ユーザー更新内容 {'user','email','name'} へ変換。エラーは (None, [理由...])。

    上書きするのはメール・氏名のみ（ロール等は無視）。
    """
    errors: list[str] = []
    user, err = _find_user_by_no(db, row.get(NO, ""))
    if err:
        errors.append(err)
    email = row.get(EMAIL, "")
    name = row.get(NAME, "")
    _validate_email_name(email, name, errors)
    if errors or not user:
        return None, errors
    return {"user": user, "email": email, "name": name}, []


def row_to_create(row: dict, allow_admin_chief: bool) -> tuple[dict | None, list[str]]:
    """No空欄の新規作成内容 {'role','email','name'} へ変換。エラーは (None, [理由...])。

    ロールは単一指定（エクスポートと同じ raw key）。admin_chief は依頼者が管理責任者の場合のみ。
    """
    errors: list[str] = []
    role = row.get(ROLE, "")
    email = row.get(EMAIL, "")
    name = row.get(NAME, "")

    if not role:
        errors.append(f"新規作成には{ROLE}が必須です（{NO}が空のため新規作成として扱います）")
    elif "," in role or " " in role:
        errors.append(f"{ROLE}「{role}」は1つだけ指定してください")
    elif role not in CREATABLE_ROLES:
        errors.append(f"{ROLE}「{role}」は不正です（指定可: " + " / ".join(sorted(CREATABLE_ROLES)) + "）")
    elif role == "admin_chief" and not allow_admin_chief:
        errors.append("admin_chief（管理責任者）の作成は管理責任者のみ可能です")

    _validate_email_name(email, name, errors)
    if errors:
        return None, errors
    return {"role": role, "email": email, "name": name}, []
