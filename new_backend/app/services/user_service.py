"""ユーザー関連のビジネスロジック。採番・認証・ロール判定を集約する。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shared import Invitation, User

NEW_SYSTEM_ROLES = {"tutor", "school", "sales", "office", "admin_master", "admin_chief"}
ALLOWED_INVITATION_ROLES = {"tutor", "school", "sales", "office", "admin_master", "admin_chief"}

ROLE_LABELS = {
    "tutor":        "講師",
    "school":       "学校担当",
    "sales":        "営業担当",
    "office":       "事務担当",
    "admin_master": "管理者",
    "admin_chief":  "管理責任者",
}

# (最小番号, プレフィックス) — ユーザーID採番ポリシー（数値5桁）
#   講師=1nnnn（両システム共通の通し番号） / 学校=4nnnn / 事務・営業・経理(admin_master)=5nnnn
#   管理責任者=9nnnn
_NO_RANGE: dict[str, tuple[int, str]] = {
    "tutor":        (10001, ""),
    "school":       (40001, ""),
    "sales":        (50001, ""),
    "office":       (50001, ""),
    "admin_master": (50001, ""),
    "admin_chief":  (90001, ""),
}


def generate_user_no(db: Session, role: str) -> str:
    """ロール別番号帯で「未使用の最小番号」を採番する。

    既存（未削除）user_noと未受諾招待を参照して重複を防ぐ。帯内で歯抜けになっている
    若い番号があればそれを優先して埋める（max+1ではない）。
    削除済み（ソフトデリート）ユーザーのNoは解放済みとして扱い、再利用の対象に含める。
    ※ backend/app/services/user_no_service.generate_user_no と同一方針。変更時は両方を更新すること。
    """
    start, prefix = _NO_RANGE.get(role, (10001, ""))

    # 削除済みユーザーのNoは予約しない（再利用可能にする）。
    existing: list[str | None] = list(
        db.scalars(
            select(User.user_no).where(User.user_no.is_not(None), User.deleted_at.is_(None))
        ).all()
    )
    # tutor は legacy の tutor_no も参照して衝突を防ぐ
    if role == "tutor":
        existing += list(
            db.scalars(
                select(User.tutor_no).where(User.tutor_no.is_not(None), User.deleted_at.is_(None))
            ).all()
        )

    # 未受諾招待のtutor_noカラム（user_noを格納済み）。採番済みの予約として扱う。
    pending: list[str | None] = list(
        db.scalars(
            select(Invitation.tutor_no).where(
                Invitation.tutor_no.is_not(None),
                Invitation.accepted_at.is_(None),
            )
        ).all()
    )

    used: set[int] = set()
    for no in [*existing, *pending]:
        s = str(no) if no else ""
        if prefix:
            if not s.startswith(prefix):
                continue
            value = s[len(prefix):]
        else:
            if not s.isdigit():
                continue
            value = s
        try:
            num = int(value)
        except ValueError:
            continue
        if start <= num <= start + 9999:
            used.add(num)

    # 帯の先頭から走査し、未使用の最小番号を返す。
    candidate = start
    while candidate in used:
        candidate += 1
    return f"{prefix}{candidate}"


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.lower()))


def authenticate(db: Session, email: str, password: str) -> User | None:
    from app.core.security import verify_password
    user = get_user_by_email(db, email)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def effective_roles(user: User) -> list[str]:
    return list(user.roles or []) or ([user.role] if user.role else [])


def has_new_system_role(user: User) -> bool:
    return any(r in NEW_SYSTEM_ROLES for r in effective_roles(user))


def allowed_systems_for_role(role: str) -> list[str]:
    if role in {"admin_master", "admin_chief"}:
        return ["legacy", "new"]
    return ["new"]


# CSV一括作成ユーザーの初期パスワード。初回ログイン時に変更を必須化する（must_change_password=True）。
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
        tutor_no=user_no if role == "tutor" else None,  # legacy 互換
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
    user_no は採番し直す（旧Noが既に他ユーザーに再利用されている場合の衝突を避ける。
    旧Noが空いていればそのまま再取得される）。呼び出し側でメール・ロールを検証済みであること。
    """
    from app.core.security import hash_password

    # まだ deleted_at が立っているうちに採番すると、旧Noが空いていれば再取得・埋まっていれば別の空き番号になる。
    user_no = generate_user_no(db, role)
    user.user_no = user_no
    user.tutor_no = user_no if role == "tutor" else None  # legacy 互換
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

