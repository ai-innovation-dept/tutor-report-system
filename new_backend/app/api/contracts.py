"""契約管理 API（経理 admin_master・管理責任者 admin_chief・営業 sales・事務 office）。

契約は (講師, 学校) ごとに1件で、work_assignment_profiles に格納する。
作成時に (講師, 学校) の assignment を取得/自動作成して紐付ける。
"""
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.dependencies.auth import require_role
from app.models.shared import User
from app.models.work import WorkAssignmentProfile
from app.schemas.contracts import (
    MAX_MAIN_TASKS,
    MAX_SUB_TASKS,
    ContractCreate,
    ContractForTutorOut,
    ContractOut,
    ContractTask,
    ContractUpdate,
    ContractWorkloadCase,
)
from app.services import contract_import_service
from app.services.assignment_service import get_or_create_new_assignment
from app.services.contract_form_service import build_column_definition

router = APIRouter(prefix="/api/w/contracts", tags=["work-contracts"])

_DETAIL_FIELDS = (
    "customer_id", "our_staff", "dispatch_place_address", "contract_start", "contract_end",
    "monthly_minutes", "weekly_lessons", "shift_note", "work_content",
    "scoring_enabled", "scoring_label", "scoring_unit", "scoring_task_id", "scoring_contract_id",
)


def _has_role(user: User | None, role: str) -> bool:
    if not user:
        return False
    return role in (list(user.roles or []) or ([user.role] if user.role else []))


# 委託業務カラム（メイン: task_name_N 等 / サブ: sub_task_name_N 等）の読み書き。
# prefix="" がメイン（最大3件）、prefix="sub_" がサブ（最大5件）。
def _tasks_to_columns(profile: WorkAssignmentProfile, tasks: list[ContractTask], prefix: str = "", max_count: int = MAX_MAIN_TASKS) -> None:
    for index in range(1, max_count + 1):
        task = tasks[index - 1] if index <= len(tasks) else None
        setattr(profile, f"{prefix}task_name_{index}", (task.task_name or None) if task else None)
        setattr(profile, f"{prefix}task_id_{index}", (task.task_id or None) if task else None)
        setattr(profile, f"{prefix}contract_id_{index}", (task.contract_id or None) if task else None)


def _tasks_from_columns(profile: WorkAssignmentProfile, prefix: str = "", max_count: int = MAX_MAIN_TASKS) -> list[ContractTask]:
    tasks: list[ContractTask] = []
    for index in range(1, max_count + 1):
        name = getattr(profile, f"{prefix}task_name_{index}")
        task_id = getattr(profile, f"{prefix}task_id_{index}")
        contract_id = getattr(profile, f"{prefix}contract_id_{index}")
        if name or task_id or contract_id:
            tasks.append(ContractTask(task_name=name, task_id=task_id, contract_id=contract_id))
    return tasks


