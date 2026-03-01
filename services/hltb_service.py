from howlongtobeatpy import HowLongToBeat

from services.twitch_service import get_current_game
from utils.cache import load_cache, save_cache


hltb = HowLongToBeat()

CACHE_KEY = "hltb"
HLTB_CACHE = {}


async def get_hltb_info(game: str | None) -> str:
    # Определяем игру
    if not game:
        game = await get_current_game()
        if not game:
            return "MrDestructoid Не удалось определить текущую игру стрима."

    cache = load_cache(CACHE_KEY)

    if game.lower() in cache:
        return cache[game.lower()]

    # Проверяем кеш
    # if game_key in HLTB_CACHE:
    #     return f"MrDestructoid {HLTB_CACHE[game_key]}"


    # Поиск на HLTB
    results = hltb.search(game)
    if not results:
        return f"MrDestructoid Нет данных для «{game}»"

    best = max(results, key=lambda x: x.similarity)

    message = (f"MrDestructoid Прохождение {best.game_name} | "
            f"Сюжет: {best.main_story or '?'} ч | "
            f"Доп: {best.main_extra or '?'} ч | "
            f"100%: {best.completionist or '?'} ч")

    cache[game.lower()] = message
    save_cache(CACHE_KEY, cache)

    return message






