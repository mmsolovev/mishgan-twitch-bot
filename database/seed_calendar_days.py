"""
Seed the calendar_days table with 366 rows (Jan 1 – Dec 31, including Feb 29).

Usage:
    python database/seed_calendar_days.py

Idempotent: uses ON CONFLICT DO NOTHING, safe to re-run.
"""

import asyncio
import calendar
from datetime import datetime

from sqlalchemy import text

from database.db import AsyncSessionLocal

REFERENCE_YEAR = 2024  # leap year – ensures Feb 29 is included


async def seed_calendar_days():
    async with AsyncSessionLocal() as session:
        for month in range(1, 13):
            days_in_month = calendar.monthrange(REFERENCE_YEAR, month)[1]
            for day in range(1, days_in_month + 1):
                day_of_year = datetime(REFERENCE_YEAR, month, day).timetuple().tm_yday
                await session.execute(
                    text(
                        """
                        INSERT INTO calendar_days (day, month, day_of_year)
                        VALUES (:day, :month, :day_of_year)
                        ON CONFLICT (month, day) DO NOTHING
                        """
                    ),
                    {"day": day, "month": month, "day_of_year": day_of_year},
                )

        await session.commit()

        result = await session.execute(text("SELECT COUNT(*) FROM calendar_days"))
        count = result.scalar()
        print(f"calendar_days seeded: {count} rows")


if __name__ == "__main__":
    asyncio.run(seed_calendar_days())
