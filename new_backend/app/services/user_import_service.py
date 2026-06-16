"""ユーザー管理のCSV一括エクスポート/取り込み（生成・解析・検証）。

契約管理（contract_import_service）と同じ方針:
- CSVはUTF-8(BOM付き)で出力しExcelでの文字化けを防ぐ。取り込みはUTF-8/Shift-JISを自動判定。
- 照合キー: No（user_no）。Noが一致した既存ユーザーを上書き更新する。
- 「No」が先頭#、または全列空白の行はコメント行として取り込み対象外。

【現フェーズ①】上書きできるのは「メールアドレス」「氏名」のみ。
ロール・状態・No・採番帯などは変更しない（参考列として出力するのみ・取り込み時は無視）。
No空欄（=新規作成）は次フェーズ②で対応するため、現フェーズではエラーにする。
"""
import csv
import io
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shared import User

# CSVの列見出し（単一の定義元）。エクスポート出力・取り込み解析の双方で使用する。
NO = "No"  # 照合キー（user_no）
EMAIL = "メールアドレス"  # 上書き対象
NAME = "氏名"  # 上書き対象
ROLES_REF = "ロール(参考)"  # 出力のみ・取り込み時は無視
STATUS_REF = "状態(参考)"  # 出力のみ・取り込み時は無視
SKIP_APPROVAL_REF = "学校承認スキップ(参考)"  # 出力のみ・取り込み時は無視
CREATED_REF = "登録日(参考)"  # 出力のみ・取り込み時は無視

# 取り込みで実際に上書きする列はメール・氏名のみ。それ以外は「(参考)」を付した出力専用列。
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def headers() -> list[str]:
    return [NO, EMAIL, NAME, ROLES_REF, STATUS_REF, SKIP_APPROVAL_REF, CREATED_REF]


def _roles_of(user: User) -> list[str]:
    return list(user.roles or []) or ([user.role] if user.role else [])


def _row_from_user(user: User) -> dict:
    """ユーザーを1行のCSV辞書へ変換する（エクスポート用）。"""
    return {
        NO: user.user_no or "",
        EMAIL: user.email or "",
        NAME: user.display_name or "",
        ROLES_REF: ",".join(_roles_of(user)),
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
    """1行を更新内容 {'user','email','name'} へ変換。エラーがあれば (None, [理由...])。

    現フェーズ①は既存ユーザーの更新のみ。No空欄(=新規作成)は次フェーズ②で対応するためエラーにする。
    """
    errors: list[str] = []
    no = row.get(NO, "")
    email = row.get(EMAIL, "")
    name = row.get(NAME, "")

    if not no:
        errors.append(f"{NO}が空です（新規作成は次フェーズで対応予定のため、現在は取り込めません）")
        return None, errors

    user, err = _find_user_by_no(db, no)
    if err:
        errors.append(err)

    if not email:
        errors.append(f"{EMAIL}は必須です")
    elif not _EMAIL_RE.match(email):
        errors.append(f"{EMAIL}「{email}」の形式が正しくありません")

    if not name:
        errors.append(f"{NAME}は必須です")
    elif len(name) > 100:
        errors.append(f"{NAME}は100文字以内で入力してください")

    if errors or not user:
        return None, errors
    return {"user": user, "email": email, "name": name}, []
