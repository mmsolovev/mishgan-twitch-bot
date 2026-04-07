import asyncio
import re
import time

from howlongtobeatpy import HowLongToBeat

from services.twitch_service import get_current_game
from utils.cache import load_cache, save_cache


CACHE_KEY = "hltb"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def _normalize_game_key(game: str) -> str:
    return " ".join(game.casefold().split())


def _sanitize_game_name(game: str) -> str:
    sanitized = re.sub(r"[^\w\s:+'\-.]", " ", game, flags=re.UNICODE)
    return " ".join(sanitized.split())


def _get_cached_message(cache: dict, game_key: str) -> tuple[str | None, str | None]:
    entry = cache.get(game_key)
    if entry is None:
        return None, None

    if isinstance(entry, str):
        return None, entry

    if not isinstance(entry, dict):
        return None, None

    message = entry.get("message")
    updated_at = entry.get("updated_at", 0)
    if not message:
        return None, None

    if time.time() - updated_at < CACHE_TTL_SECONDS:
        return message, None

    return None, message


def _save_cached_message(cache: dict, game_key: str, message: str) -> None:
    cache[game_key] = {
        "message": message,
        "updated_at": int(time.time()),
    }


def _search_hltb(game: str):
    client = HowLongToBeat()
    queries = [game]

    sanitized = _sanitize_game_name(game)
    if sanitized and sanitized != game:
        queries.append(sanitized)

    best_match = None
    for query in queries:
        results = client.search(query, similarity_case_sensitive=False)
        if not results:
            continue

        candidate = max(results, key=lambda item: item.similarity)
        if best_match is None or candidate.similarity > best_match.similarity:
            best_match = candidate

    return best_match


async def get_hltb_summary(game: str | None) -> str | None:
    if not game:
        return None

    cache = load_cache(CACHE_KEY)
    game_key = _normalize_game_key(game)
    cached_message, stale_message = _get_cached_message(cache, game_key)

    message = cached_message or stale_message
    if message:
        if "Нет данных" in message or "Не удалось получить данные" in message:
            return None
        return message.removeprefix("MrDestructoid ").strip()

    try:
        best = await asyncio.to_thread(_search_hltb, game)
    except Exception:
        return None

    if not best:
        return None

    summary = (
        f"Прохождение {best.game_name} | "
        f"Сюжет: {best.main_story or '?'} ч | "
        f"Доп: {best.main_extra or '?'} ч | "
        f"100%: {best.completionist or '?'} ч"
    )

    _save_cached_message(cache, game_key, f"MrDestructoid {summary}")
    save_cache(CACHE_KEY, cache)

    return summary


async def get_hltb_info(game: str | None) -> str:
    if not game:
        game = await get_current_game()
        if not game:
            return "MrDestructoid Не удалось определить текущую игру стрима."

    summary = await get_hltb_summary(game)
    if summary:
        return f"MrDestructoid {summary}"

    cache = load_cache(CACHE_KEY)
    game_key = _normalize_game_key(game)
    _, stale_message = _get_cached_message(cache, game_key)
    if stale_message:
        return stale_message

    return f"MrDestructoid Нет данных для «{game}»"
