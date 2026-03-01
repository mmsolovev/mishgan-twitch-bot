from twitchio.ext import commands
from core.registry import load_commands
from config.settings import BOT_PREFIX, TWITCH_TOKEN, TWITCH_NICK, TWITCH_CHANNEL


class Bot(commands.Bot):

    def __init__(self):
        super().__init__(
            token=TWITCH_TOKEN,
            prefix=BOT_PREFIX,
            nick=TWITCH_NICK,
            initial_channels=[TWITCH_CHANNEL],
        )

    async def event_ready(self):
        load_commands(self)

        print(f"✅ Bot connected as {self.nick}")
        print(f"📜 Commands loaded: {list(self.commands.keys())}")

    async def event_message(self, message):
        if not message.author:
            return

        # логируем только команды
        if message.content.startswith(self._prefix):
            print(f"[CMD] {message.author.name}: {message.content}")

        # передаём сообщение дальше,
        await self.handle_commands(message)


