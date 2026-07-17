import math
import secrets
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import or_, func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password
from app.dependencies.auth import get_current_user, has_role, require_role
from app.models.shared import User
from app.schemas.school_settings import SchoolSettingsIn, SchoolSettingsOut
from app.schemas.users import UserListOut, UserOut, UserPatch, UserRolesPatch
from app.services import school_deadline_service, user_import_service
from app.services.user_service import create_initial_user, revive_user

router = APIRouter(prefix="/api/w/users", tags=["work-users"])

# 経理画面のロールタブと一致させる
ROLE_TAB_KEYS = ["tutor", "school", "sales", "office", "admin_master", "admin_chief"]
# ロール編集を許可する組み合わせ（営業・事務スタッフのみ付け替え可能）
EDITABLE_STAFF_ROLES = {"sales", "office"}


def _user_roles(user: User) -> list[str]:
    return list(user.roles or []) or ([user.role] if user.role else [])


ROLE_LABELS_JA = {
    "tutor": "講師",
    "school": "学校",
    "sales": "営業",
    "office": "事務",
    "admin_master": "経理",
    "admin_chief": "管理責任者",
}


def _active_role_counts(db: Session) -> dict[str, int]:
    """新システム所属の有効（未削除・is_active）ユーザーをロール別に数える。

    「最後の1人」のロール保護ガードと、一覧画面のUI判定で共通に使う唯一の集計。
    """
    users = db.scalars(
        select(User).where(User.deleted_at.is_(None), User.is_active.is_(True))
    ).all()
    counts: dict[str, int] = {}
    for u in users:
        if "new" not in (u.allowed_systems or []):
            continue
        for role in _user_roles(u):
            counts[role] = counts.get(role, 0) + 1
    return counts


def _get_user_or_404(db: Session, user_id: UUID) -> User:
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    return user


def _ensure_not_self(current_user: User, user: User) -> None:
    """自分自身の削除・無効化を禁止する（操作者が自分のアカウントを使用不能にするのを防ぐ）。"""
    if current_user.id == user.id:
        raise HTTPException(status_code=409, detail="自分自身は削除・無効化できません")


def _ensure_not_last_of_role(db: Session, user: User) -> None:
    """対象がそのロールの最後の有効ユーザーなら操作を止める（ロールを空にしない）。

    対象が持つロールごとに有効ユーザー数を数え、1人（＝本人のみ）なら 409。
    既に無効化済みの対象は有効数に影響しないため対象外（無効ユーザーの削除は許可）。
    """
    if not user.is_active:
        return
    counts = _active_role_counts(db)
    for role in _user_roles(user):
        if counts.get(role, 0) <= 1:
            label = ROLE_LABELS_JA.get(role, role)
            raise HTTPException(status_code=409, detail=f"最後の{label}ユーザーのため操作できません")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("", response_model=UserListOut)
def list_users(
    page: int = 1,
    per_page: int = 50,
    role: str | None = None,
    roles: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    requester_roles = _user_roles(user)
    role_filter = roles or role
    # 営業・事務はユーザ管理を経理と同等に利用できるため、全件一覧（フィルタなし）も許可する
    if not ({"admin_master", "admin_chief", "sales", "office"} & set(requester_roles)) and role_filter is None:
        raise HTTPException(status_code=403, detail="forbidden")
    page = max(1, page)
    per_page = min(max(1, per_page), 100)
    stmt = select(User).where(User.deleted_at.is_(None))
    if search and search.strip():
        kw = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(func.lower(User.display_name).like(kw), func.lower(User.email).like(kw))
        )
    users = db.scalars(stmt).all()
    # 所属の唯一の基準は allowed_systems。新システム(new)に登録のあるユーザーのみ表示する。
    users = [u for u in users if "new" in (u.allowed_systems or [])]
    # No の小さい順（数値）でソート。user_no 未設定は末尾。
    users = sorted(users, key=lambda u: int(u.user_no) if u.user_no and str(u.user_no).isdigit() else 999999)

    role_counts = {"all": len(users)}
    for key in ROLE_TAB_KEYS:
        role_counts[key] = sum(1 for u in users if key in _user_roles(u))
    active_admin_master_count = sum(
        1 for u in users if u.is_active and "admin_master" in _user_roles(u)
    )
    active_admin_chief_count = sum(
        1 for u in users if u.is_active and "admin_chief" in _user_roles(u)
    )
    # ロール保護（最後の1人は削除・無効化不可）のUI判定用に、全ロールの有効ユーザー数を返す。
    # 検索・ロール絞り込みに依存しない全体集計（サーバ側ガード _ensure_not_last_of_role と同一基準）。
    active_role_counts = _active_role_counts(db)

    if role_filter:
        wanted = {r.strip() for r in role_filter.split(",") if r.strip()}
        users = [u for u in users if wanted & set(_user_roles(u))]
    total = len(users)
    total_pages = max(1, math.ceil(total / per_page))
    start = (page - 1) * per_page
    return UserListOut(
        items=list(users[start: start + per_page]),
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        role_counts=role_counts,
        active_admin_master_count=active_admin_master_count,
        active_admin_chief_count=active_admin_chief_count,
        active_role_counts=active_role_counts,
    )


