"""契約管理 API（経理 admin_master・管理責任者 admin_chief・営業 sales・事務 office）。

契約は (講師, 学校) ごとに1件で、work_assignment_profiles に格納する。
作成時に (講師, 学校) の assignment を取得/自動作成して紐付ける。
"""
import re
import uuid
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
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
    ContractPeriodSlot,
    ContractTask,
    ContractUpdate,
    ContractWorkloadCase,
    term_payload_errors,
)
from app.services import contract_import_service
from app.services.assignment_service import get_or_create_new_assignment
from app.services.contract_form_service import build_column_definition

router = APIRouter(prefix="/api/w/contracts", tags=["work-contracts"])

_DETAIL_FIELDS = (
    "customer_id", "our_staff", "dispatch_place_address", "work_location", "classroom_name", "contract_start", "contract_end",
    "monthly_minutes", "weekly_lessons", "shift_note", "work_content",
    "scoring_enabled", "scoring_label", "scoring_unit", "scoring_task_id", "scoring_contract_id",
    "show_dispatch_address", "show_work_content", "show_commuter_pass", "show_break_minutes", "show_schedule_note",
)
# CSVは報告書の表示項目フラグ・コマ設定を扱わないため、CSV upsert の更新では既存値を保持する（ドロワー管理）。
_CSV_PRESERVED_FIELDS = (
    "show_dispatch_address", "show_work_content", "show_commuter_pass", "show_break_minutes", "show_schedule_note",
    "period_slots",
)


def _has_role(user: User | None, role: str) -> bool:
    if not user:
        return False
    return role in (list(user.roles or []) or ([user.role] if user.role else []))


# 委託業務カラム（メイン: task_name_N 等 / サブ: sub_task_name_N 等）の読み書き。
# prefix="" がメイン（前期=1/後期=2の位置固定）、prefix="sub_" がサブ（最大5件）。
def _tasks_to_columns(profile: WorkAssignmentProfile, tasks: list[ContractTask], prefix: str = "", max_count: int = MAX_MAIN_TASKS) -> None:
    for index in range(1, max_count + 1):
        task = tasks[index - 1] if index <= len(tasks) else None
        setattr(profile, f"{prefix}task_name_{index}", (task.task_name or None) if task else None)
        setattr(profile, f"{prefix}task_id_{index}", (task.task_id or None) if task else None)
        setattr(profile, f"{prefix}contract_id_{index}", (task.contract_id or None) if task else None)


def _tasks_from_columns(
    profile: WorkAssignmentProfile, prefix: str = "", max_count: int = MAX_MAIN_TASKS, positional: bool = False,
) -> list[ContractTask]:
    """委託業務カラムをリスト化する。positional=True（メイン用）は [0]=前期/[1]=後期 の位置を
    保ったまま返す（欠けた期は空タスク。末尾の空きは詰める）。サブは従来どおり空行を詰める。"""
    tasks: list[ContractTask] = []
    for index in range(1, max_count + 1):
        name = getattr(profile, f"{prefix}task_name_{index}")
        task_id = getattr(profile, f"{prefix}task_id_{index}")
        contract_id = getattr(profile, f"{prefix}contract_id_{index}")
        if name or task_id or contract_id:
            tasks.append(ContractTask(task_name=name, task_id=task_id, contract_id=contract_id))
        elif positional:
            tasks.append(ContractTask())
    if positional:
        while tasks and tasks[-1].is_empty():
            tasks.pop()
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
        work_location=profile.work_location,
        classroom_name=profile.classroom_name,
        show_dispatch_address=profile.show_dispatch_address,
        show_work_content=profile.show_work_content,
        show_commuter_pass=profile.show_commuter_pass,
        show_break_minutes=profile.show_break_minutes,
        show_schedule_note=profile.show_schedule_note,
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
        tasks=_tasks_from_columns(profile, positional=True),
        sub_tasks=_tasks_from_columns(profile, prefix="sub_", max_count=MAX_SUB_TASKS),
        period_slots=_period_slots_from_json(profile),
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
    """期別設定（月時間・週コマ・適用期間・コマ設定）のケースをJSONへ変換する。"""
    return [case.model_dump(mode="json") for case in payload.workload_cases]


def _workload_cases_from_json(profile: WorkAssignmentProfile) -> list[ContractWorkloadCase]:
    return [ContractWorkloadCase(**case) for case in (profile.workload_cases or []) if isinstance(case, dict)]


def _period_slots_from_json(profile: WorkAssignmentProfile) -> list[ContractPeriodSlot]:
    # 表示・自動計算とも開始時刻順（①=最も早い時間帯）で扱う（保存時の正規化と同じ並び）
    slots = [ContractPeriodSlot(**slot) for slot in (profile.period_slots or []) if isinstance(slot, dict)]
    return sorted(slots, key=lambda slot: slot.start)


