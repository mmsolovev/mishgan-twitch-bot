from twitchio.ext import commands

from services.command_registry import register_command
from services.games_service import build_game_response, build_games_help_message
from utils.cooldowns import check_cooldown
from utils.delays import custom_delay


def setup(bot):
    register_command(
        "игры",
        "Команда: !игры [название игры] — статистика по игре на стриме и ссылка на общую таблицу",
        "all",
        aliases=["игра"],
    )

    @commands.command(name="игры", aliases=("игра",))
    async def games_command(ctx, *, game: str = None):
        if not check_cooldown(ctx, "игры", 10):
            return

        await custom_delay(1, 7.3, 8.2)

        # Упрощенная проверка, основная логика в games_service
        cleaned_game = "".join(c for c in (game or "") if c.isprintable()).strip()

        if not cleaned_game:
            await ctx.send(build_games_help_message())
            return

        await ctx.send(build_game_response(cleaned_game))

    bot.add_command(games_command)
