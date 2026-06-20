from twitchio.ext import commands

from services.command_registry import register_command
from services.games_service import build_game_response, build_games_help_message
from utils.cooldowns import check_cooldown, check_user_cooldown
from utils.delays import custom_delay, human_delay


def setup(bot):
    register_command(
        "игры",
        "Команда: !игры [название игры] — вывод статистики по игре на стриме",
        "all",
        aliases=["игра"],
    )

    @commands.command(name="игры", aliases=("игра",))
    async def games_command(ctx, *, game: str = None):
        cleaned_game = "".join(c for c in (game or "") if c.isprintable()).strip() if game else ""

        if not cleaned_game or not any(c.isalnum() for c in cleaned_game):
            # Без аргументов: глобальный кулдаун 20с + custom_delay
            if not check_cooldown(ctx, "игры", 20):
                return
            await custom_delay(1, 7.3, 8.2)
            await ctx.send(build_games_help_message())
            return

        # С аргументами: per-user кулдаун 1с + human_delay
        if not check_user_cooldown(ctx, "игры_search", 1):
            return
        await human_delay()
        await ctx.send(build_game_response(cleaned_game))

    bot.add_command(games_command)
