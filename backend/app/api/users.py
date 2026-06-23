# === Phase 3: ユーザー管理 START ===
import secrets
import math
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.rbac import ADMIN_ROLES, has_role, is_admin, require_role, sync_user_roles
from app.core.security import hash_password, verify_password
from app.database import get_db
from app.deps import get_current_user
from app.models import Assignment, Invitation, LessonReport, ReportStatus, User
from app.api.invitations import _send_invitation_email, prepare_parent_invitation_for_assignment
from app.schemas import AssignmentCreate, AssignmentOut, AssignmentPatch, PasswordChange, StudentOption, UserCreate, UserListOut, UserOut, UserPatch, UserRolesPatch
from app.services import assignment_import_service, user_import_service
from app.services.user_import_service import create_initial_user, revive_user

router = APIRouter(prefix="/api", tags=["users"])


@router.post("/users")
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin_receiver", "admin_reviewer", "admin_master", "admin_chief"))):
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=409, detail="email already exists")
    password = payload.password or secrets.token_urlsafe(10)
    user = User(email=str(payload.email), role=payload.role, display_name=payload.display_name, phone=payload.phone, password_hash=hash_password(password))
    sync_user_roles(user, [payload.role])
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"user": UserOut.model_validate(user), "initial_password": password}


# 受付担当ロール以上で利用できるCSV一括エクスポート/取り込み。
# 注意: ルーティングの都合上、`/users/{user_id}` より前に定義すること（`export` が user_id として
# 解釈されるのを避けるため）。
_CSV_ROLES = ("admin_receiver", "admin_reviewer", "admin_master", "admin_chief")


@router.get("/users/export")
def export_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_CSV_ROLES)),
):
    """現在の登録ユーザー（既存システム）をCSV(UTF-8 BOM)でエクスポートする（バックアップ）。

    編集して /users/import で再取込できる（Noで照合し、メール・氏名のみを上書き更新）。
    一覧画面と同じ母集団（allowed_systems に "legacy" を含む未削除ユーザー）をNo昇順で出力する。
    """
    users = db.scalars(select(User).where(User.deleted_at.is_(None))).all()
    users = [u for u in users if "legacy" in (u.allowed_systems or [])]
    users = sorted(users, key=lambda u: int(u.user_no) if u.user_no and str(u.user_no).isdigit() else 999999)
    return Response(
        content=user_import_service.build_export_csv(users),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("ユーザー一覧.csv")},
    )


@router.post("/users/import")
def import_users(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*_CSV_ROLES)),
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


def _parse_roles(roles: str | None, role: str | None) -> set[str]:
    raw = roles or role or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def _active_admin_master_count(db: Session) -> int:
    return sum(
        1
        for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
        if has_role(user, "admin_master")
    )


def _active_admin_chief_count(db: Session) -> int:
    return sum(
        1
        for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
        if has_role(user, "admin_chief")
    )


def _ensure_not_last_admin_master(user: User, db: Session) -> None:
    if has_role(user, "admin_master") and user.is_active and _active_admin_master_count(db) <= 1:
        raise HTTPException(status_code=409, detail="最後の管理者のため操作できません")
    if has_role(user, "admin_chief") and user.is_active and _active_admin_chief_count(db) <= 1:
        raise HTTPException(status_code=409, detail="最後の管理責任者のため操作できません")


# 承認フローが「進行中」とみなす報告書ステータス＝下書き・最終承認済み・クローズ「以外」。
# これらに達していれば当人の関与は終わっており、安全に削除できる。
_FLOW_SETTLED_STATUSES = (
    ReportStatus.draft.value,
    ReportStatus.admin_approved.value,
    ReportStatus.closed.value,
)


