import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from config.settings import GAMES_SHEET_URL
from database.db import SessionLocal
from database.models import Game, GameMeta, GameStats


MAX_VISIBLE_SUGGESTIONS = 3
CONFIDENT_MATCH_THRESHOLD = 0.88
SIMILAR_MATCH_THRESHOLD = 0.64
CONFIDENT_GAP_THRESHOLD = 0.08
ROMAN_TO_ARABIC = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}


@dataclass
class GameLookupResult:
    name: str
    streams_count: int
    last_stream: object
    hours_streamed: float
    rank: int
    liked: bool | None
    completed: bool | None


@dataclass
class CandidateMatch:
    game: GameLookupResult
    score: float
    ratio: float
    overlap: float


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _canonicalize_text(value: str) -> str:
    normalized = _normalize_text(value)
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    tokens = [ROMAN_TO_ARABIC.get(token, token) for token in normalized.split()]
    return " ".join(tokens)


def _extract_number_tokens(value: str) -> set[str]:
    return {token for token in _canonicalize_text(value).split() if token.isdigit()}


def _format_hours(value: float | None) -> str:
    if value is None:
        return "н/д"

    formatted = f"{value:.1f}".rstrip("0").rstrip(".")
    return formatted


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
        return f" | Все стримы и игры канала {GAMES_SHEET_URL}"
    return " Таблица игр: ссылка не настроена"


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


def _find_exact_match(query: str, ranked_games: list[GameLookupResult]) -> GameLookupResult | None:
    canonical_query = _canonicalize_text(query)
    if not canonical_query:
        return None

    for item in ranked_games:
        if _canonicalize_text(item.name) == canonical_query:
            return item

    return None


def _build_candidate_match(query: str, item: GameLookupResult) -> CandidateMatch | None:
    canonical_query = _canonicalize_text(query)
    canonical_name = _canonicalize_text(item.name)
    if not canonical_query or not canonical_name:
        return None

    query_numbers = _extract_number_tokens(query)
    name_numbers = _extract_number_tokens(item.name)
    if query_numbers and query_numbers != name_numbers:
        return None

    ratio = SequenceMatcher(None, canonical_query, canonical_name).ratio()
    query_tokens = set(canonical_query.split())
    name_tokens = set(canonical_name.split())
    overlap = len(query_tokens & name_tokens) / len(query_tokens) if query_tokens else 0.0

    bonus = 0.0
    if canonical_name.startswith(canonical_query):
        bonus += 0.10
    elif canonical_query in canonical_name:
        bonus += 0.06

    if query_tokens and query_tokens <= name_tokens:
        bonus += 0.10

    score = ratio * 0.7 + overlap * 0.3 + bonus
    if score < SIMILAR_MATCH_THRESHOLD:
        return None

    return CandidateMatch(game=item, score=score, ratio=ratio, overlap=overlap)


def _find_similar_matches(query: str, ranked_games: list[GameLookupResult]) -> list[CandidateMatch]:
    matches: list[CandidateMatch] = []

    for item in ranked_games:
        candidate = _build_candidate_match(query, item)
        if candidate:
            matches.append(candidate)

    matches.sort(
        key=lambda item: (
            -item.score,
            -item.overlap,
            -item.ratio,
            len(_canonicalize_text(item.game.name)),
            -item.game.hours_streamed,
            _normalize_text(item.game.name),
        )
    )
    return matches


def _format_game_stats(match: GameLookupResult) -> str:
    parts = [
        f"Игра: {match.name}",
        f"Количество стримов {match.streams_count}",
        f"В последний раз {_format_date(match.last_stream)}",
        f"Часов {_format_hours(match.hours_streamed)} (#{match.rank})",
    ]

    status = _format_status(match.liked, match.completed)
    if status:
        parts.append(status)

    return " | ".join(parts)


def _format_suggestions(matches: list[CandidateMatch]) -> str:
    visible = [candidate.game.name for candidate in matches[:MAX_VISIBLE_SUGGESTIONS]]
    hidden_count = max(0, len(matches) - len(visible))

    message = ", ".join(f"«{name}»" for name in visible)
    if hidden_count:
        message += f" и еще {hidden_count}"
    return message


def _is_confident_match(query: str, best_match: CandidateMatch, other_matches: list[CandidateMatch]) -> bool:
    if best_match.score < CONFIDENT_MATCH_THRESHOLD:
        return False

    query_numbers = _extract_number_tokens(query)
    if query_numbers and query_numbers == _extract_number_tokens(best_match.game.name):
        return True

    if not other_matches:
        return True

    next_best = other_matches[0]
    if best_match.score - next_best.score >= CONFIDENT_GAP_THRESHOLD:
        return True

    return best_match.ratio >= 0.97


def find_game_lookup(query: str) -> GameLookupResult | None:
    query = (query or "").strip()
    if not query:
        return None

    ranked_games = _load_ranked_games()

    exact_match = _find_exact_match(query, ranked_games)
    if exact_match:
        return exact_match

    similar_matches = _find_similar_matches(query, ranked_games)
    if not similar_matches:
        return None

    best_match = similar_matches[0]
    other_matches = similar_matches[1:]

    if _is_confident_match(query, best_match, other_matches):
        return best_match.game

    return None


def build_games_help_message() -> str:
    return "Написать в чат: !игры [название игры] — вывод статистики со стримов по игре" + _doc_suffix()


def build_game_response(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return build_games_help_message()

    ranked_games = _load_ranked_games()

    exact_match = _find_exact_match(query, ranked_games)
    if exact_match:
        return _format_game_stats(exact_match) + _doc_suffix()

    similar_matches = _find_similar_matches(query, ranked_games)
    if not similar_matches:
        return f"Игра «{query}» не найдена, и похожих вариантов тоже нет. Скорее всего этой игры на стримах не было." + _doc_suffix()

    best_match = similar_matches[0]
    other_matches = similar_matches[1:]

    if _is_confident_match(query, best_match, other_matches):
        prefix = f"Точного совпадения не нашел, но скорее всего это «{best_match.game.name}»."
        if other_matches:
            prefix += (
                f" Есть и другие похожие результаты: {len(other_matches)}"
                f", например «{other_matches[0].game.name}»."
            )
        return prefix + " " + _format_game_stats(best_match.game) + _doc_suffix()

    suggestions = _format_suggestions(similar_matches)
    return (
        f"Точного совпадения для «{query}» не найдено. Возможно, имелось в виду: {suggestions}. "
        "Если ничего не подходит, скорее всего этой игры на стримах не было."
        + _doc_suffix()
    )
