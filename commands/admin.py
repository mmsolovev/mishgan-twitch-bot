from twitchio.ext import commands

import services.runtime as runtime
from config.settings import ADMINS
from services.command_registry import register_command


def has_access(ctx):
    return ctx.author.name in ADMINS or ctx.author.is_mod


def setup(bot):

    register_command("отбой", "Команда: !отбой Выключить бота", "mod")
    register_command("старт", "Команда: !старт Включить бота", "mod")

    @commands.command(name="отбой")
    async def stop_bot(ctx):
        if not has_access(ctx):
            return

        if not runtime.BOT_ENABLED:
            await ctx.send("MrDestructoid Бот уже выключен Deadge")
            return

        runtime.BOT_ENABLED = False
        await ctx.send("MrDestructoid Бот ушёл спать Deadge")

    @commands.command(name="старт")
    async def start_bot(ctx):
        if not has_access(ctx):
            return

        if runtime.BOT_ENABLED:
            await ctx.send("MrDestructoid Бот уже работает veryCat")
            return

        runtime.BOT_ENABLED = True
        await ctx.send("MrDestructoid Бот снова в деле")

    bot.add_command(stop_bot)
    bot.add_command(start_bot)
