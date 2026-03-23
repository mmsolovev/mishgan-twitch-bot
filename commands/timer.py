from twitchio.ext import commands
import asyncio

from config.settings import ALLOWED_USERS
from services.command_registry import register_command
from utils.cooldowns import check_cooldown
from utils.delays import human_delay

from services.timer_service import (
    parse_time,
    start_timer,
    start_return_timer,
    stop_timer,
    list_timers,
    MAX_SECONDS
)





def has_access(ctx) -> bool:
    return ctx.author.is_mod or ctx.author.name in ALLOWED_USERS


def setup(bot):

    register_command(
        "таймер",
        "Команда: !таймер [время] [текст] | стоп | список | возврат (для Steam)",
        "mod"
    )

    @commands.command(name="таймер")
    async def timer_command(ctx):
        if not has_access(ctx):
            return

        if not check_cooldown(ctx, "таймер", 5):
            return

        args = ctx.message.content.split()[1:]

        if not args:
            await ctx.send(
                "Использование: !таймер [время] [текст] | !таймер стоп [ник] | !таймер список | !таймер возврат | Пример: !таймер 5м Пора"
            )
            return

        cmd = args[0].lower()

        # ❌ стоп
        if cmd == "стоп":
            await stop_timer(ctx)
            return

        # 📋 список
        if cmd == "список":
            await list_timers(ctx)
            return

        # 🔁 возврат
        if cmd == "возврат":
            await human_delay()
            await ctx.send("MrDestructoid ⏳ Таймер возврата Steam запущен (1ч 55м)")

            asyncio.create_task(start_return_timer(ctx))
            return

        # ⏱ обычный таймер
        seconds = parse_time(cmd)

        if not seconds:
            await ctx.send("MrDestructoid Не понял время HUH Пример: 10м, 1ч")
            return

        if seconds > MAX_SECONDS:
            await ctx.send("MrDestructoid 8 часов максимум, я не умею считать дальше pepeW")
            return

        text = " ".join(args[1:]) or "MrDestructoid ALERT ТАЙМЕР ИСТЁК ALERT"

        await human_delay()
        await ctx.send(f"MrDestructoid ⏳ Таймер на {cmd} запущен")

        await start_timer(ctx, seconds, text)

    bot.add_command(timer_command)