def _ensure_no_active_approval_flow(user: User, db: Session) -> None:
    """承認フロー進行中の報告書に当人(講師 or 保護者)が関与している間は削除を止める。

    soft-delete 自体はデータ整合性(UUID参照)を壊さないが、削除すると本人しか進められない
    承認（保護者の承認、差戻し中の講師の再提出）が停滞する。最終承認/クローズに達してから、
    または担当の付け替え（保護者の紐づけ変更で報告書のparent_idも更新される）後に削除する想定。
    運営ロール(受付/再鑑/管理者)は報告書のtutor_id/parent_idに紐づかないため該当しない。
    """
    active_count = db.scalar(
        select(func.count())
        .select_from(LessonReport)
        .where(
            or_(LessonReport.tutor_id == user.id, LessonReport.parent_id == user.id),
            LessonReport.status.notin_(_FLOW_SETTLED_STATUSES),
        )
    )
    if active_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"このユーザーは承認フロー進行中の報告書に関与しているため削除できません（{active_count}件）。"
                "対象の報告書が最終承認またはクローズされてから、もしくは担当の付け替え後に削除してください。"
            ),
        )


@router.get("/users", response_model=UserListOut)
def list_users(
    page: int = 1,
    per_page: int = 50,
    roles: str | None = None,
    role: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    page = max(1, page)
    per_page = min(max(1, per_page), 100)
    stmt = select(User).where(User.deleted_at.is_(None))
    if search and search.strip():
        keyword = f"%{search.strip().lower()}%"
        # 氏名・メールに加えて No（user_no / tutor_no）でも検索可能にする。
        # 担当管理など No 中心の運用でタイプアヘッド検索を効かせるため。NULL 列は like が
        # NULL を返し OR では非マッチ扱いになるので安全。
        stmt = stmt.where(or_(
            func.lower(User.display_name).like(keyword),
            func.lower(User.email).like(keyword),
            func.lower(User.user_no).like(keyword),
            func.lower(User.tutor_no).like(keyword),
        ))
    users = db.scalars(stmt).all()
    # 所属の唯一の基準は allowed_systems。既存システム(legacy)に登録のあるユーザーのみ表示する。
    users = [user for user in users if "legacy" in (user.allowed_systems or [])]
    # No の小さい順（数値）でソート。user_no 未設定は末尾。
    users = sorted(users, key=lambda u: int(u.user_no) if u.user_no and str(u.user_no).isdigit() else 999999)
    role_counts = {key: 0 for key in ["all", "tutor", "parent", "admin_receiver", "admin_reviewer", "admin_master", "admin_chief"]}
    role_counts["all"] = len(users)
    for user in users:
        for user_role in user.roles or [user.role]:
            if user_role in role_counts:
                role_counts[user_role] += 1
    selected_roles = _parse_roles(roles, role)
    if selected_roles:
        users = [user for user in users if any(has_role(user, selected_role) for selected_role in selected_roles)]
    total = len(users)
    total_pages = max(1, math.ceil(total / per_page))
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    return UserListOut(
        items=users[start : start + per_page],
        total=total,
        total_pages=total_pages,
        page=page,
        per_page=per_page,
        role_counts=role_counts,
        active_admin_master_count=_active_admin_master_count(db),
        active_admin_chief_count=_active_admin_chief_count(db),
    )


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role(*ADMIN_ROLES))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(user_id: UUID, payload: UserPatch, db: Session = Depends(get_db), current_user: User = Depends(require_role("admin_receiver", "admin_reviewer", "admin_master", "admin_chief"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    data = payload.model_dump(exclude_unset=True)
    if "skip_parent_approval" in data and not has_role(current_user, "admin_chief"):
        raise HTTPException(status_code=403, detail="保護者承認スキップの設定は管理責任者のみ可能です")
    if "role" in data and data["role"]:
        sync_user_roles(user, [data.pop("role")])
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/roles", response_model=UserOut)
def patch_user_roles(user_id: UUID, payload: UserRolesPatch, db: Session = Depends(get_db), _: User = Depends(require_role("admin_receiver", "admin_reviewer", "admin_master", "admin_chief"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    sync_user_roles(user, payload.roles)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/disable")
def disable_user(user_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(require_role("admin_receiver", "admin_reviewer", "admin_master", "admin_chief"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    if has_role(user, "admin_chief") and not has_role(current_user, "admin_chief"):
        raise HTTPException(status_code=403, detail="管理責任者の無効化は管理責任者のみ可能です")
    _ensure_not_last_admin_master(user, db)
    user.is_active = False
    db.commit()
    return {"status": "disabled"}


@router.patch("/users/{user_id}/enable")
def enable_user(user_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role("admin_receiver", "admin_reviewer", "admin_master", "admin_chief"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    user.is_active = True
    db.commit()
    return {"status": "enabled"}


@router.delete("/users/{user_id}")
def delete_user(user_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(require_role("admin_receiver", "admin_reviewer", "admin_master", "admin_chief"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    if has_role(user, "admin_chief") and not has_role(current_user, "admin_chief"):
        raise HTTPException(status_code=403, detail="管理責任者の削除は管理責任者のみ可能です")
    _ensure_not_last_admin_master(user, db)
    _ensure_no_active_approval_flow(user, db)
    user.deleted_at = datetime.now(timezone.utc)
    user.is_active = False
    db.commit()
    return {"status": "deleted"}


@router.post("/users/me/password")
def change_password(payload: PasswordChange, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="current password mismatch")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"status": "ok"}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role("admin_receiver", "admin_reviewer", "admin_master", "admin_chief"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    password = secrets.token_urlsafe(10)
    user.password_hash = hash_password(password)
    db.commit()
    return {"initial_password": password}


@router.post("/assignments", response_model=AssignmentOut)
async def create_assignment(payload: AssignmentCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = payload.model_dump()
    parent_email = data.pop("parent_email", None)
    if user.role == "tutor":
        if payload.tutor_id != user.id:
            raise HTTPException(status_code=403, detail="cannot create assignments for another tutor")
        data["parent_id"] = None
    elif not is_admin(user):
        # 担当管理（作成）は運営4ロール（受付・再鑑・管理者・管理責任者）が利用可
        raise HTTPException(status_code=403, detail="not allowed")
    else:
        tutor = db.get(User, payload.tutor_id)
        if not tutor or not has_role(tutor, "tutor"):
            raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
        if payload.parent_id:
            parent = db.get(User, payload.parent_id)
            if not parent or not has_role(parent, "parent"):
                raise HTTPException(status_code=422, detail="parent_id must be a parent user")
    if parent_email and data.get("parent_id"):
        raise HTTPException(status_code=422, detail="parent_email and parent_id cannot both be set")
    duplicate = db.scalar(
        select(Assignment).where(
            Assignment.tutor_id == payload.tutor_id,
            Assignment.student_name == payload.student_name,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="assignment already exists")
    assignment = Assignment(**data)
    db.add(assignment)
    invitation = None
    should_send = False
    if parent_email:
        db.flush()
        invitation, _, should_send = prepare_parent_invitation_for_assignment(str(parent_email), assignment, db, user)
    db.commit()
    if invitation and should_send:
        invitation = db.scalar(
            select(Invitation)
            .options(selectinload(Invitation.assignment).selectinload(Assignment.tutor))
            .where(Invitation.id == invitation.id)
        )
        await _send_invitation_email(invitation, request)
    assignment = db.scalar(
        select(Assignment)
        .options(selectinload(Assignment.tutor), selectinload(Assignment.parent))
        .where(Assignment.id == assignment.id)
    )
    return assignment


# 担当管理（編集）は運営4ロールが利用可。
# admin_chief は案件の全項目を編集可、それ以外の運営（受付・再鑑・管理者）は
# skip_parent_approval 以外の全項目を編集可（スキップ設定は管理責任者のみ）。
# reminder_count は「エンドレス送信」化により既存システムでは未使用だが、
# assignments テーブルを共有する新システムが利用するため列・編集経路は残す。
_SKIP_PARENT_FIELD = "skip_parent_approval"


@router.patch("/assignments/{assignment_id}", response_model=AssignmentOut)
def patch_assignment(assignment_id: UUID, payload: AssignmentPatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assignment = db.get(Assignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="assignment not found")
    if has_role(current_user, "admin_chief"):
        data = payload.model_dump(exclude_unset=True)
    elif is_admin(current_user):
        data = payload.model_dump(exclude_unset=True)
        if _SKIP_PARENT_FIELD in data:
            raise HTTPException(status_code=403, detail="保護者承認スキップの設定は管理責任者のみ可能です")
    else:
        raise HTTPException(status_code=403, detail="insufficient role")
    if "tutor_id" in data and data["tutor_id"] is not None:
        tutor = db.get(User, data["tutor_id"])
        if not tutor or not has_role(tutor, "tutor"):
            raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
    if "parent_id" in data and data["parent_id"] is not None:
        parent = db.get(User, data["parent_id"])
        if not parent or not has_role(parent, "parent"):
            raise HTTPException(status_code=422, detail="parent_id must be a parent user")
    for key, value in data.items():
        setattr(assignment, key, value)
    if "parent_id" in data:
        db.query(LessonReport).filter(LessonReport.assignment_id == assignment.id).update({"parent_id": data["parent_id"]}, synchronize_session=False)
    db.commit()
    assignment = db.scalar(
        select(Assignment)
        .options(selectinload(Assignment.tutor), selectinload(Assignment.parent))
        .where(Assignment.id == assignment.id)
    )
    return assignment


@router.delete("/assignments/{assignment_id}")
def delete_assignment(assignment_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role("admin_receiver", "admin_reviewer", "admin_master", "admin_chief"))):
    """担当（assignment）を物理削除する。報告書が紐づく場合は履歴保持のため削除を拒否し、無効化を案内する。"""
    assignment = db.get(Assignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="assignment not found")
    report_count = db.scalar(select(func.count()).select_from(LessonReport).where(LessonReport.assignment_id == assignment_id))
    if report_count:
        raise HTTPException(status_code=409, detail="この担当には報告書があるため削除できません。無効化をご利用ください。")
    db.delete(assignment)
    db.commit()
    return {"status": "deleted"}


@router.get("/assignments/students", response_model=list[StudentOption])
def list_assignment_students(
    q: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    """担当管理の生徒選択（タイプアヘッド）用に、既存の担当から生徒候補を返す。

    生徒は (生徒名, 保護者) の一意な組として扱う（生徒マスタは持たない）。同じ生徒に複数の
    講師が付いていても1件に集約する。キーワード q は生徒名・保護者名・保護者No を部分一致
    （大文字小文字無視）で絞り込む。数千件規模を想定し、サーバー側で集約・件数制限してから
    返す。新システム（system_type='new'）の学校紐付けは対象外。
    """
    limit = min(max(1, limit), 50)
    stmt = (
        select(Assignment.student_name, Assignment.parent_id, User.display_name, User.user_no)
        .outerjoin(User, Assignment.parent_id == User.id)
        .where(or_(Assignment.system_type != "new", Assignment.system_type.is_(None)))
        .group_by(Assignment.student_name, Assignment.parent_id, User.display_name, User.user_no)
        .order_by(Assignment.student_name)
    )
    if q and q.strip():
        keyword = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(Assignment.student_name).like(keyword),
            func.lower(User.display_name).like(keyword),
            func.lower(User.user_no).like(keyword),
        ))
    rows = db.execute(stmt.limit(limit)).all()
    return [
        StudentOption(student_name=name, parent_id=parent_id, parent_name=parent_name, parent_no=parent_no)
        for (name, parent_id, parent_name, parent_no) in rows
    ]


@router.get("/assignments/export")
def export_assignments(db: Session = Depends(get_db), _: User = Depends(require_role(*_CSV_ROLES))):
    """現在の担当（既存システム）をCSV(UTF-8 BOM)でエクスポートする（バックアップ）。

    編集して /assignments/import で再取込できる（講師No＋生徒名で照合し、保護者の紐づけを更新）。
    新システム(system_type='new')の学校紐付けは除外し、講師No・生徒名の昇順で出力する。
    """
    assignments = db.scalars(
        select(Assignment)
        .options(selectinload(Assignment.tutor), selectinload(Assignment.parent))
        .where(or_(Assignment.system_type != "new", Assignment.system_type.is_(None)))
    ).all()
    assignments = sorted(assignments, key=lambda a: (
        int(a.tutor.user_no) if a.tutor and a.tutor.user_no and str(a.tutor.user_no).isdigit() else 999999,
        a.student_name or "",
    ))
    return Response(
        content=assignment_import_service.build_export_csv(assignments),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("担当一覧.csv")},
    )


@router.post("/assignments/import")
def import_assignments(file: UploadFile = File(...), db: Session = Depends(get_db), _: User = Depends(require_role(*_CSV_ROLES))):
    """担当CSVを一括取り込みする。1件でも検証エラーがあれば全件中止（何も登録しない）。

    照合キー=(講師No, 生徒名)。一致する担当があれば保護者の紐づけを上書き更新、無ければ新規作成。
    保護者No 空欄=保護者未設定／記入かつ該当する保護者が居ない=エラー。講師Noは既存講師が必須。
    """
    try:
        rows = assignment_import_service.parse_rows(file.file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    resolved: list[dict] = []  # {"tutor","student_name","parent","line"}
    errors: list[str] = []
    seen: dict[tuple, int] = {}
    for offset, row in enumerate(rows):
        line_no = offset + 2  # ヘッダー(1行目)の次から
        if assignment_import_service.is_skip_row(row):
            continue
        res, row_errors = assignment_import_service.resolve_row(db, row)
        if row_errors:
            errors.extend(f"{line_no}行目: {message}" for message in row_errors)
            continue
        key = (res["tutor"].id, res["student_name"])
        if key in seen:
            errors.append(f"{line_no}行目: 同一CSV内で講師No＋生徒名が{seen[key]}行目と重複しています")
            continue
        seen[key] = line_no
        res["line"] = line_no
        resolved.append(res)

    if errors:
        raise HTTPException(status_code=400, detail={
            "message": f"取り込みできませんでした（{len(errors)}件のエラー）。修正して再度お試しください。",
            "errors": errors,
        })
    if not resolved:
        raise HTTPException(status_code=400, detail={
            "message": "取り込み対象の行がありません。講師No・生徒名（必要に応じて保護者No）を入力してください。",
            "errors": [],
        })

    created = 0
    updated = 0
    for res in resolved:
        parent_id = res["parent"].id if res["parent"] else None
        existing = db.scalar(
            select(Assignment).where(
                Assignment.tutor_id == res["tutor"].id,
                Assignment.student_name == res["student_name"],
                or_(Assignment.system_type != "new", Assignment.system_type.is_(None)),
            )
        )
        if existing:
            existing.parent_id = parent_id
            # 承認先を一致させるため、紐づく報告書の parent_id も更新する（PATCHと同じ挙動）。
            db.query(LessonReport).filter(LessonReport.assignment_id == existing.id).update({"parent_id": parent_id}, synchronize_session=False)
            updated += 1
        else:
            db.add(Assignment(tutor_id=res["tutor"].id, parent_id=parent_id, student_name=res["student_name"], is_active=True))
            created += 1
    db.commit()
    return {"imported": len(resolved), "created": created, "updated": updated}


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(Assignment).options(selectinload(Assignment.tutor), selectinload(Assignment.parent)).order_by(Assignment.created_at.desc())
    # 業務連絡表システム（新システム）が作成した学校紐付け（system_type='new'）を除外し、
    # 本システムのレコードのみ返す。NULL は legacy 扱い（過去データ・新カラム導入前の挿入分）。
    stmt = stmt.where(or_(Assignment.system_type != "new", Assignment.system_type.is_(None)))
    if user.role == "tutor":
        stmt = stmt.where(Assignment.tutor_id == user.id, Assignment.is_active.is_(True))
    elif user.role == "parent":
        stmt = stmt.where(Assignment.parent_id == user.id, Assignment.is_active.is_(True))
    elif not is_admin(user):
        raise HTTPException(status_code=403, detail="not allowed")
    return db.scalars(stmt).all()
# === Phase 3 END ===
