from twitchio.ext import commands

from services.command_registry import register_command
from services.hltb_service import HLTB_COMMAND_DESCRIPTION, get_hltb_info
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


def setup(bot):

    register_command(
        "hltb",
        HLTB_COMMAND_DESCRIPTION,
        "all"
    )

    @commands.command(name="hltb")
    async def hltb_command(ctx, *, game: str | None = None):
        if not check_cooldown(ctx, "hltb", 5):
            return

        await human_delay()

        result = await get_hltb_info(game)

        await ctx.send(result)

    bot.add_command(hltb_command)