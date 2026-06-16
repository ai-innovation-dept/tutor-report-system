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
from app.schemas.users import UserListOut, UserOut, UserPatch, UserRolesPatch
from app.services import user_import_service

router = APIRouter(prefix="/api/w/users", tags=["work-users"])

# 経理画面のロールタブと一致させる
ROLE_TAB_KEYS = ["tutor", "school", "sales", "office", "admin_master", "admin_chief"]
# ロール編集を許可する組み合わせ（営業・事務スタッフのみ付け替え可能）
EDITABLE_STAFF_ROLES = {"sales", "office"}


def _user_roles(user: User) -> list[str]:
    return list(user.roles or []) or ([user.role] if user.role else [])


def _active_admin_master_count(db: Session) -> int:
    users = db.scalars(
        select(User).where(User.deleted_at.is_(None), User.is_active.is_(True))
    ).all()
    return sum(1 for u in users if "admin_master" in _user_roles(u))


def _active_admin_chief_count(db: Session) -> int:
    users = db.scalars(
        select(User).where(User.deleted_at.is_(None), User.is_active.is_(True))
    ).all()
    return sum(1 for u in users if "admin_chief" in _user_roles(u))


def _get_user_or_404(db: Session, user_id: UUID) -> User:
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    return user


def _ensure_not_last_admin(db: Session, user: User) -> None:
    if (
        user.is_active
        and "admin_master" in _user_roles(user)
        and _active_admin_master_count(db) <= 1
    ):
        raise HTTPException(status_code=409, detail="最後の経理ユーザーのため操作できません")
    if (
        user.is_active
        and "admin_chief" in _user_roles(user)
        and _active_admin_chief_count(db) <= 1
    ):
        raise HTTPException(status_code=409, detail="最後の管理責任者のため操作できません")


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
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    """CSVを一括取り込みする。1件でも検証エラーがあれば全件中止（何も更新しない）。

    現フェーズ①は「既存ユーザーの更新のみ」。Noが一致した既存ユーザーのメール・氏名を上書きする。
    （新規作成＝No空欄は次フェーズ②で対応。）メールは他ユーザーと重複しないことを検証する。
    """
    try:
        rows = user_import_service.parse_rows(file.file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    updates: list[dict] = []  # {"user", "email", "name", "line"}
    errors: list[str] = []
    seen_user: dict[UUID, int] = {}
    for offset, row in enumerate(rows):
        line_no = offset + 2  # ヘッダー(1行目)の次から
        if user_import_service.is_skip_row(row):
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

    # メール重複チェック: 同一CSV内の重複と、他の既存ユーザー（自分自身を除く）との衝突を検出する。
    email_line: dict[str, int] = {}
    for item in updates:
        key = item["email"].lower()
        if key in email_line:
            errors.append(f"{item['line']}行目: メールアドレス「{item['email']}」が{email_line[key]}行目と重複しています")
        else:
            email_line[key] = item["line"]
    if updates:
        lowered = list({item["email"].lower() for item in updates})
        existing = db.scalars(select(User).where(func.lower(User.email).in_(lowered))).all()
        holders: dict[str, list[User]] = {}
        for u in existing:
            holders.setdefault(u.email.lower(), []).append(u)
        for item in updates:
            if any(h.id != item["user"].id for h in holders.get(item["email"].lower(), [])):
                errors.append(f"{item['line']}行目: メールアドレス「{item['email']}」は既に他のユーザーが使用しています")

    if errors:
        raise HTTPException(status_code=400, detail={
            "message": f"取り込みできませんでした（{len(errors)}件のエラー）。修正して再度お試しください。",
            "errors": errors,
        })
    if not updates:
        raise HTTPException(status_code=400, detail={
            "message": "取り込み対象の行がありません。Noで照合できる既存ユーザーの行を入力してください。",
            "errors": [],
        })

    for item in updates:
        item["user"].email = item["email"]
        item["user"].display_name = item["name"]
    db.commit()
    return {"imported": len(updates), "created": 0, "updated": len(updates)}


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
        _ensure_not_last_admin(db, user)
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
    if has_role(user, "admin_chief") and not has_role(current_user, "admin_chief"):
        raise HTTPException(status_code=403, detail="管理責任者の無効化は管理責任者のみ可能です")
    _ensure_not_last_admin(db, user)
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
    if has_role(user, "admin_chief") and not has_role(current_user, "admin_chief"):
        raise HTTPException(status_code=403, detail="管理責任者の削除は管理責任者のみ可能です")
    _ensure_not_last_admin(db, user)
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