def _to_out(profile: WorkAssignmentProfile) -> ContractOut:
    return ContractOut(
        id=profile.id,
        assignment_id=profile.assignment_id,
        tutor_id=profile.tutor_id,
        school_id=profile.school_id,
        tutor_name=profile.tutor.display_name if profile.tutor else None,
        school_name=profile.school.display_name if profile.school else None,
        customer_id=profile.customer_id,
        our_staff=profile.our_staff,
        dispatch_place_address=profile.dispatch_place_address,
        contract_start=profile.contract_start,
        contract_end=profile.contract_end,
        monthly_minutes=profile.monthly_minutes,
        weekly_lessons=profile.weekly_lessons,
        workload_cases=_workload_cases_from_json(profile),
        shift_note=profile.shift_note,
        work_content=profile.work_content,
        scoring_enabled=profile.scoring_enabled,
        scoring_label=profile.scoring_label,
        scoring_unit=profile.scoring_unit,
        scoring_task_id=profile.scoring_task_id,
        scoring_contract_id=profile.scoring_contract_id,
        tasks=_tasks_from_columns(profile),
        sub_tasks=_tasks_from_columns(profile, prefix="sub_", max_count=MAX_SUB_TASKS),
        is_active=profile.is_active,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _get_profile_loaded(db: Session, profile_id: uuid.UUID) -> WorkAssignmentProfile:
    profile = db.scalar(
        select(WorkAssignmentProfile)
        .options(selectinload(WorkAssignmentProfile.tutor), selectinload(WorkAssignmentProfile.school))
        .where(WorkAssignmentProfile.id == profile_id)
    )
    if not profile:
        raise HTTPException(status_code=404, detail="contract not found")
    return profile


def _workload_cases_to_json(payload: ContractCreate | ContractUpdate) -> list[dict]:
    """月時間・週コマのケースをJSONへ変換する。

    ケース未指定で旧来の単一値（monthly_minutes / weekly_lessons）だけが
    指定された場合（CSV取込など）は、契約期間を適用期間とした1ケースに合成する。
    """
    cases = list(payload.workload_cases)
    if not cases and (payload.monthly_minutes is not None or payload.weekly_lessons is not None):
        cases = [ContractWorkloadCase(
            monthly_minutes=payload.monthly_minutes,
            weekly_lessons=payload.weekly_lessons,
            start_date=payload.contract_start,
            end_date=payload.contract_end,
        )]
    return [case.model_dump(mode="json") for case in cases]


def _workload_cases_from_json(profile: WorkAssignmentProfile) -> list[ContractWorkloadCase]:
    return [ContractWorkloadCase(**case) for case in (profile.workload_cases or []) if isinstance(case, dict)]


def _apply_payload(profile: WorkAssignmentProfile, payload: ContractCreate) -> None:
    """契約詳細フィールドと委託業務をプロファイルへ反映する（作成・upsertで共用）。"""
    for field in _DETAIL_FIELDS:
        setattr(profile, field, getattr(payload, field))
    profile.workload_cases = _workload_cases_to_json(payload)
    _tasks_to_columns(profile, payload.tasks)
    _tasks_to_columns(profile, payload.sub_tasks, prefix="sub_", max_count=MAX_SUB_TASKS)


def _resolve_pair(db: Session, tutor_id: uuid.UUID, school_id: uuid.UUID) -> tuple[User, User]:
    tutor = db.get(User, tutor_id)
    if not _has_role(tutor, "tutor"):
        raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
    school = db.get(User, school_id)
    if not _has_role(school, "school"):
        raise HTTPException(status_code=422, detail="school_id must be a school user")
    return tutor, school


@router.get("", response_model=list[ContractOut])
def list_contracts(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    profiles = db.scalars(
        select(WorkAssignmentProfile)
        .options(selectinload(WorkAssignmentProfile.tutor), selectinload(WorkAssignmentProfile.school))
        .order_by(WorkAssignmentProfile.created_at.desc())
    ).all()
    return [_to_out(p) for p in profiles]


@router.post("", response_model=ContractOut, status_code=201)
def create_contract(
    payload: ContractCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    if not payload.tasks:
        raise HTTPException(status_code=422, detail="担当業務①は必須です")
    tutor, school = _resolve_pair(db, payload.tutor_id, payload.school_id)

    duplicate = db.scalar(
        select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == tutor.id,
            WorkAssignmentProfile.school_id == school.id,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="この講師と学校の契約は既に存在します")

    assignment = get_or_create_new_assignment(db, tutor, school)
    profile = WorkAssignmentProfile(
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        school_id=school.id,
        form_type="monthly_dispatch",
        contract_meta={},
        is_active=True,
    )
    _apply_payload(profile, payload)
    db.add(profile)
    db.commit()
    return _to_out(_get_profile_loaded(db, profile.id))


def _upsert_contract(db: Session, payload: ContractCreate) -> bool:
    """(講師,学校)が既存なら更新、無ければ作成。created(bool) を返す（commitは呼び出し側）。"""
    tutor, school = _resolve_pair(db, payload.tutor_id, payload.school_id)
    profile = db.scalar(
        select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == tutor.id,
            WorkAssignmentProfile.school_id == school.id,
        )
    )
    created = profile is None
    if created:
        assignment = get_or_create_new_assignment(db, tutor, school)
        profile = WorkAssignmentProfile(
            assignment_id=assignment.id,
            tutor_id=tutor.id,
            school_id=school.id,
            form_type="monthly_dispatch",
            contract_meta={},
            is_active=True,
        )
        db.add(profile)
    else:
        profile.is_active = True
    _apply_payload(profile, payload)
    return created


@router.get("/import-template")
def download_import_template(_: User = Depends(require_role("admin_master", "admin_chief", "sales", "office"))):
    """CSV一括登録用のテンプレート（UTF-8 BOM）をダウンロードする。"""
    return Response(
        content=contract_import_service.build_template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contract_import_template.csv"'},
    )


@router.post("/import")
def import_contracts(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    """CSVを一括取り込みする。1件でも検証エラーがあれば全件中止（何も登録しない）。

    (講師×学校)の重複は upsert（既存契約を上書き更新）する。
    """
    try:
        rows = contract_import_service.parse_rows(file.file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    payloads: list[ContractCreate] = []
    errors: list[str] = []
    seen: dict[tuple, int] = {}
    for offset, row in enumerate(rows):
        line_no = offset + 2  # ヘッダー(1行目)の次から
        if contract_import_service.is_skip_row(row):
            continue
        payload, row_errors = contract_import_service.row_to_payload(db, row)
        if row_errors:
            errors.extend(f"{line_no}行目: {message}" for message in row_errors)
            continue
        key = (payload.tutor_id, payload.school_id)
        if key in seen:
            errors.append(f"{line_no}行目: 同一CSV内で講師×学校が{seen[key]}行目と重複しています")
            continue
        seen[key] = line_no
        payloads.append(payload)

    if errors:
        raise HTTPException(status_code=400, detail={
            "message": f"取り込みできませんでした（{len(errors)}件のエラー）。修正して再度お試しください。",
            "errors": errors,
        })
    if not payloads:
        raise HTTPException(status_code=400, detail={
            "message": "取り込み対象の行がありません。記入例（講師番号が空または先頭#）以外の行を入力してください。",
            "errors": [],
        })

    created = updated = 0
    for payload in payloads:
        if _upsert_contract(db, payload):
            created += 1
        else:
            updated += 1
    db.commit()
    return {"imported": len(payloads), "created": created, "updated": updated}


@router.get("/for-tutor", response_model=list[ContractForTutorOut])
def list_contracts_for_tutor(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    """ログイン中の講師に紐づく契約一覧＋報告書フォーム用の動的列定義を返す。"""
    profiles = db.scalars(
        select(WorkAssignmentProfile)
        .options(selectinload(WorkAssignmentProfile.school))
        .where(WorkAssignmentProfile.tutor_id == user.id, WorkAssignmentProfile.is_active.is_(True))
    ).all()
    return [
        ContractForTutorOut(
            school_id=p.school_id,
            school_name=p.school.display_name if p.school else None,
            customer_id=p.customer_id,
            our_staff=p.our_staff,
            dispatch_place_address=p.dispatch_place_address,
            contract_start=p.contract_start,
            contract_end=p.contract_end,
            monthly_minutes=p.monthly_minutes,
            weekly_lessons=p.weekly_lessons,
            workload_cases=_workload_cases_from_json(p),
            shift_note=p.shift_note,
            work_content=p.work_content,
            tasks=_tasks_from_columns(p),
            sub_tasks=_tasks_from_columns(p, prefix="sub_", max_count=MAX_SUB_TASKS),
            column_definition=build_column_definition(p),
        )
        for p in profiles
    ]


@router.get("/{contract_id}", response_model=ContractOut)
def get_contract(
    contract_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    return _to_out(_get_profile_loaded(db, contract_id))


@router.patch("/{contract_id}", response_model=ContractOut)
def update_contract(
    contract_id: uuid.UUID,
    payload: ContractUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    profile = _get_profile_loaded(db, contract_id)
    data = payload.model_dump(exclude_unset=True)

    # 講師・学校の変更（指定時のみ）。変更時は assignment を再解決し重複を確認する。
    new_tutor_id = data.get("tutor_id", profile.tutor_id)
    new_school_id = data.get("school_id", profile.school_id)
    if new_tutor_id != profile.tutor_id or new_school_id != profile.school_id:
        tutor, school = _resolve_pair(db, new_tutor_id, new_school_id)
        duplicate = db.scalar(
            select(WorkAssignmentProfile).where(
                WorkAssignmentProfile.tutor_id == tutor.id,
                WorkAssignmentProfile.school_id == school.id,
                WorkAssignmentProfile.id != profile.id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="この講師と学校の契約は既に存在します")
        assignment = get_or_create_new_assignment(db, tutor, school)
        profile.tutor_id = tutor.id
        profile.school_id = school.id
        profile.assignment_id = assignment.id

    for field in _DETAIL_FIELDS:
        if field in data:
            setattr(profile, field, data[field])
    if "workload_cases" in data:
        profile.workload_cases = [case.model_dump(mode="json") for case in payload.workload_cases]
    if "tasks" in data:
        _tasks_to_columns(profile, payload.tasks)
    if "sub_tasks" in data:
        _tasks_to_columns(profile, payload.sub_tasks, prefix="sub_", max_count=MAX_SUB_TASKS)

    db.commit()
    return _to_out(_get_profile_loaded(db, profile.id))


@router.delete("/{contract_id}")
def delete_contract(
    contract_id: uuid.UUID,
    hard: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    """hard=False（既定）は論理削除（無効化）。hard=True は物理削除（行を完全に削除）。
    物理削除は契約レコードのみを対象とし、報告書（assignment 単位で保持）には影響しない。"""
    profile = _get_profile_loaded(db, contract_id)
    if hard:
        db.delete(profile)
    else:
        profile.is_active = False
    db.commit()
    return {"status": "ok"}
