"""全ユーザーの user_no を新採番ポリシー（数値5桁）で振り直す一回限りの保守スクリプト。

採番ポリシー（先頭桁＝ロール区分）:
  講師 (tutor)                       : 1nnnn  (10001〜)  ※新旧共通の通し番号
  保護者 (parent)                    : 2nnnn  (20001〜)  既存システム
  受付・再鑑・管理者(legacy在籍)      : 3nnnn  (30001〜)  既存システム
  学校 (school)                      : 4nnnn  (40001〜)  新システム
  事務・営業・経理(新のみのadmin)     : 5nnnn  (50001〜)  新システム

admin_master は登録元で決定: allowed_systems に 'legacy' を含めば 3nnnn、新システムのみなら 5nnnn。
講師は tutor_no も user_no と同値（数値）に揃える。

リレーション（報告書・紐付け・契約・招待）は全て UUID(id) で結合しているため、番号の振り直しは
参照整合性に影響しない。created_at 昇順で決定論的に採番するため、再実行しても同じ結果になる。
"""
from sqlalchemy import select

from app.database import SessionLocal
from app.models import User


def band_for_user(role: str, allowed_systems: list[str] | None, current_user_no: str | None = None) -> int:
    if role == "tutor":
        return 10000
    if role == "parent":
        return 20000
    if role in ("admin_receiver", "admin_reviewer"):
        return 30000
    if role == "admin_master":
        # admin_master は両システム所属(allowed_systems)になるため所属では判別できない。
        # 既に新システム経理帯(5xxxx)で採番済みなら新由来として維持、それ以外は既存管理者帯。
        if current_user_no and current_user_no.isdigit() and 50000 < int(current_user_no) < 60000:
            return 50000
        return 30000
    if role == "school":
        return 40000
    # office / sales / その他新システムスタッフ
    return 50000


def main() -> None:
    db = SessionLocal()
    try:
        users = db.scalars(select(User).order_by(User.created_at)).all()
        counters: dict[int, int] = {}
        for user in users:
            band = band_for_user(user.role, user.allowed_systems, user.user_no)
            counters[band] = counters.get(band, 0) + 1
            user.user_no = str(band + counters[band])
            if user.role == "tutor":
                user.tutor_no = user.user_no
        db.commit()
        summary = ", ".join(f"{band//10000}nnnn={n}件" for band, n in sorted(counters.items()))
        print(f"振り直したユーザー: {len(users)} 件（{summary}）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
