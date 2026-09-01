import asyncio
import random
import re
import time

from twitchio.ext.eventsub.websocket import EventSubWSClient
from twitchio.http import Route

import config.settings as settings
from services.games_service import find_game_lookup
from services.hltb_service import get_hltb_summary, is_non_game_category
from runtime.collector import RuntimeStreamCollector
from utils.logger import get_logger


class EventSubService:
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("eventsub")
        self.client = EventSubWSClient(bot)
        self.collector = RuntimeStreamCollector(bot)
        self.connected = False
        self.subscriptions = {}
        self.watchdog_task = None
        self._watchdog_interval_seconds = 60
        self.channel_state = {}
        self.broadcaster_id = None
        self.moderator_id = None
        self.last_shoutout_at = 0.0
        self.next_shoutout_available_at = 0.0
        self.recent_raids = {}
        self._handlers = {
            "channel_update": self.on_channel_update,
            "stream_start": self.on_stream_start,
            "stream_end": self.on_stream_end,
            "raid": self.on_raid,
            "followV2": self.on_follow,
            "channel_shoutout_create": self.on_shoutout_create,
        }

    async def setup(self):
        if not settings.TWITCH_ACCESS_TOKEN:
            print("[EventSub] skipped: TWITCH_TOKEN is missing")
            return

        target_user, bot_user = await self.resolve_users()
        self.broadcaster_id = int(target_user.id)
        self.moderator_id = int(bot_user.id)
        await self.prime_channel_state(target_user.id)
        await self.collector.bootstrap(target_user.id, settings.TWITCH_ACCESS_TOKEN)
        results = await self.subscribe_topics(target_user.id, bot_user.id)

        self.connected = any(results.values())
        self.subscriptions = results

        self.logger.info(
            f"[EventSub] setup complete for {target_user.name} "
            f"(broadcaster_id={target_user.id}, bot_id={bot_user.id})"
        )

        if self.connected:
            await self.ensure_watchdog_started()

    async def resolve_users(self):
        users = await self.bot.fetch_users(
            names=[settings.TWITCH_PRIMARY_CHANNEL, settings.TWITCH_NICK],
            token=settings.TWITCH_ACCESS_TOKEN,
            force=True,
        )

        by_name = {user.name.lower(): user for user in users}
        target_user = by_name.get(settings.TWITCH_PRIMARY_CHANNEL.lower())
        bot_user = by_name.get(settings.TWITCH_NICK.lower())

        if not target_user:
            raise RuntimeError(f"Target channel '{settings.TWITCH_PRIMARY_CHANNEL}' was not found")

        if not bot_user:
            raise RuntimeError(f"Bot account '{settings.TWITCH_NICK}' was not found")

        if settings.BOT_ID and str(bot_user.id) != str(settings.BOT_ID):
            raise RuntimeError(
                f"settings.BOT_ID mismatch: .env has {settings.BOT_ID}, but Twitch API returned {bot_user.id} for {settings.TWITCH_NICK}"
            )

        return target_user, bot_user

    async def prime_channel_state(self, broadcaster_id: int):
        channel_info = await self.bot.fetch_channel(str(broadcaster_id), token=settings.TWITCH_ACCESS_TOKEN)
        self.channel_state = {
            "title": channel_info.title,
            "category_name": channel_info.game_name,
            "category_id": str(channel_info.game_id),
        }

    async def subscribe_topics(self, broadcaster_id: int, moderator_id: int):
        subscriptions = {
            "channel.update": lambda: self.client.subscribe_channel_update(broadcaster_id, settings.TWITCH_ACCESS_TOKEN),
            "stream.online": lambda: self.client.subscribe_channel_stream_start(
                broadcaster_id, settings.TWITCH_ACCESS_TOKEN
            ),
            "stream.offline": lambda: self.client.subscribe_channel_stream_end(
                broadcaster_id, settings.TWITCH_ACCESS_TOKEN
            ),
            "channel.raid.to": lambda: self.client.subscribe_channel_raid(
                settings.TWITCH_ACCESS_TOKEN,
                to_broadcaster=broadcaster_id,
            ),
            "channel.follow.v2": lambda: self.client.subscribe_channel_follows_v2(
                broadcaster_id,
                moderator_id,
                settings.TWITCH_ACCESS_TOKEN,
            ),
            "channel.shoutout.create": lambda: self.client.subscribe_channel_shoutout_create(
                broadcaster_id,
                moderator_id,
                settings.TWITCH_ACCESS_TOKEN,
            ),
        }

        results = {}
        for name, subscribe in subscriptions.items():
            try:
                await subscribe()
                results[name] = True
                self.logger.info("[EventSub] subscribed: %s", name)
            except Exception as exc:
                results[name] = False
                self.logger.warning("[EventSub] failed to subscribe %s: %s", name, exc)

        return results

    async def ensure_watchdog_started(self):
        if self.watchdog_task and not self.watchdog_task.done():
            return
        self.watchdog_task = self.bot.loop.create_task(self._watchdog_loop())
        self.logger.info(
            "[EventSub] watchdog started with interval %ss",
            self._watchdog_interval_seconds,
        )

    async def _watchdog_loop(self):
        try:
            while True:
                await asyncio.sleep(self._watchdog_interval_seconds)
                if not self._is_eventsub_healthy():
                    self.logger.warning(
                        "[EventSub] watchdog detected dead websocket, restarting EventSub"
                    )
                    await self.restart()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("[EventSub] watchdog loop failed: %s", exc)

    def _is_eventsub_healthy(self) -> bool:
        sockets = getattr(self.client, "_sockets", None)
        if not sockets:
            return False

        for sock in sockets:
            pump = getattr(sock, "_pump_task", None)
            if bool(sock.is_connected) and (pump is None or not pump.done()):
                return True
        return False

    async def restart(self):
        try:
            if self.watchdog_task and not self.watchdog_task.done():
                self.watchdog_task.cancel()
                try:
                    await self.watchdog_task
                except asyncio.CancelledError:
                    pass
            self.watchdog_task = None

            for sock in getattr(self.client, "_sockets", []):
                pump = getattr(sock, "_pump_task", None)
                if pump and not pump.done():
                    pump.cancel()
                try:
                    await sock._sock.close()
                except Exception:
                    pass
            self.client._sockets = []

            if not self.broadcaster_id or not self.moderator_id:
                await self.setup()
                return

            results = await self.subscribe_topics(self.broadcaster_id, self.moderator_id)
            self.connected = any(results.values())
            self.subscriptions = results

            if self.connected:
                await self.ensure_watchdog_started()
            self.logger.info("[EventSub] EventSub restarted, connected=%s", self.connected)
        except Exception as exc:
            self.logger.warning("[EventSub] restart failed, will retry next cycle: %s", exc)
            await self.ensure_watchdog_started()

    async def dispatch(self, event_name: str, payload):
        handler = self._handlers.get(event_name)
        if not handler:
            self.logger.info("[EventSub] no handler for %s", event_name)
            return

        await handler(payload.data)

    async def on_channel_update(self, data):
        previous_title = self.channel_state.get("title")
        previous_category = self.channel_state.get("category_name")
        category_changed = previous_category != data.category_name
        ignored_category = self._is_ignored_game_category(data.category_name)

        changes = []
        if category_changed:
            changes.append(f"game: '{previous_category}' -> '{data.category_name}'")
        if previous_title != data.title:
            changes.append(f"title: '{previous_title}' -> '{data.title}'")

        self.channel_state = {
            "title": data.title,
            "category_name": data.category_name,
            "category_id": data.category_id,
        }
        self.collector.handle_channel_update(data)

        if changes:
            self.logger.info("[EventSub] channel.update for %s: %s", data.broadcaster.name, ", ".join(changes))
        else:
            self.logger.info(
                "[EventSub] channel.update for %s: update received without title/category change",
                data.broadcaster.name,
            )

        if category_changed and ignored_category:
            self.logger.info("[EventSub] game-change ignored for category '%s'", data.category_name)
            return

        if category_changed:
            await self.announce_game_change(data.category_name)

    async def on_stream_start(self, data):
        self.logger.info(
            "[EventSub] stream.online for %s: started_at=%s type=%s",
            data.broadcaster.name,
            data.started_at.isoformat(),
            data.type,
        )
        try:
            stream_snapshot = await self.fetch_live_stream_snapshot()
        except Exception as exc:
            self.logger.warning("[EventSub] failed to fetch live stream snapshot on stream.online: %s", exc)
            stream_snapshot = None
        await self.collector.handle_stream_online(data, stream_snapshot=stream_snapshot)

    async def on_stream_end(self, data):
        self.logger.info("[EventSub] stream.offline for %s", data.broadcaster.name)
        await self.collector.handle_stream_offline()

    async def on_raid(self, data):
        self.logger.info(
            "[EventSub] channel.raid: %s -> %s viewers=%s",
            data.raider.name,
            data.reciever.name,
            data.viewer_count,
        )
        await self.maybe_send_raid_shoutout(data)

    async def on_follow(self, data):
        self.logger.info(
            "[EventSub] channel.follow.v2: %s followed %s at %s",
            data.user.name,
            data.broadcaster.name,
            data.followed_at.isoformat(),
        )
        self.collector.handle_follow(data)

    async def on_shoutout_create(self, data):
        self.logger.info(
            "[EventSub] channel.shoutout.create: %s -> %s viewer_count=%s",
            data.broadcaster.name,
            data.to_broadcaster.name,
            data.viewer_count,
        )
        self.last_shoutout_at = time.time()
        self.next_shoutout_available_at = data.cooldown_ends_at.timestamp()

    async def maybe_send_raid_shoutout(self, data):
        raider_login = data.raider.name.lower()
        now = time.time()

        self.prune_recent_raids(now)

        if data.viewer_count < 50:
            self.logger.info("[EventSub] shoutout skipped for %s: viewer_count < 50", raider_login)
            return

        if raider_login == settings.TWITCH_PRIMARY_CHANNEL.lower():
            self.logger.info("[EventSub] shoutout skipped for %s: same as target channel", raider_login)
            return

        if raider_login in self.recent_raids:
            self.logger.info("[EventSub] shoutout skipped for %s: duplicate raid event", raider_login)
            return

        if now < self.next_shoutout_available_at:
            self.logger.info("[EventSub] shoutout skipped for %s: broadcaster cooldown active", raider_login)
            return

        self.recent_raids[raider_login] = now

        delay = random.uniform(8.3, 10.2)
        self.logger.info("[EventSub] scheduling shoutout for %s after %.2fs", raider_login, delay)
        await asyncio.sleep(delay)

        try:
            await self.send_shoutout(data.raider.id, data.raider.name)
        except Exception as exc:
            self.logger.warning("[EventSub] shoutout failed for %s: %s", raider_login, exc)
            return

        self.last_shoutout_at = time.time()
        self.next_shoutout_available_at = self.last_shoutout_at + 120
        self.logger.info("[EventSub] shoutout sent for %s", raider_login)

    def prune_recent_raids(self, now: float):
        expiry_seconds = 1800
        expired = [login for login, seen_at in self.recent_raids.items() if now - seen_at > expiry_seconds]
        for login in expired:
            del self.recent_raids[login]

    async def send_shoutout(self, to_broadcaster_id: int, to_broadcaster_login: str):
        if not self.broadcaster_id or not self.moderator_id:
            raise RuntimeError("EventSub shoutout context is not initialized")

        route = Route(
            "POST",
            "chat/shoutouts",
            query=[
                ("from_broadcaster_id", str(self.broadcaster_id)),
                ("to_broadcaster_id", str(to_broadcaster_id)),
                ("moderator_id", str(self.moderator_id)),
            ],
            token=settings.TWITCH_ACCESS_TOKEN,
        )

        try:
            await self.bot._http.request(route, paginate=False)
        except Exception as exc:
            raise RuntimeError(
                f"Helix shoutout request failed for {to_broadcaster_login}. "
                f"Check moderator:manage:shoutouts scope, moderator status, and Twitch cooldowns. ({exc})"
            ) from exc

    async def announce_game_change(self, game_name: str):
        channel = self.bot.get_channel(settings.TWITCH_PRIMARY_CHANNEL)
        if not channel:
            self.logger.info(
                "[EventSub] game-change message skipped: channel %s is not available",
                settings.TWITCH_PRIMARY_CHANNEL,
            )
            return

        message = await self.build_game_change_message(game_name)
        self.logger.info("[EventSub] game-change chat message: %s", message)

        if settings.TWITCH_PRIMARY_CHANNEL.casefold() in {
            channel.casefold() for channel in (settings.TWITCH_BOT_BADGE_CHANNELS or [])
        }:
            from services import chat_sender
            sent = await chat_sender.send_message(settings.TWITCH_PRIMARY_CHANNEL, message)
            if sent:
                return
            self.logger.warning("[EventSub] game-change API send failed, falling back to IRC")

        await channel.send(message)

    async def build_game_change_message(self, game_name: str) -> str:
        game_lookup = await find_game_lookup(game_name)
        message_parts = []

        if game_lookup is None or game_lookup.streams_count <= 0:
            message_parts.append(f"MrDestructoid Игра {game_name} на канале впервые")
        else:
            message_parts.append(
                f"MrDestructoid Игра {game_lookup.name} уже была на стриме раз: {game_lookup.streams_count}, "
                f"часов в игре: {self._format_hours(game_lookup.hours_streamed)}, "
                f"последний стрим {self._format_date(game_lookup.last_stream)}"
            )

        hltb_summary = await get_hltb_summary(game_name)
        if hltb_summary:
            formatted_hltb = self._format_hltb_for_game_change(hltb_summary)
            if formatted_hltb:
                message_parts.append(formatted_hltb)

        if settings.GAMES_SHEET_URL:
            message_parts.append(f"Все игры канала: {settings.GAMES_SHEET_URL}")

        return " | ".join(message_parts)

    @staticmethod
    def _format_hours(value: float | None) -> str:
        if value is None:
            return "н/д"

        formatted = f"{value:.1f}".rstrip("0").rstrip(".")
        return formatted

    @staticmethod
    def _format_date(value) -> str:
        if not value:
            return "н/д"
        return value.strftime("%d.%m.%Y")

    @staticmethod
    def _format_hltb_for_game_change(summary: str) -> str | None:
        """Extract the overall playtime from the !hltb response header."""
        if not summary:
            return None

        marker = "по HowLongToBeat "
        idx = summary.find(marker)
        if idx < 0:
            return None

        time_raw = summary[idx + len(marker):].split(" | ", 1)[0].strip()
        if not time_raw or not re.search(r"\d", time_raw):
            return None

        return f"Прохождение по HLTB {time_raw}"

    async def fetch_live_stream_snapshot(self):
        streams = await self.bot.fetch_streams(
            user_logins=[settings.TWITCH_PRIMARY_CHANNEL],
            token=settings.TWITCH_ACCESS_TOKEN,
            type="live",
        )
        return streams[0] if streams else None

    @staticmethod
    def _is_ignored_game_category(category_name: str | None) -> bool:
        return is_non_game_category(category_name)
