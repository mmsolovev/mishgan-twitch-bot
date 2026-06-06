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
        if not check_cooldown(ctx, "фильм", 10):
            return

        # Любые аргументы — только для ADMINS.
        if args and not _is_admin(ctx):
            return

        # Без аргументов: выводим текущую серию (если задана).
        if not args:
            msg = format_current_episode_for_chat()
            if msg:
                await human_delay()
                await ctx.send(msg)
            return

        op = str(args[0]).casefold()

        if op == "+":
            if len(args) >= 2:
                arg = str(args[1])
                if arg == "0":
                    set_generic_series_state()
                    return

                ref = _parse_ref(arg)
                if not ref:
                    await human_delay()
                    await ctx.send("MrDestructoid Формат: !фильм + 2-4")
                    return
                set_current_episode(ref, set_started_time_now=True)
                return

            set_online_status(True)
            nxt = increment_episode()
            if not nxt:
                await human_delay()
                await ctx.send("MrDestructoid Не получилось определить следующую серию")
            return

        if op == "-":
            # "!фильм -" → выставить "online": False
            # "!фильм - time" → удалить только время
            if len(args) >= 2 and str(args[1]).casefold() == "time":
                clear_time_only()
                return
            clear_all()
            return

        if op == "time":
            # "!фильм time 13:47" → задать время
            # "!фильм time" → поставить текущее время
            value = str(args[1]) if len(args) >= 2 else None
            ok = set_started_time(value)
            if not ok:
                await human_delay()
                await ctx.send("MrDestructoid Формат времени: HH:MM (например 13:47)")
            return

        # неизвестный аргумент: ничего не делаем
        return

    bot.add_command(movie_command)