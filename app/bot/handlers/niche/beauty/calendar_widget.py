import calendar
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_MONTHS_UA = [
    "", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
    "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень",
]
_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def make_calendar(
    year: int,
    month: int,
    nav_prefix: str,
    day_prefix: str,
    marked_days: set[int] | None = None,
) -> InlineKeyboardMarkup:
    """
    nav_prefix  — callback prefix for month navigation  e.g. 'tt_b_nav'
    day_prefix  — callback prefix for day selection     e.g. 'tt_b_day'
    marked_days — day numbers to mark with 📌 (admin schedule view)
    """
    today = date.today()
    marked = marked_days or set()

    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="◀️", callback_data=f"{nav_prefix}:{prev_y}:{prev_m:02d}"),
            InlineKeyboardButton(text=f"{_MONTHS_UA[month]} {year}", callback_data="tt_ignore"),
            InlineKeyboardButton(text="▶️", callback_data=f"{nav_prefix}:{next_y}:{next_m:02d}"),
        ],
        [InlineKeyboardButton(text=d, callback_data="tt_ignore") for d in _WEEKDAYS],
    ]

    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="tt_ignore"))
            else:
                d = date(year, month, day)
                if d < today:
                    row.append(InlineKeyboardButton(text="·", callback_data="tt_ignore"))
                else:
                    label = f"📌{day}" if day in marked else str(day)
                    row.append(InlineKeyboardButton(
                        text=label,
                        callback_data=f"{day_prefix}:{year}-{month:02d}-{day:02d}",
                    ))
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)
