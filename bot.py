import os
import asyncio
import random
import time
import aiohttp
import json

from twitchio.ext import commands
from howlongtobeatpy import HowLongToBeat
from dotenv import load_dotenv
from datetime import datetime


load_dotenv()  # ищет файл .env и загружает переменные

TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")
TWITCH_NICK = os.getenv("TWITCH_NICK")
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
CALENDARIFIC_KEY = os.getenv("CALENDARIFIC_KEY")

COUNTRY = "US"

COOLDOWN=60
HUMAN_DELAY_MIN = 1.3
HUMAN_DELAY_MAX = 2.8

# Загружаем праздники при старте бота
with open("holidays.json", "r", encoding="utf-8") as f:
    HOLIDAYS = json.load(f)

class Bot(commands.Bot):

    def __init__(self):
        super().__init__(
            token=TWITCH_TOKEN,
            prefix="!",
            nick=TWITCH_NICK,
            initial_channels=[TWITCH_CHANNEL],
        )

        self.hltb = HowLongToBeat()
        self.last_command_time = 0

    async def event_ready(self):
        print(f"✅ Logged in as {self.nick}")

    async def event_message(self, message):
        if not message.author:
            return

        # логируем только команды
        if not message.content.startswith("!"):
            return

        print(f"[COMMAND] {message.author.name}: {message.content}")
        await self.handle_commands(message)

    @commands.command(name="hltb")
    async def hltb_command(self, ctx, *, game: str = None):
        if not await self.check_cooldown(ctx):
            return

        if not game:
            game = await self.get_current_game()
            if not game:
                await ctx.send("MrDestructoid Не удалось определить текущую игру стрима.")
                return

        await asyncio.sleep(random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX))

        results = self.hltb.search(game)
        if not results:
            await ctx.send(f"MrDestructoid Нет данных для «{game}»")
            return

        best = max(results, key=lambda x: x.similarity)

        await ctx.send(
            f"MrDestructoid Прохождение {best.game_name} | "
            f"Сюжет: {best.main_story or '?'} ч | "
            f"Доп: {best.main_extra or '?'} ч | "
            f"100%: {best.completionist or '?'} ч"
        )

    @commands.command(name="праздник")
    async def holiday_command(self, ctx):
        if not await self.check_cooldown(ctx):
            return

        await asyncio.sleep(random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX))

        today = datetime.now().strftime("%d-%m")
        holidays_today = HOLIDAYS.get(today, [])

        if not holidays_today:
            await ctx.send("MrDestructoid Сегодня официальных праздников нет")
            return

        # выбираем случайный праздник
        holiday = random.choice(holidays_today)
        await ctx.send(f"MrDestructoid Сегодня: {holiday['name']} | {holiday['desc']}")

    # Вспомогательные функции
    async def check_cooldown(self, ctx):
        now = time.time()
        if now - self.last_command_time < COOLDOWN:
            return False
        self.last_command_time = now
        return True

    async def get_current_game(self):
        url = f"https://api.twitch.tv/helix/streams?user_login={TWITCH_CHANNEL}"
        headers = {
            "Client-ID": CLIENT_ID,
            "Authorization": f"Bearer {await self.get_app_access_token()}"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                if data.get("data"):
                    return data["data"][0].get("game_name")
        return None

    async def get_app_access_token(self):
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params) as resp:
                data = await resp.json()
                return data["access_token"]


# ЗАПУСК
if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    bot = Bot()
    bot.run()
