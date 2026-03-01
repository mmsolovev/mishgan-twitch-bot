from twitchio.ext import commands
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


def setup(bot):

    @commands.command(name="пример")
    async def example(ctx):
        if not await check_cooldown(ctx, "пример"):
            return

        await human_delay()

        await ctx.send("Команда работает!")
