from twitchio.ext import commands

from config.settings import ADMINS
from services.command_registry import register_command
from services.lost_movie_service import (
    LostEpisodeRef,
    clear_all,
    clear_time_only,
    format_current_episode_for_chat,
    increment_episode,
    set_current_episode,
    set_generic_series_state,
    set_online_status,
    set_started_time,
)
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


ALIASES = [
    "сериал",
    "серия",
    "кино",
    "film",
    "movie",
    "начало",
    "сезон",
    "lost",
    "лост"
]


def _is_admin(ctx) -> bool:
    return bool(ctx.author) and ctx.author.name in ADMINS


def _parse_ref(value: str) -> LostEpisodeRef | None:
    # ожидаем "2-4"
    if not value or "-" not in value:
        return None
    left, right = value.split("-", 1)
    left, right = left.strip(), right.strip()
    if not (left.isdigit() and right.isdigit()):
        return None
    season, episode = int(left), int(right)
    if season < 0 or episode < 0:
        return None
    return LostEpisodeRef(season=season, episode=episode)


def setup(bot):
    register_command(
        "фильм",
        "Команда: !фильм — текущий фильм или серия сериала. ",
        "all",
        aliases=ALIASES,
    )

    @commands.command(name="фильм", aliases=ALIASES)
    async def movie_command(ctx, *args):
        if not check_cooldown(ctx, "фильм", 20):
            return

        await human_delay()
        await ctx.send("MrDestructoid Сериал Lost полностью просмотрен")
        return

    bot.add_command(movie_command)