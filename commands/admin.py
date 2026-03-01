from twitchio.ext import commands
from config.settings import TWITCH_NICK


def setup(bot):

    @commands.command(name="reload")
    async def reload_bot(ctx):
        if ctx.author.name != TWITCH_NICK:
            return

        await ctx.send("MrDestructoid Перезагрузка пока не реализована")