@router.get("/export")
def export_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    """現在の登録ユーザー（新システム）をCSV(UTF-8 BOM)でエクスポートする（バックアップ）。

    編集して /import で再取込できる（Noで照合し、メール・氏名のみを上書き更新）。
    一覧画面と同じ母集団（allowed_systems に "new" を含む未削除ユーザー）をNo昇順で出力する。
    """
    users = db.scalars(select(User).where(User.deleted_at.is_(None))).all()
    users = [u for u in users if "new" in (u.allowed_systems or [])]
    users = sorted(users, key=lambda u: int(u.user_no) if u.user_no and str(u.user_no).isdigit() else 999999)
    return Response(
        content=user_import_service.build_export_csv(users),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("ユーザー一覧.csv")},
    )


@router.post("/import")
def import_users(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    """CSVを一括取り込みする。1件でも検証エラーがあれば全件中止（何も登録しない）。

    No一致の既存ユーザー → メール・氏名のみ上書き更新。
    No空欄の行 → 新規作成（ロール必須、user_no自動採番、初期パスワード Passw0rd!、初回ログイン時変更必須）。
    　ただしメールが削除済みユーザーのものなら、その同一アカウントを復活させる（履歴を引き継ぐ）。
    メールは他の有効ユーザー／同一CSV内で重複しないことを検証する。
    """
    try:
        rows = user_import_service.parse_rows(file.file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    allow_admin_chief = has_role(current_user, "admin_chief")
    updates: list[dict] = []  # {"user", "email", "name", "line"}
    creates: list[dict] = []  # {"role", "email", "name", "line"}
    errors: list[str] = []
    seen_user: dict[UUID, int] = {}
    for offset, row in enumerate(rows):
        line_no = offset + 2  # ヘッダー(1行目)の次から
        if user_import_service.is_skip_row(row):
            continue
        if user_import_service.is_new_row(row):
            result, row_errors = user_import_service.row_to_create(row, allow_admin_chief)
            if row_errors:
                errors.extend(f"{line_no}行目: {message}" for message in row_errors)
                continue
            result["line"] = line_no
            creates.append(result)
            continue
        result, row_errors = user_import_service.row_to_update(db, row)
        if row_errors:
            errors.extend(f"{line_no}行目: {message}" for message in row_errors)
            continue
        uid = result["user"].id
        if uid in seen_user:
            errors.append(f"{line_no}行目: 同一CSV内でNoが{seen_user[uid]}行目と重複しています")
            continue
        seen_user[uid] = line_no
        result["line"] = line_no
        updates.append(result)

    # メール重複チェック: 同一CSV内の重複と、既存ユーザーとの衝突を検出する（更新行は自分自身を除外）。
    email_line: dict[str, int] = {}
    for item in (*updates, *creates):
        key = item["email"].lower()
        if key in email_line:
            errors.append(f"{item['line']}行目: メールアドレス「{item['email']}」が{email_line[key]}行目と重複しています")
        else:
            email_line[key] = item["line"]
    targets = [*updates, *creates]
    if targets:
        # email は一意制約のため、1メールにつき最大1ユーザー（削除済みを含む）しか保持しない。
        lowered = list({item["email"].lower() for item in targets})
        existing = db.scalars(select(User).where(func.lower(User.email).in_(lowered))).all()
        holder_by_email: dict[str, User] = {u.email.lower(): u for u in existing}
        for item in targets:
            holder = holder_by_email.get(item["email"].lower())
            if holder is None:
                continue
            self_id = item["user"].id if "user" in item else None  # 更新行は自分自身を許容
            if holder.id == self_id:
                continue
            if holder.deleted_at is None:
                errors.append(f"{item['line']}行目: メールアドレス「{item['email']}」は既に他のユーザーが使用しています")
            elif "user" in item:
                # 更新行が削除済みユーザーのメールを要求：復活は新規作成行（No空欄）でのみ対応する。
                errors.append(
                    f"{item['line']}行目: メールアドレス「{item['email']}」は削除済みユーザーが使用しています。"
                    "再利用するにはNo欄を空にして新規作成行として取り込んでください"
                )
            else:
                # 新規作成行のメールが削除済みユーザーのもの：同一アカウントを復活させる。
                item["revive"] = holder

    if errors:
        raise HTTPException(status_code=400, detail={
            "message": f"取り込みできませんでした（{len(errors)}件のエラー）。修正して再度お試しください。",
            "errors": errors,
        })
    if not targets:
        raise HTTPException(status_code=400, detail={
            "message": "取り込み対象の行がありません。No一致の既存ユーザー、またはNo空欄の新規作成行を入力してください。",
            "errors": [],
        })

    for item in updates:
        item["user"].email = item["email"]
        item["user"].display_name = item["name"]
    created = 0
    revived = 0
    for item in creates:
        holder = item.get("revive")
        if holder is not None:
            revive_user(db, holder, item["role"], item["email"], item["name"])
            revived += 1
        else:
            create_initial_user(db, item["role"], item["email"], item["name"])
            created += 1
        db.flush()  # 連続採番が直前に確定したNoを未使用判定に反映できるようにする
    db.commit()
    return {"imported": len(targets), "created": created, "revived": revived, "updated": len(updates)}


# ---------------------------------------------------------------------------
# 学校の締め日通知設定（202607161140）: 早期チェックON/OFF・通知日数・月ごとの締め日（年間設定）
# ---------------------------------------------------------------------------

def _get_school_or_error(db: Session, user_id: UUID) -> User:
    user = _get_user_or_404(db, user_id)
    if "school" not in _user_roles(user):
        raise HTTPException(status_code=409, detail="締め日通知設定は学校ユーザーのみ設定できます")
    return user


def _school_settings_out(db: Session, school: User, year: int) -> SchoolSettingsOut:
    setting = school_deadline_service.get_school_setting(db, school.id)
    return SchoolSettingsOut(
        early_check_enabled=setting.early_check_enabled if setting else False,
        notice_days_before=(
            setting.notice_days_before if setting else school_deadline_service.DEFAULT_NOTICE_DAYS_BEFORE
        ),
        year=year,
        deadlines=school_deadline_service.deadlines_for_year(db, school.id, year),
    )


@router.get("/{user_id}/school-settings", response_model=SchoolSettingsOut)
def get_school_settings(
    user_id: UUID,
    year: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    """学校の締め日通知設定と、指定年の締め日一覧を返す。"""
    school = _get_school_or_error(db, user_id)
    return _school_settings_out(db, school, year)


@router.put("/{user_id}/school-settings", response_model=SchoolSettingsOut)
def put_school_settings(
    user_id: UUID,
    payload: SchoolSettingsIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    """学校の締め日通知設定を保存する。deadlines に渡した月のみ更新・削除する（None=削除）。

    締め日を変更した月は送信済みガードが解除され、新しい締め日の窓で確認メールの再送対象になる。
    """
    school = _get_school_or_error(db, user_id)
    school_deadline_service.save_school_settings(
        db,
        school,
        early_check_enabled=payload.early_check_enabled,
        notice_days_before=payload.notice_days_before,
        deadlines=payload.deadlines,
    )
    db.commit()
    return _school_settings_out(db, school, payload.year)


@router.patch("/{user_id}", response_model=UserOut)
def patch_user(
    user_id: UUID,
    payload: UserPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    user = _get_user_or_404(db, user_id)
    data = payload.model_dump(exclude_unset=True)
    if "skip_parent_approval" in data and not has_role(current_user, "admin_chief"):
        raise HTTPException(status_code=403, detail="学校承認スキップの設定は管理責任者のみ可能です")
    if data.get("is_active") is False:
        _ensure_not_self(current_user, user)
        _ensure_not_last_of_role(db, user)
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/roles", response_model=UserOut)
def update_user_roles(
    user_id: UUID,
    payload: UserRolesPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    user = _get_user_or_404(db, user_id)
    current = set(_user_roles(user))
    if not current <= EDITABLE_STAFF_ROLES:
        raise HTTPException(status_code=409, detail="このユーザーのロールは変更できません")
    new_roles = [r for r in payload.roles if r]
    if not new_roles:
        raise HTTPException(status_code=422, detail="少なくとも1つのロールを選択してください")
    if not set(new_roles) <= EDITABLE_STAFF_ROLES:
        raise HTTPException(status_code=422, detail="営業・事務のロールのみ変更できます")
    user.roles = new_roles
    user.role = new_roles[0]
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/disable", response_model=UserOut)
def disable_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    user = _get_user_or_404(db, user_id)
    _ensure_not_self(current_user, user)
    if has_role(user, "admin_chief") and not has_role(current_user, "admin_chief"):
        raise HTTPException(status_code=403, detail="管理責任者の無効化は管理責任者のみ可能です")
    _ensure_not_last_of_role(db, user)
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/enable", response_model=UserOut)
def enable_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    user = _get_user_or_404(db, user_id)
    user.is_active = True
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}")
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    user = _get_user_or_404(db, user_id)
    _ensure_not_self(current_user, user)
    if has_role(user, "admin_chief") and not has_role(current_user, "admin_chief"):
        raise HTTPException(status_code=403, detail="管理責任者の削除は管理責任者のみ可能です")
    _ensure_not_last_of_role(db, user)
    # 共有テーブルのため物理削除はせずソフトデリートする
    user.is_active = False
    user.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}


@router.post("/{user_id}/reset-password")
def reset_user_password(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    user = _get_user_or_404(db, user_id)
    password = secrets.token_urlsafe(10)
    user.password_hash = hash_password(password)
    db.commit()
    return {"initial_password": password}
