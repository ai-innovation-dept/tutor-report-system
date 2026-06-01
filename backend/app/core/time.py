from datetime import date, datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def get_current_jst() -> datetime:
    return datetime.now(JST)


def get_current_jst_date() -> date:
    return get_current_jst().date()


def get_current_jst_month() -> str:
    return get_current_jst().strftime("%Y-%m")


def month_string(value: date | str) -> str:
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    return value[:7]
