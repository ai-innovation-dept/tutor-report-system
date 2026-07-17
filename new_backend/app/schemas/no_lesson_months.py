"""講師の「当月授業なし」申請APIのスキーマ（202607161140）。"""
from pydantic import BaseModel


class NoLessonMonthsOut(BaseModel):
    months: list[str]


class NoLessonToggleIn(BaseModel):
    no_lesson: bool


class NoLessonToggleOut(BaseModel):
    target_month: str
    no_lesson: bool
