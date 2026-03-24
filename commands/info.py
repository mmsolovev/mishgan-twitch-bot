from twitchio.ext import commands

from services.command_registry import register_command
from services.info_service import resolve_query, format_for_chat
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


def setup(bot):

    register_command(
        "инфо",
        "Команда: !инфо [стример/комп/девайсы/[поиск по девайсу]]",
        "all"
    )

    @commands.command(name="инфо")
    async def info_command(ctx, *args):

        if not check_cooldown(ctx, "инфо", 10):
            return

        await human_delay()

        query = args[-1] if args else "стример"

        result = resolve_query(query)

        if not result:
            await ctx.send("MrDestructoid Нет такой информации")
            return

        key, value = result

        msg = format_for_chat(key, value)

        await ctx.send(msg)

    bot.add_command(info_command)