def _apply_payload(profile: WorkAssignmentProfile, payload: ContractCreate) -> None:
    """契約詳細フィールドと委託業務をプロファイルへ反映する（作成・upsertで共用）。"""
    for field in _DETAIL_FIELDS:
        setattr(profile, field, getattr(payload, field))
    profile.workload_cases = _workload_cases_to_json(payload)
    profile.period_slots = [slot.model_dump() for slot in payload.period_slots]
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
    term_errors = term_payload_errors(payload.tasks, payload.workload_cases)
    if term_errors:
        raise HTTPException(status_code=422, detail="／".join(term_errors))
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
        _apply_payload(profile, payload)
    else:
        profile.is_active = True
        # 既存契約の表示項目フラグ・コマ設定（CSV対象外）を保持してから上書きする
        preserved = {field: getattr(profile, field) for field in _CSV_PRESERVED_FIELDS}
        old_cases = [case for case in (profile.workload_cases or []) if isinstance(case, dict)]
        _apply_payload(profile, payload)
        for field, value in preserved.items():
            setattr(profile, field, value)
        # CSVは期別のコマ設定(slots)を扱わないため、既存ケースのコマ設定を task_index で引き継ぐ
        old_slots = {int(case.get("task_index") or 1): case["slots"] for case in old_cases if case.get("slots")}
        profile.workload_cases = [
            ({**case, "slots": old_slots[int(case.get("task_index") or 1)]}
             if not case.get("slots") and int(case.get("task_index") or 1) in old_slots else case)
            for case in (profile.workload_cases or [])
        ]
    return created


@router.get("/export")
def export_contracts(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    """現在の契約（有効）をCSV(UTF-8 BOM)でエクスポートする。編集して /import で再取込できる（番号で照合・upsert）。"""
    profiles = db.scalars(
        select(WorkAssignmentProfile)
        .options(selectinload(WorkAssignmentProfile.tutor), selectinload(WorkAssignmentProfile.school))
        .where(WorkAssignmentProfile.is_active.is_(True))
        .order_by(WorkAssignmentProfile.created_at.desc())
    ).all()
    return Response(
        content=contract_import_service.build_export_csv(profiles),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("契約一覧.csv")},
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


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@router.get("/for-tutor", response_model=list[ContractForTutorOut])
def list_contracts_for_tutor(
    target_month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    """ログイン中の講師に紐づく契約一覧＋報告書フォーム用の動的列定義を返す。

    列定義は対象月（target_month。省略時は現在月）に適用される期（前期/後期）の
    担当業務のみを含む（新規報告書は当月のみ作成できるため、画面は当月を渡す）。
    """
    if not (target_month and _MONTH_RE.match(target_month)):
        target_month = datetime.now(ZoneInfo(settings.TIMEZONE)).strftime("%Y-%m")
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
            work_location=p.work_location,
            classroom_name=p.classroom_name,
            show_dispatch_address=p.show_dispatch_address,
            show_work_content=p.show_work_content,
            show_commuter_pass=p.show_commuter_pass,
            show_break_minutes=p.show_break_minutes,
            show_schedule_note=p.show_schedule_note,
            contract_start=p.contract_start,
            contract_end=p.contract_end,
            monthly_minutes=p.monthly_minutes,
            weekly_lessons=p.weekly_lessons,
            workload_cases=_workload_cases_from_json(p),
            shift_note=p.shift_note,
            work_content=p.work_content,
            tasks=_tasks_from_columns(p, positional=True),
            sub_tasks=_tasks_from_columns(p, prefix="sub_", max_count=MAX_SUB_TASKS),
            period_slots=_period_slots_from_json(p),
            column_definition=build_column_definition(p, target_month),
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
    if "period_slots" in data:
        profile.period_slots = [slot.model_dump() for slot in payload.period_slots]
    if "tasks" in data:
        _tasks_to_columns(profile, payload.tasks)
    if "sub_tasks" in data:
        _tasks_to_columns(profile, payload.sub_tasks, prefix="sub_", max_count=MAX_SUB_TASKS)

    # 担当業務・期別設定を変更する更新は、最終状態で前期/後期の必須（名称・適用期間・重複なし）を検証する。
    # 他フィールドのみの部分更新は旧形式の契約でも通す（新仕様は契約を編集した時点から適用）。
    if "tasks" in data or "workload_cases" in data:
        term_errors = term_payload_errors(
            _tasks_from_columns(profile, positional=True),
            _workload_cases_from_json(profile),
        )
        if term_errors:
            raise HTTPException(status_code=422, detail="／".join(term_errors))

    # 部分更新（片方だけ送信）でもコマ設定×休憩非表示の組み合わせにならないよう最終状態で検証する
    has_slots = bool(profile.period_slots) or any(
        case.get("slots") for case in (profile.workload_cases or []) if isinstance(case, dict)
    )
    if has_slots and profile.show_break_minutes is False:
        raise HTTPException(
            status_code=422,
            detail="休憩時間を非表示にしている契約ではコマ設定を使用できません（表示項目の「休憩時間」をONにしてください）",
        )

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
