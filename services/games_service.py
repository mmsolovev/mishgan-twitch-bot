from dataclasses import dataclass

from config.settings import GAMES_SHEET_URL
from database.db import SessionLocal
from database.models import Game, GameMeta, GameStats


@dataclass
class GameLookupResult:
    name: str
    streams_count: int
    last_stream: object
    hours_streamed: float
    rank: int
    liked: bool | None
    completed: bool | None


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _format_hours(value: float | None) -> str:
    if value is None:
        return "н/д"

    formatted = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{formatted}ч"


def _format_date(value) -> str:
    if not value:
        return "н/д"
    return value.strftime("%d.%m.%Y")


def _format_status(liked: bool | None, completed: bool | None) -> str | None:
    if liked is True and completed is True:
        return "Игра пройдена и очень понравилась"
    if completed is True:
        return "Игра пройдена"
    if liked is True:
        return "Игра очень понравилась"
    return None


def _doc_suffix() -> str:
    if GAMES_SHEET_URL:
        return f" | Информация о всех стримах и играх канала тут {GAMES_SHEET_URL}"
    return " Таблица игр: ссылка не настроена."


def _load_ranked_games() -> list[GameLookupResult]:
    session = SessionLocal()

    try:
        rows: list[tuple[Game, GameStats, GameMeta | None]] = []

        stats_rows = (
            session.query(Game, GameStats, GameMeta)
            .join(GameStats, GameStats.game_id == Game.id)
            .outerjoin(GameMeta, GameMeta.game_id == Game.id)
            .filter(GameStats.period == "all")
            .all()
        )

        for game, stats, meta in stats_rows:
            rows.append((game, stats, meta))

        rows.sort(key=lambda item: (-(item[1].hours_streamed or 0), _normalize_text(item[0].name)))

        ranked_games: list[GameLookupResult] = []
        for rank, (game, stats, meta) in enumerate(rows, start=1):
            ranked_games.append(
                GameLookupResult(
                    name=game.name,
                    streams_count=int(stats.streams_count or 0),
                    last_stream=stats.last_stream,
                    hours_streamed=stats.hours_streamed or 0,
                    rank=rank,
                    liked=meta.liked if meta else None,
                    completed=meta.completed if meta else None,
                )
            )

        return ranked_games
    finally:
        session.close()


def _find_best_match(query: str, ranked_games: list[GameLookupResult]) -> GameLookupResult | None:
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return None

    scored_matches: list[tuple[int, int, float, GameLookupResult]] = []

    for item in ranked_games:
        normalized_name = _normalize_text(item.name)

        if normalized_name == normalized_query:
            score = 0
        elif normalized_name.startswith(normalized_query):
            score = 1
        elif normalized_query in normalized_name:
            score = 2
        else:
            continue

        scored_matches.append((score, len(normalized_name), -item.hours_streamed, item))

    if not scored_matches:
        return None

    scored_matches.sort(key=lambda item: (item[0], item[1], item[2], _normalize_text(item[3].name)))
    return scored_matches[0][3]


def build_games_help_message() -> str:
    return "Написать в чат: !игры [название игры] — вывод статистики со стримов по игре." + _doc_suffix()


def build_game_response(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return build_games_help_message()

    ranked_games = _load_ranked_games()
    match = _find_best_match(query, ranked_games)

    if not match:
        return f"Игры «{query.strip()}» на стримах не нахожу." + _doc_suffix()

    parts = [
        f"Название: {match.name}",
        f"Сколько раз: {match.streams_count}",
        f"Когда последний раз: {_format_date(match.last_stream)}",
        f"Часов: {_format_hours(match.hours_streamed)} (#{match.rank})",
    ]

    status = _format_status(match.liked, match.completed)
    if status:
        parts.append(status)

    return " | ".join(parts) + _doc_suffix()
