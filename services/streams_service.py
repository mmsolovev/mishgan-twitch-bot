from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import joinedload

from config.settings import GAMES_SHEET_URL
from database.db import SessionLocal
from database.models import Stream, StreamGame


@dataclass
class StreamLookupResult:
    date: object
    duration: float | None
    games: list[str]


def _doc_suffix() -> str:
    if GAMES_SHEET_URL:
        return f" | Информация о всех стримах и играх канала тут {GAMES_SHEET_URL}"
    return " Таблица стримов: ссылка не настроена."


def _format_datetime(value) -> str:
    if not value:
        return "н/д"
    return value.strftime("%d.%m.%Y %H:%M")


def _format_hours(value: float | None) -> str:
    if value is None:
        return "н/д"

    formatted = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{formatted}ч"


def _format_games(games: list[str]) -> str:
    return " -> ".join(games) if games else "н/д"


def _parse_date(value: str) -> datetime | None:
    raw_value = (value or "").strip()
    if not raw_value:
        return None

    for pattern in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw_value, pattern)
        except ValueError:
            continue

    return None


def _load_streams_for_date(target_date: datetime) -> list[StreamLookupResult]:
    session = SessionLocal()

    try:
        day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        streams = (
            session.query(Stream)
            .options(joinedload(Stream.stream_games).joinedload(StreamGame.game))
            .filter(Stream.date >= day_start, Stream.date < day_end)
            .order_by(Stream.date.asc())
            .all()
        )

        return [
            StreamLookupResult(
                date=stream.date,
                duration=stream.duration,
                games=[stream_game.game.name for stream_game in stream.stream_games],
            )
            for stream in streams
        ]
    finally:
        session.close()


def build_streams_help_message() -> str:
    return "Написать в чат: !стримы [дата] — вывод информации по стриму за дату. Формат даты: ДД.ММ.ГГГГ." + _doc_suffix()


def build_stream_response(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return build_streams_help_message()

    target_date = _parse_date(query)
    if not target_date:
        return "Не понял дату. Используй формат ДД.ММ.ГГГГ." + _doc_suffix()

    matches = _load_streams_for_date(target_date)
    if not matches:
        return f"Стримов за дату {target_date.strftime('%d.%m.%Y')} не нахожу." + _doc_suffix()

    parts = [
        f"Дата и время: {_format_datetime(match.date)} | Длительность: {_format_hours(match.duration)} | Игры: {_format_games(match.games)}"
        for match in matches
    ]

    return " || ".join(parts) + _doc_suffix()
