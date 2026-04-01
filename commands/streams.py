from twitchio.ext import commands

from services.command_registry import register_command
from services.streams_service import build_stream_response, build_streams_help_message
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


def setup(bot):

    register_command(
        "стримы",
        "Команда: !стримы [дата] — информация по стриму за указанную дату и ссылка на общую таблицу",
        "all"
    )

    @commands.command(name="стримы")
    async def streams_command(ctx, *, date_query: str = None):
        if not check_cooldown(ctx, "стримы", 10):
            return

        await human_delay()

        date_query = (date_query or "").strip()

        if not date_query:
            await ctx.send(build_streams_help_message())
            return

        await ctx.send(build_stream_response(date_query))

    bot.add_command(streams_command)
