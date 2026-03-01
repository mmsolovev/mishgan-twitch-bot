import asyncio
import json
import random
from datetime import datetime

from twitchio.ext import commands
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


# Загружаем праздники при старте бота
with open("storage/holidays.json", "r", encoding="utf-8") as f:
    HOLIDAYS = json.load(f)


def setup(bot):

    @commands.command(name="праздник")
    async def holiday_command(ctx):
        if not check_cooldown(ctx, "праздник", 60):
            return

        await human_delay()

        today = datetime.now().strftime("%d-%m")
        holidays_today = HOLIDAYS.get(today, [])

        if not holidays_today:
            await ctx.send("MrDestructoid Сегодня официальных праздников нет")
            return

        # выбираем случайный праздник
        holiday = random.choice(holidays_today)
        await ctx.send(f"MrDestructoid Сегодня: {holiday['name']} | {holiday['desc']}")

    bot.add_command(holiday_command)
