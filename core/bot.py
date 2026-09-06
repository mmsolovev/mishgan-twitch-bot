import services.runtime as runtime

import config.settings as settings
from twitchio.ext import commands
from twitchio.ext.commands.exceptions import CommandNotFound

from core.context import SafeContext
from core.registry import load_commands

from sqlalchemy import select

from database.db import AsyncSessionLocal
from database.models import User
from services.command_usage_service import ensure_bot_commands, log_command_usage
from services.deferred_service import RecommendationSheetsSyncScheduler
from services.eventsub_service import EventSubService
from services.user_service import get_or_create_user, get_or_create_user_by_login


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            client_id=settings.CLIENT_ID,
            client_secret=settings.CLIENT_SECRET,
            bot_id=settings.BOT_ID,
            prefix=settings.BOT_PREFIX,
            case_insensitive=True,
        )

        self.commands_loaded = False
        self.eventsub_service = EventSubService(self)
        self.recommendation_sheets_sync_scheduler = RecommendationSheetsSyncScheduler()

        # ID собственных исходящих сообщений. Ловим эхо своих сообщений через EventSub,
        # чтобы не обрабатывать их как команды (в twitchio 3.x встроенный фильтр по
        # bot_id не подходит — нам нужны команды от собственного аккаунта).
        self._bot_message_ids: set[str] = set()

    def track_sent_message(self, message_id: str | None) -> None:
        if not message_id:
            return
        self._bot_message_ids.add(message_id)
        if len(self._bot_message_ids) > 5000:
            self._bot_message_ids.clear()

    async def event_ready(self):
        if not self.commands_loaded:
            load_commands(self)
            self.commands_loaded = True

            async with AsyncSessionLocal() as session:
                await ensure_bot_commands(session)

        try:
            await self.add_token(settings.TWITCH_ACCESS_TOKEN, settings.TWITCH_REFRESH_TOKEN)
        except Exception as exc:
            print(f"[Auth] add_token failed: {exc}")

        if not self.eventsub_service.connected:
            try:
                await self.eventsub_service.setup()
            except Exception as exc:
                print(f"[EventSub] setup failed: {exc}")

        print(f"Bot connected as {self.bot_id} to {settings.TWITCH_CHANNELS or [settings.TWITCH_PRIMARY_CHANNEL]}")
        print(f"Commands loaded: {list(self.commands.keys())}")
        print(f"[EventSub] subscriptions: {self.eventsub_service.subscriptions}")

    def get_context(self, payload, *, cls=None):
        return super().get_context(payload, cls=cls or SafeContext)

    async def event_message(self, payload):
        if payload.source_broadcaster is not None:
            return

        # Пропускаем только эхо собственных отправленных сообщений; командам от
        # собственного аккаунта (chatter.id == bot_id) разрешено работать.
        if payload.id in self._bot_message_ids:
            return

        if payload.text.startswith(self._get_prefix):
            print(f"[CMD] {payload.chatter.name}: {payload.text}")

        if not runtime.BOT_ENABLED:
            if not payload.text.casefold().startswith(f"{self._get_prefix}старт".casefold()):
                return

        await self.process_commands(payload)

    async def event_command_invoked(self, context):
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

    async def event_command_error(self, payload):
        context = payload.context
        error = payload.exception

        if isinstance(error, CommandNotFound):
            invoked = context.invoked_with or (context.message.text if context.message else "")
            print(f"\x1b[31m[CMD] Unknown command: {invoked}\x1b[0m")
            return

        await super().event_command_error(payload)

    async def event_channel_update(self, payload):
        await self.eventsub_service.dispatch("channel_update", payload)

    async def event_stream_online(self, payload):
        await self.eventsub_service.dispatch("stream_start", payload)

    async def event_stream_offline(self, payload):
        await self.eventsub_service.dispatch("stream_end", payload)

    async def event_raid(self, payload):
        await self.eventsub_service.dispatch("raid", payload)

    async def event_follow(self, payload):
        await self.eventsub_service.dispatch("follow", payload)

    async def event_shoutout_create(self, payload):
        await self.eventsub_service.dispatch("channel_shoutout_create", payload)