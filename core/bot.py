import services.runtime as runtime

from twitchio.ext import commands
from twitchio.ext.commands.errors import CommandNotFound

from core.context import SafeContext
from core.registry import load_commands
import config.settings as settings
from sqlalchemy import select

from database.db import AsyncSessionLocal
from database.models import User
from services.command_usage_service import ensure_bot_commands, log_command_usage
from services.eventsub_service import EventSubService
from services.deferred_service import RecommendationSheetsSyncScheduler
from services.user_service import get_or_create_user, get_or_create_user_by_login


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=settings.TWITCH_TOKEN,
            prefix=settings.BOT_PREFIX,
            nick=settings.TWITCH_NICK,
            initial_channels=list(settings.TWITCH_CHANNELS or [settings.TWITCH_PRIMARY_CHANNEL]),
            case_insensitive=True,
        )

        self.commands_loaded = False
        self.eventsub_service = EventSubService(self)
        self.recommendation_sheets_sync_scheduler = RecommendationSheetsSyncScheduler()

    async def event_ready(self):
        if not self.commands_loaded:
            load_commands(self)
            self.commands_loaded = True

            async with AsyncSessionLocal() as session:
                await ensure_bot_commands(session)

        if not self.eventsub_service.connected:
            try:
                await self.eventsub_service.setup()
            except Exception as exc:
                print(f"[EventSub] setup failed: {exc}")

        print(f"Bot connected as {self.nick} to {settings.TWITCH_CHANNELS or [settings.TWITCH_PRIMARY_CHANNEL]}")
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

    async def event_command(self, context):
        try:
            async with AsyncSessionLocal() as session:
                user = await get_or_create_user(session, context.author)

                result = await session.execute(
                    select(User).where(User.login == context.channel.name)
                )
                streamer = result.scalar_one_or_none()
                if streamer is None:
                    streamer = await get_or_create_user_by_login(session, context.channel.name)

                await log_command_usage(session, user, streamer, context.command.name)
                await session.commit()
        except Exception as exc:
            print(f"[CMD] logging error: {exc}")

    async def event_command_error(self, context, error):
        if isinstance(error, CommandNotFound):
            command_name = error.name or context.message.content.removeprefix(str(context.prefix)).split(" ", 1)[0]
            print(f"\x1b[31m[CMD] Unknown command: {command_name}\x1b[0m")
            return

        await super().event_command_error(context, error)

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
