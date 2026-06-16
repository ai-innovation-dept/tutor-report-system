"""ユーザー管理のCSV一括エクスポート/取り込み（生成・解析・検証・作成）。

業務連絡表システム(new_backend)の同名サービスと同じ方針を、指導実績報告システム(legacy)の
ロール体系・所属(allowed_systems)に合わせて移植したもの:
- CSVはUTF-8(BOM付き)で出力しExcelでの文字化けを防ぐ。取り込みはUTF-8/Shift-JISを自動判定。
- 照合キー: No（user_no）。Noが先頭#、または全列空白の行はコメント行として取り込み対象外。

取り込みの挙動:
- No一致の既存ユーザー → 「メールアドレス」「氏名」のみ上書き更新（ロール・状態・No・採番帯は変更しない）。
- No空欄の行 → 新規作成。「ロール」「メールアドレス」「氏名」が必須。user_no は自動採番し、
  初期パスワード(Passw0rd!)を設定して初回ログイン時のパスワード変更を必須にする（must_change_password）。
  メールが削除済みユーザーのものなら、その同一アカウントを復活させる（履歴を引き継ぐ）。

保護者(parent)もアカウントのみ作成できる。講師・生徒との紐づけ（担当）はCSVでは扱わず、
担当管理またはアカウント作成後の招待フローで行う。
"""
import csv
import io
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.services.user_no_service import generate_user_no

# CSVの列見出し（単一の定義元）。エクスポート出力・取り込み解析の双方で使用する。
NO = "No"  # 照合キー（user_no）
EMAIL = "メールアドレス"  # 更新・新規作成で適用
NAME = "氏名"  # 更新・新規作成で適用
ROLE = "ロール"  # 新規作成行(No空欄)では必須。既存更新では無視（参考）。
STATUS_REF = "状態(参考)"  # 出力のみ・取り込み時は無視
CREATED_REF = "登録日(参考)"  # 出力のみ・取り込み時は無視

# 新規作成で指定できるロール（招待フローと同一）。エクスポートのロール列と同じ表記（raw key）で入力する。
# 受付＋再鑑の複数ロールは画面のチェックボックスで付与する想定のため、新規作成行は単一指定とする。
CREATABLE_ROLES = {"tutor", "parent", "admin_receiver", "admin_reviewer", "admin_master", "admin_chief"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def headers() -> list[str]:
    return [NO, EMAIL, NAME, ROLE, STATUS_REF, CREATED_REF]


def allowed_systems_for_role(role: str) -> list[str]:
    """ロールに応じた所属システム。admin_master/admin_chief は両システム、他は当(legacy)のみ。

    既存の招待登録フロー（auth.py register_parent）の規約と一致させる。
    """
    if role in {"admin_master", "admin_chief"}:
        return ["legacy", "new"]
    return ["legacy"]


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
    """No(user_no)で既存システムの既存ユーザーを一意に特定する。"""
    candidates = [
        u for u in db.scalars(
            select(User).where(User.deleted_at.is_(None), User.user_no == no)
        ).all()
        if "legacy" in (u.allowed_systems or [])
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


# 初期パスワード。初回ログイン時に変更を必須化する（must_change_password=True）。
INITIAL_PASSWORD = "Passw0rd!"


def create_initial_user(db: Session, role: str, email: str, display_name: str) -> User:
    """初期パスワード付きでユーザーを新規作成する（CSV一括作成用）。

    招待フローと同じ採番・所属・tutor_no規約に従い、初回ログイン時のパスワード変更を必須にする。
    呼び出し側でメール重複・ロール妥当性を検証済みであること。
    """
    from app.core.security import hash_password

    user_no = generate_user_no(db, role)
    user = User(
        email=email.strip().lower(),
        role=role,
        roles=[role],
        display_name=display_name,
        user_no=user_no,
        tutor_no=user_no if role == "tutor" else None,
        allowed_systems=allowed_systems_for_role(role),
        password_hash=hash_password(INITIAL_PASSWORD),
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    return user


def revive_user(db: Session, user: User, role: str, email: str, display_name: str) -> User:
    """ソフトデリート済みユーザーを同一アカウントのまま復活させる（CSV新規作成行でメール再利用時）。

    email は一意制約のため別アカウントとして作り直せない。既存の招待再登録フローと同様に、
    同一アカウントを復活させ、過去の報告書履歴を同一人物のものとして引き継ぐ。
    ロール・氏名はCSVの内容で初期化し、初期パスワード(Passw0rd!)＋初回ログイン時の変更必須を設定する。
    user_no は採番し直す。呼び出し側でメール・ロールを検証済みであること。
    """
    from app.core.security import hash_password

    user_no = generate_user_no(db, role)
    user.user_no = user_no
    user.tutor_no = user_no if role == "tutor" else None
    user.deleted_at = None
    user.is_active = True
    user.role = role
    user.roles = [role]
    user.allowed_systems = allowed_systems_for_role(role)
    user.display_name = display_name
    user.email = email.strip().lower()
    user.password_hash = hash_password(INITIAL_PASSWORD)
    user.must_change_password = True
    return user
