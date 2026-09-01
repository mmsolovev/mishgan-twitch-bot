from twitchio.ext import commands

from services.command_registry import register_command
from services.streams_service import build_stream_response, build_streams_help_message
from utils.args import clean_text
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


def setup(bot):

    register_command(
        "стримы",
        "Команда: !стримы [дата ДД.ММ.ГГ] — информация по стриму за указанную дату",
        "all",
        aliases=["стрим"],
    )

    @commands.command(name="стримы", aliases=("стрим",))
    async def streams_command(ctx, *, date_query: str | None = None):
        if not check_cooldown(ctx, "стримы", 5):
            return

        await human_delay()

        date_query = clean_text(date_query)

        if not date_query:
            await ctx.send(build_streams_help_message())
            return

        await ctx.send(await build_stream_response(date_query))

    bot.add_command(streams_command)
