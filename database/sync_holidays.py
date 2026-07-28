"""
One-time script: populate holidays table from storage/holidays.json.

Reads holidays from JSON, links each to a calendar_day, and inserts
new entries on subsequent runs (safe to re-run).

Usage:
    python database/sync_holidays.py
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from database.db import AsyncSessionLocal
from database.models import CalendarDay, Holiday
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

HOLIDAYS_JSON = Path(__file__).resolve().parent.parent / "storage" / "holidays.json"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_date_key(key: str) -> tuple[int, int] | None:
    """Parse 'DD-MM' key into (day, month)."""
    parts = key.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        day, month = int(parts[0]), int(parts[1])
        if 1 <= day <= 31 and 1 <= month <= 12:
            return day, month
    except ValueError:
        pass
    return None


async def _get_or_create_calendar_day(session, day: int, month: int) -> int:
    result = await session.execute(
        select(CalendarDay).where(CalendarDay.day == day, CalendarDay.month == month)
    )
    cal_day = result.scalar_one_or_none()
    if cal_day is not None:
        return cal_day.id

    cal_day = CalendarDay(day=day, month=month)
    session.add(cal_day)
    await session.flush()
    return cal_day.id


async def sync_holidays() -> None:
    with open(HOLIDAYS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    inserted = 0
    skipped = 0

    async with AsyncSessionLocal() as session:
        for key, items in data.items():
            parsed = _parse_date_key(key)
            if parsed is None:
                log.warning("Skipping invalid date key: %r", key)
                continue

            day, month = parsed
            calendar_day_id = await _get_or_create_calendar_day(session, day, month)

            for item in items:
                name = (item.get("name") or "").strip()
                if not name:
                    continue

                description = (item.get("desc") or "").strip() or None

                exists = await session.execute(
                    select(Holiday.id)
                    .where(Holiday.calendar_day_id == calendar_day_id, Holiday.name == name)
                    .limit(1)
                )
                if exists.first():
                    skipped += 1
                    continue

                session.add(Holiday(
                    calendar_day_id=calendar_day_id,
                    name=name,
                    description=description,
                    created_at=_utcnow(),
                ))
                inserted += 1

        await session.commit()

    log.info("Done: inserted=%d, skipped=%d", inserted, skipped)


if __name__ == "__main__":
    import asyncio
    asyncio.run(sync_holidays())
