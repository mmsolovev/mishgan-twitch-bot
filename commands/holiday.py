from twitchio.ext import commands

from utils.cooldowns import check_cooldown
from utils.delays import human_delay
from services.command_registry import register_command

from services.holiday_service import (
    get_today_key,
    get_random_today,
    get_all_today_names,
    get_by_index,
    get_holidays_by_date,
    search_holiday
)


def setup(bot):

    register_command(
        "праздник",
        "Команды: !праздник [все/номер/поиск/дата] — случайный или выбранный праздник",
        "all"
    )

    @commands.command(name="праздник")
    async def holiday_command(ctx):
        if not check_cooldown(ctx, "праздник", 5):
            return

        args = ctx.message.content.split()[1:]

        await human_delay()

        # 🔹 без аргументов → случайный
        if not args:
            holiday = get_random_today()

            if not holiday:
                await ctx.send("MrDestructoid Сегодня праздников нет")
                return

            await ctx.send(
                f"MrDestructoid Сегодня: {holiday['name']} | {holiday['desc']}"
            )
            return

        arg1 = args[0].lower()
        arg1 = arg1.replace(".", "-").replace("/", "-")

        # 🔹 все
        if arg1 == "все":
            names = get_all_today_names()

            if not names:
                await ctx.send("MrDestructoid Сегодня праздников нет")
                return

            await ctx.send("Сегодня: " + " | ".join(names))
            return

        # 🔹 дата
        if "-" in arg1:
            holidays = get_holidays_by_date(arg1)

            if not holidays:
                await ctx.send("MrDestructoid Нет праздников на эту дату")
                return

            # дата + номер
            if len(args) > 1 and args[1].isdigit():
                idx = int(args[1]) - 1
                holiday = get_by_index(arg1, idx)

                if not holiday:
                    await ctx.send("MrDestructoid Нет такого номера damn")
                    return

                await ctx.send(
                    f"{arg1}: {holiday['name']} | {holiday['desc']}"
                )
                return

            # просто дата
            names = [h["name"] for h in holidays]
            await ctx.send(f"{arg1}: " + " | ".join(names))
            return

        # 🔹 номер (сегодня)
        if arg1.isdigit():
            idx = int(arg1) - 1
            today = get_today_key()

            holiday = get_by_index(today, idx)

            if not holiday:
                await ctx.send("MrDestructoid Нет такого номера damn")
                return

            await ctx.send(
                f"Сегодня: {holiday['name']} | {holiday['desc']}"
            )
            return

        # 🔹 поиск
        results = search_holiday(arg1)

        if not results:
            await ctx.send("MrDestructoid Ничего не найдено damn")
            return

        formatted = [
            f"{r['date']} {r['name']}"
            for r in results
        ]

        await ctx.send("Найдено: " + " | ".join(formatted))

    bot.add_command(holiday_command)
