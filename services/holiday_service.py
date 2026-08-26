import random
import time
from datetime import datetime

from sqlalchemy import select

from database.db import AsyncSessionLocal
from database.models import CalendarDay, Holiday


async def _load_all_holidays():
    async with AsyncSessionLocal() as session:
        q = (
            select(
                CalendarDay.day,
                CalendarDay.month,
                Holiday.name,
                Holiday.description,
            )
            .join(Holiday, Holiday.calendar_day_id == CalendarDay.id)
            .order_by(CalendarDay.month, CalendarDay.day, Holiday.id)
        )
        rows = (await session.execute(q)).all()

        holidays: dict[str, list[dict]] = {}
        for day, month, name, description in rows:
            key = f"{int(day):02d}-{int(month):02d}"
            holidays.setdefault(key, []).append(
                {
                    "name": name,
                    "desc": description or "",
                }
            )
        return holidays


_CACHE: dict[str, list[dict]] | None = None
_CACHE_TS: float = 0.0
_CACHE_TTL: float = 60.0


def _parse_date_key(key: str) -> str | None:
    parts = key.split("-")
    if len(parts) != 2:
        return None
    day_s, month_s = parts
    if not day_s.isdigit() or not month_s.isdigit():
        return None
    day, month = int(day_s), int(month_s)
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    return f"{day:02d}-{month:02d}"


async def _ensure_cache() -> dict[str, list[dict]]:
    global _CACHE, _CACHE_TS
    now = time.monotonic()
    if _CACHE is not None and now - _CACHE_TS < _CACHE_TTL:
        return _CACHE
    _CACHE = await _load_all_holidays()
    _CACHE_TS = time.monotonic()
    return _CACHE


def invalidate_holidays_cache() -> None:
    global _CACHE, _CACHE_TS
    _CACHE = None
    _CACHE_TS = 0.0


def get_today_key() -> str:
    return datetime.now().strftime("%d-%m")


async def get_holidays_by_date(date: str):
    cache = await _ensure_cache()
    key = _parse_date_key(date)
    if key is None:
        return []
    return cache.get(key, [])


async def get_random_today():
    today = get_today_key()
    holidays = await get_holidays_by_date(today)

    if not holidays:
        return None

    return random.choice(holidays)


async def get_all_today_names():
    today = get_today_key()
    holidays = await get_holidays_by_date(today)

    return [h["name"] for h in holidays]


async def get_by_index(date: str, index: int):
    holidays = await get_holidays_by_date(date)

    if 0 <= index < len(holidays):
        return holidays[index]

    return None


async def search_holiday(query: str):
    cache = await _ensure_cache()
    results = []

    for date, holidays in cache.items():
        for h in holidays:
            if query.lower() in h["name"].lower():
                results.append({
                    "date": date,
                    "name": h["name"],
                    "desc": h["desc"]
                })

    return results[:5]
