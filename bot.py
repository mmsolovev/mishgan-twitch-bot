import os
import asyncio
import random
import time
import aiohttp
import json
import g4f

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

ALLOWED_USERS = ["mishgan_sol", "tabula", "orfeylefontu"]
BANNED_WORDS = ["n-word", "slur1", "badword1"]
BLOCKED_START = ["!", "/", "#", "@", "$", "%", "&"]
HAR_DIR = os.path.join(os.getcwd(), "har_and_cookies")
CACHE_FILE = "hltb_cache.json"

COOLDOWN=60
HUMAN_DELAY_MIN = 1.3
HUMAN_DELAY_MAX = 2.8

HLTB_CACHE = {}

# Загружаем праздники при старте бота
with open("holidays.json", "r", encoding="utf-8") as f:
    HOLIDAYS = json.load(f)

def censor_text(text: str) -> str:
    """Заменяет запрещённые слова на ****"""
    censored = text
    text_lower = censored.lower()
    for word in BANNED_WORDS:
        idx = text_lower.find(word)
        while idx != -1:
            censored = censored[:idx] + "*" * len(word) + censored[idx+len(word):]
            text_lower = censored.lower()
            idx = text_lower.find(word)
    return censored

def sanitize_start(text: str) -> str:
    """Убирает запрещённые символы в начале ответа"""
    while text and text[0] in BLOCKED_START:
        text = text[1:]
    return text.strip()

async def process_gpt_answer(raw_answer: str) -> str:
    """Обрабатывает ответ GPT: цензура, длина, запрещённые стартовые символы"""
    if not raw_answer:
        return "MrDestructoid GPT временно недоступен"

    answer = raw_answer.strip().replace("\n", " ")
    answer = censor_text(answer)
    answer = sanitize_start(answer)
    if len(answer) > 150:
        answer = answer[:147] + "..."
    if not answer:  # если после фильтров пусто
        answer = "MrDestructoid GPT ответил пусто после фильтров"
    return answer

async def ask_gpt(prompt: str) -> str:
    loop = asyncio.get_running_loop()

    def sync_request():
        return g4f.ChatCompletion.create(
            model="",
            messages=[
                {
                    "role": "system",
                    "content": "Отвечай кратко, не более 150 символов. Не используй непристойные слова и запрещённые "
                               "на Twitch выражения. Откажись отвечать, если вопрос касается политики или религии."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
        )

    return await loop.run_in_executor(None, sync_request)


class Bot(commands.Bot):

    def __init__(self):
        super().__init__(
            token=TWITCH_TOKEN,
            prefix="!",
            nick=TWITCH_NICK,
            initial_channels=[TWITCH_CHANNEL],
        )

        self.hltb = HowLongToBeat()
        self.last_command_time = 0  # общий кулдаун
        self.last_gpt_time = 0  # кулдаун для !вопрос

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

        # Определяем игру
        if not game:
            game = await self.get_current_game()
            if not game:
                await ctx.send("MrDestructoid Не удалось определить текущую игру стрима.")
                return

        game_key = game.lower()  # ключ для кеша

        # Проверяем кеш
        if game_key in HLTB_CACHE:
            await ctx.send(f"MrDestructoid {HLTB_CACHE[game_key]}")
            return

        # Human-like задержка
        await asyncio.sleep(random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX))

        # Поиск на HLTB
        results = self.hltb.search(game)
        if not results:
            await ctx.send(f"MrDestructoid Нет данных для «{game}»")
            return

        best = max(results, key=lambda x: x.similarity)

        # Формируем ответ
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

    @commands.command(name="вопрос")
    async def question_command(self, ctx, *, question: str = None):
        # проверяем, что пользователь разрешён
        user = ctx.author.name.lower()
        if user not in ALLOWED_USERS:
            return

        if not question:
            await ctx.send("MrDestructoid Используй: !вопрос <текст>")
            return

        if len(question) > 200:
            await ctx.send("MrDestructoid Вопрос слишком длинный")
            return

        if "http://" in question or "https://" in question:
            await ctx.send("MrDestructoid Ссылки запрещены")
            return

        if not await self.check_gpt_cooldown(ctx):
            return

        await asyncio.sleep(random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX))

        try:
            answer = await asyncio.wait_for(ask_gpt(question), timeout=15)

            if not answer:
                await ctx.send("MrDestructoid Я не знаю ответа")
                return

            answer = answer.strip().replace("\n", " ")
            if len(answer) > 150:
                answer = answer[:147] + "..."

            await ctx.send(f"MrDestructoid {answer}")

        except Exception as e:
            print("g4f error:", e)
            await ctx.send("MrDestructoid GPT временно недоступен")


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

    async def check_gpt_cooldown(self, ctx):
        now = time.time()
        GPT_COOLDOWN = 20  # секунды, можно вынести в .env

        if now - self.last_gpt_time < GPT_COOLDOWN:
            return False

        self.last_gpt_time = now
        return True


# ЗАПУСК
if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    bot = Bot()
    bot.run()
