import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config.settings import GAMES_SHEET_URL
from database.db import AsyncSessionLocal
from database.models import Stream, StreamGame
from utils.time_format import format_hours_minutes


@dataclass
class StreamLookupResult:
    date: datetime
    duration_minutes: int | None
    games: list[str]


def _doc_suffix() -> str:
    if GAMES_SHEET_URL:
        return f" | Все стримы канала {GAMES_SHEET_URL}"
    return " Список стримов: ссылка не настроена"


def _format_datetime(value) -> str:
    if not value:
        return "н/д"
    return value.strftime("%d.%m.%Y %H:%M")


def _format_duration(value: int | None) -> str:
    if value is None:
        return "н/д"
    return format_hours_minutes(value / 60) or "н/д"


def _format_games(games: list[str]) -> str:
    return " -> ".join(games) if games else "н/д"


_TEXT_DATE_PATTERN = re.compile(r"^(\d{1,2})[\s.]+([а-яё]+)(?:\s+(\d{2,4}))?$", re.IGNORECASE)

_MONTHS_BY_NAME = {
    "январь": 1, "января": 1,
    "февраль": 2, "февраля": 2,
    "март": 3, "марта": 3,
    "апрель": 4, "апреля": 4,
    "май": 5, "мая": 5,
    "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7,
    "август": 8, "августа": 8,
    "сентябрь": 9, "сентября": 9,
    "октябрь": 10, "октября": 10,
    "ноябрь": 11, "ноября": 11,
    "декабрь": 12, "декабря": 12,
}


def _parse_text_month_date(value: str) -> datetime | None:
    match = _TEXT_DATE_PATTERN.match(value.strip().lower())
    if not match:
        return None

    day_text, month_name, year_text = match.groups()
    month = _MONTHS_BY_NAME.get(month_name)
    if month is None:
        return None

    try:
        day = int(day_text)
    except ValueError:
        return None

    if year_text:
        year = int(year_text)
        if year < 100:
            year += 2000
    else:
        year = datetime.now().year

    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _parse_date(value: str) -> datetime | None:
    raw_value = (value or "").strip()
    if not raw_value:
        return None

    normalized_value = re.sub(r"[./-]", ".", raw_value)

    day_first_patterns = (
        "%d.%m.%Y",
        "%d.%m.%y",
        "%d.%m",
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
            parsed = datetime.strptime(normalized_value, pattern)
            if pattern == "%d.%m":
                parsed = parsed.replace(year=datetime.now().year)
            return parsed
        except ValueError:
            continue

    text_parsed = _parse_text_month_date(normalized_value)
    if text_parsed is not None:
        return text_parsed

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
        "Написать в чат: !стримы [дата ДД.ММ.ГГ] — информация по стриму за указанную дату "
        + _doc_suffix()
    )


async def build_stream_response(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return build_streams_help_message()

    target_date = _parse_date(query)
    if not target_date:
        return (
            "Не могу распознать дату. Подойдут форматы вроде 05.04.2026, 05.04.26, 05.04, "
            "05/04/2026, 05-04-26, 2026-04-05 или 5 апреля"
            + _doc_suffix()
        )

    matches = await _load_streams_for_date(target_date)
    if not matches:
        return f"Стримов за дату {target_date.strftime('%d.%m.%Y')} не нахожу" + _doc_suffix()

    parts = [
        f"Дата и время: {_format_datetime(match.date)} | Длительность: {_format_duration(match.duration_minutes)} | Игры: {_format_games(match.games)}"
        for match in matches
    ]

    return " || ".join(parts) + _doc_suffix()
