import services.runtime as runtime

from twitchio.ext import commands

from core.context import SafeContext
from core.registry import load_commands
from config.settings import BOT_PREFIX, TWITCH_CHANNEL, TWITCH_NICK, TWITCH_TOKEN
from services.eventsub_service import EventSubService


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TWITCH_TOKEN,
            prefix=BOT_PREFIX,
            nick=TWITCH_NICK,
            initial_channels=[TWITCH_CHANNEL],
            case_insensitive=True,
        )

        self.commands_loaded = False
        self.eventsub_service = EventSubService(self)

    async def event_ready(self):
        if not self.commands_loaded:
            load_commands(self)
            self.commands_loaded = True

        if not self.eventsub_service.connected:
            try:
                await self.eventsub_service.setup()
            except Exception as exc:
                print(f"[EventSub] setup failed: {exc}")

        print(f"Bot connected as {self.nick} to {TWITCH_CHANNEL}")
        print(f"Commands loaded: {list(self.commands.keys())}")
        print(f"[EventSub] subscriptions: {self.eventsub_service.subscriptions}")

    async def get_context(self, message, *, cls=None):
        return await super().get_context(message, cls=cls or SafeContext)

    async def event_message(self, message):
        if not message.author:
            return

        if message.content.startswith(self._prefix):
            print(f"[CMD] {message.author.name}: {message.content}")

        if not runtime.BOT_ENABLED:
            if not message.content.casefold().startswith(f"{self._prefix}старт".casefold()):
                return

        await self.handle_commands(message)

    async def event_raw_usernotice(self, channel, tags):
        if tags.get("msg-id") != "raid":
            return

        raider = tags.get("msg-param-login")
        viewers = int(tags.get("msg-param-viewerCount", 0))

        print(f"[RAID][IRC] {raider} with {viewers} viewers")

    async def event_eventsub_notification_channel_update(self, payload):
        await self.eventsub_service.dispatch("channel_update", payload)

    async def event_eventsub_notification_stream_start(self, payload):
        await self.eventsub_service.dispatch("stream_start", payload)

    async def event_eventsub_notification_stream_end(self, payload):
        await self.eventsub_service.dispatch("stream_end", payload)

    async def event_eventsub_notification_raid(self, payload):
        await self.eventsub_service.dispatch("raid", payload)

    async def event_eventsub_notification_channel_shoutout_create(self, payload):
        await self.eventsub_service.dispatch("channel_shoutout_create", payload)

    async def event_eventsub_notification_channel_shoutout_receive(self, payload):
        await self.eventsub_service.dispatch("channel_shoutout_receive", payload)
