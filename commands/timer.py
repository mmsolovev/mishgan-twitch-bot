from encodings.aliases import aliases

from twitchio.ext import commands

from config.settings import ALLOWED_USERS
from services.command_registry import register_command
from services.timer_service import (
    MAX_SECONDS,
    build_status_message,
    parse_time,
    start_return_timer,
    start_timer,
    stop_return_timer,
    stop_timer,
)
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


def has_access(ctx) -> bool:
    return ctx.author.is_mod or ctx.author.name in ALLOWED_USERS


def setup(bot):

    register_command(
        "таймер",
        "Команда: !таймер — время до окончания | !таймер [время] [текст] | !таймер стоп | !таймер возврат (Steam)",
        "all"
    )

    @commands.command(name="таймер")
    async def timer_command(ctx, *, args: str | None = None):
        await human_delay()

        # 📊 статус доступен всем, не чаще раза в 30 секунд
        if not args:
            if not check_cooldown(ctx, "таймер_статус", 30):
                return
            await ctx.send(build_status_message())
            return

        if not has_access(ctx):
            return

        if not check_cooldown(ctx, "таймер", 5):
            return

        parts = args.split()
        cmd = parts[0].lower()

        # ❌ стоп (сначала обычный таймер, потом возврат)
        if cmd == "стоп":
            await stop_timer(ctx)
            return

        # 🔁 возврат
        if cmd == "возврат":
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub == "стоп":
                await stop_return_timer(ctx)
            else:
                await start_return_timer(ctx)
            return

        # ⏱ обычный глобальный таймер
        seconds = parse_time(cmd)

        if not seconds:
            await ctx.send("Не понял время HUH Пример: 10м, 1ч")
            return

        if seconds > MAX_SECONDS:
            await ctx.send("8 часов максимум, я не умею считать дальше pepeW")
            return

        text = " ".join(parts[1:]) or "ALERT ТАЙМЕР ИСТЁК ALERT"

        await start_timer(ctx, seconds, text)

    bot.add_command(timer_command)