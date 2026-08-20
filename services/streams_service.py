import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config.settings import GAMES_SHEET_URL
from database.db import AsyncSessionLocal
from database.models import Stream, StreamGame


@dataclass
class StreamLookupResult:
    date: object
    duration_minutes: int | None
    games: list[str]


def _doc_suffix() -> str:
    if GAMES_SHEET_URL:
        return f" | Информация о всех стримах канала {GAMES_SHEET_URL}"
    return " Таблица стримов: ссылка не настроена"


def _format_datetime(value) -> str:
    if not value:
        return "н/д"
    return value.strftime("%d.%m.%Y %H:%M")


def _format_hours(value: int | None) -> str:
    if value is None:
        return "н/д"
    hours = value / 60
    formatted = f"{hours:.1f}".rstrip("0").rstrip(".")
    return f"{formatted}ч"


def _format_games(games: list[str]) -> str:
    return " -> ".join(games) if games else "н/д"


def _parse_date(value: str) -> datetime | None:
    raw_value = (value or "").strip()
    if not raw_value:
        return None

    normalized_value = re.sub(r"[./-]", ".", raw_value)

    day_first_patterns = (
        "%d.%m.%Y",
        "%d.%m.%y",
    )
    year_first_patterns = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%y-%m-%d",
        "%y/%m/%d",
        "%y.%m.%d",
    )

    for pattern in day_first_patterns:
        try:
            return datetime.strptime(normalized_value, pattern)
        except ValueError:
            continue

    for pattern in year_first_patterns:
        try:
            return datetime.strptime(raw_value, pattern)
        except ValueError:
            continue

    return None


async def _load_streams_for_date(target_date: datetime) -> list[StreamLookupResult]:
    async with AsyncSessionLocal() as session:
        day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        q = (
            select(Stream)
            .options(selectinload(Stream.stream_games).selectinload(StreamGame.game))
            .where(Stream.started_at >= day_start, Stream.started_at < day_end)
            .order_by(Stream.started_at.asc())
        )
        result = await session.execute(q)
        streams = result.scalars().all()

        return [
            StreamLookupResult(
                date=stream.started_at,
                duration_minutes=stream.duration_minutes,
                games=[sg.game.name for sg in stream.stream_games if sg.game],
            )
            for stream in streams
        ]


def build_streams_help_message() -> str:
    return (
        "Написать в чат: !стримы [дата] — вывод информации по стриму за дату. "
        "Поддерживаются форматы вроде 05.04.2026, 05.04.26, 05/04/2026, 05-04-26, 2026-04-05"
        + _doc_suffix()
    )


async def build_stream_response(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return build_streams_help_message()

    target_date = _parse_date(query)
    if not target_date:
        return (
            "Не могу распознать дату. Подойдут форматы вроде 05.04.2026, 05.04.26, 05/04/2026, 05-04-26 или 2026-04-05"
            + _doc_suffix()
        )

    matches = await _load_streams_for_date(target_date)
    if not matches:
        return f"Стримов за дату {target_date.strftime('%d.%m.%Y')} не нахожу" + _doc_suffix()

    parts = [
        f"Дата и время: {_format_datetime(match.date)} | Длительность: {_format_hours(match.duration_minutes)} | Игры: {_format_games(match.games)}"
        for match in matches
    ]

    return " || ".join(parts) + _doc_suffix()
