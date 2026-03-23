import asyncio
import random
import time

from twitchio.ext import commands
from core.registry import load_commands
from config.settings import BOT_PREFIX, TWITCH_TOKEN, TWITCH_NICK, TWITCH_CHANNEL
from services import runtime


class Bot(commands.Bot):

    def __init__(self):
        super().__init__(
            token=TWITCH_TOKEN,
            prefix=BOT_PREFIX,
            nick=TWITCH_NICK,
            initial_channels=[TWITCH_CHANNEL],
        )

        self.last_shoutout = 0
        self.recent_raids = set()

    async def event_ready(self):
        load_commands(self)

        print(f"✅ Bot connected as {self.nick} to {TWITCH_CHANNEL}")
        print(f"📜 Commands loaded: {list(self.commands.keys())}")

    async def event_message(self, message):
        if not message.author:
            return

        # лог команд
        if message.content.startswith(self._prefix):
            print(f"[CMD] {message.author.name}: {message.content}")

        # ❗ если бот выключен — игнорируем ВСЁ кроме !старт
        if not runtime.BOT_ENABLED:
            if not message.content.startswith(f"{self._prefix}старт"):
                return

        await self.handle_commands(message)

    async def event_raw_usernotice(self, channel, tags):
        if tags.get("msg-id") != "raid":
            return

        raider = tags.get("msg-param-login")
        viewers = int(tags.get("msg-param-viewerCount", 0))

        print(f"[RAID] {raider} with {viewers} viewers")

        # фильтр
        if not raider or viewers < 50:
            return

        # защита от дублей
        if raider in self.recent_raids:
            return

        self.recent_raids.add(raider)

        # защита от себя
        if raider.lower() == TWITCH_CHANNEL.lower():
            return

        await self.handle_raid(raider)

    async def handle_raid(self, raider: str):
        now = time.time()

        if now - self.last_shoutout < 120:
            print("[SO] cooldown active")
            return

        delay = random.uniform(2.5, 5.5)
        await asyncio.sleep(delay)

        channel = self.get_channel(TWITCH_CHANNEL)
        if not channel:
            return

        print(f"[SO] sending shoutout to {raider} after {delay:.2f}s")

        await channel.send(f"/shoutout {raider}")

        self.last_shoutout = time.time()
