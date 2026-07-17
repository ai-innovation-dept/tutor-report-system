"""学校の締め日通知設定APIのスキーマ（202607161140）。"""
import re
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# 対象月（YYYY-MM）の形式。締め日設定と「当月授業なし」申請APIで共用する
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class SchoolDeadlineOut(BaseModel):
    target_month: str
    deadline_date: date
    notice_sent_at: datetime | None

    model_config = {"from_attributes": True}


class SchoolSettingsOut(BaseModel):
    early_check_enabled: bool
    notice_days_before: int
    year: int
    deadlines: list[SchoolDeadlineOut]


class SchoolSettingsIn(BaseModel):
    early_check_enabled: bool
    # 締め日の何日前に確認メールを送るか（0=締め日当日のみ）
    notice_days_before: int = Field(ge=0, le=60)
    # 画面で表示中の年（保存後の返却用）
    year: int = Field(ge=2000, le=2100)
    # {対象月 YYYY-MM: 締め日 or None(削除)}。渡した月のみ更新・削除する
    deadlines: dict[str, date | None] = Field(default_factory=dict)

    @field_validator("deadlines")
    @classmethod
    def _validate_month_keys(cls, value: dict[str, date | None]) -> dict[str, date | None]:
        for month in value:
            if not MONTH_PATTERN.match(month):
                raise ValueError(f"対象月の形式が不正です: {month}（YYYY-MM で指定してください）")
        return value
