import asyncio
import random
import re
import time

from twitchio import eventsub
from twitchio.http import Route

import config.settings as settings
from services.games_service import find_game_lookup
from services.hltb_service import get_hltb_summary, is_non_game_category
from runtime.collector import RuntimeStreamCollector
from services import chat_sender
from services.token_service import validate_token
from utils.logger import get_logger


class EventSubService:
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("eventsub")
        self.collector = RuntimeStreamCollector(bot)
        self.connected = False
        self.subscriptions = {}
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
            "follow": self.on_follow,
            "channel_shoutout_create": self.on_shoutout_create,
        }

    async def setup(self):
        if not settings.TWITCH_ACCESS_TOKEN:
            print("[EventSub] skipped: TWITCH_TOKEN is missing")
            return

        await self._check_token_scopes()

        target_user, bot_user = await self.resolve_users()
        self.broadcaster_id = int(target_user.id)
        self.moderator_id = int(bot_user.id)
        await self.prime_channel_state(target_user.id)
        await self.collector.bootstrap(target_user.id)
        results = await self.subscribe_topics(target_user.id, bot_user.id)

        self.connected = any(results.values())
        self.subscriptions = results

        self.logger.info(
            "[EventSub] setup complete for %s (broadcaster_id=%s, bot_id=%s)",
            target_user.name,
            target_user.id,
            bot_user.id,
        )

    async def _check_token_scopes(self) -> None:
        validation = await validate_token(settings.TWITCH_ACCESS_TOKEN or "")
        if not validation:
            self.logger.warning("[EventSub] could not validate token scopes")
            return

        granted = set(validation.get("scopes") or [])
        required = {
            "channel.chat.message": "user:read:chat",
            "channel.follow": "moderator:read:followers",
            "channel.shoutout.create": "moderator:read:shoutouts",
        }
        for name, scope in required.items():
            if scope not in granted:
                self.logger.warning(
                    "[EventSub] user token is missing scope '%s' required for subscription '%s'. "
                    "Re-authorize the token via 'utils/authorize_twitch_token.py'",
                    scope,
                    name,
                )

    async def resolve_users(self):
        users = await self.bot.fetch_users(
            logins=[settings.TWITCH_PRIMARY_CHANNEL, settings.TWITCH_NICK],
            token_for=self.bot.bot_id,
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
        channel_info = await self.bot.fetch_channel(str(broadcaster_id), token_for=self.bot.bot_id)
        self.channel_state = {
            "title": channel_info.title,
            "category_name": channel_info.game_name,
            "category_id": str(channel_info.game_id),
        }

    async def _resolve_channel_id(self, channel_name: str) -> int | None:
        try:
            users = await self.bot.fetch_users(logins=[channel_name], token_for=self.bot.bot_id)
            for user in users:
                if user.name.casefold() == channel_name.casefold():
                    return int(user.id)
        except Exception as exc:
            self.logger.warning("[EventSub] failed to resolve channel '%s': %s", channel_name, exc)
        return None

    async def subscribe_topics(self, broadcaster_id: int, moderator_id: int):
        subscribers = {
            "channel.update": eventsub.ChannelUpdateSubscription(
                broadcaster_user_id=str(broadcaster_id)
            ),
            "stream.online": eventsub.StreamOnlineSubscription(
                broadcaster_user_id=str(broadcaster_id)
            ),
            "stream.offline": eventsub.StreamOfflineSubscription(
                broadcaster_user_id=str(broadcaster_id)
            ),
            "channel.raid.to": eventsub.ChannelRaidSubscription(
                to_broadcaster_user_id=str(broadcaster_id)
            ),
            "channel.follow.v2": eventsub.ChannelFollowSubscription(
                broadcaster_user_id=str(broadcaster_id),
                moderator_user_id=str(moderator_id),
            ),
            "channel.shoutout.create": eventsub.ShoutoutCreateSubscription(
                broadcaster_user_id=str(broadcaster_id),
                moderator_user_id=str(moderator_id),
            ),
        }

        results = {}
        for name, payload in subscribers.items():
            try:
                await self.bot.subscribe_websocket(payload, as_bot=True)
                results[name] = True
                self.logger.info("[EventSub] subscribed: %s", name)
            except Exception as exc:
                results[name] = False
                self.logger.warning("[EventSub] failed to subscribe %s: %s", name, exc)

        channels = settings.TWITCH_CHANNELS or [settings.TWITCH_PRIMARY_CHANNEL]
        for channel_name in channels:
            channel_id = await self._resolve_channel_id(channel_name)
            if channel_id is None:
                continue
            payload = eventsub.ChatMessageSubscription(
                broadcaster_user_id=str(channel_id),
                user_id=self.bot.bot_id,
            )
            name = f"channel.chat.message.{channel_name}"
            try:
                await self.bot.subscribe_websocket(payload, as_bot=True)
                results[name] = True
                self.logger.info("[EventSub] subscribed: %s", name)
            except Exception as exc:
                results[name] = False
                self.logger.warning(
                    "[EventSub] failed to subscribe %s: %s. "
                    "Hint: requires scope 'user:read:chat' on the bot user token and "
                    "the bot being a moderator/broadcaster of the channel. "
                    "Re-authorize via 'utils/authorize_twitch_token.py'",
                    name,
                    exc,
                )

        return results

    async def dispatch(self, event_name: str, payload):
        handler = self._handlers.get(event_name)
        if not handler:
            self.logger.info("[EventSub] no handler for %s", event_name)
            return

        await handler(payload)

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
            data.from_broadcaster.name,
            data.to_broadcaster.name,
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
        self.next_shoutout_available_at = data.cooldown_until.timestamp()

    async def maybe_send_raid_shoutout(self, data):
        raider_login = data.from_broadcaster.name.lower()
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
            await self.send_shoutout(data.from_broadcaster.id, data.from_broadcaster.name)
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
            params={
                "from_broadcaster_id": str(self.broadcaster_id),
                "to_broadcaster_id": str(to_broadcaster_id),
                "moderator_id": str(self.moderator_id),
            },
            token_for=self.bot.bot_id,
        )

        try:
            await self.bot._http.request(route, paginate=False)
        except Exception as exc:
            raise RuntimeError(
                f"Helix shoutout request failed for {to_broadcaster_login}. "
                f"Check moderator:manage:shoutouts scope, moderator status, and Twitch cooldowns. ({exc})"
            ) from exc

    async def announce_game_change(self, game_name: str):
        message = await self.build_game_change_message(game_name)
        self.logger.info("[EventSub] game-change chat message: %s", message)

        if settings.TWITCH_PRIMARY_CHANNEL.casefold() in {
            channel.casefold() for channel in (settings.TWITCH_BOT_BADGE_CHANNELS or [])
        }:
            sent = await chat_sender.send_message(settings.TWITCH_PRIMARY_CHANNEL, message)
            if sent:
                return
            self.logger.warning("[EventSub] game-change API send failed, falling back to v3 API")

        try:
            target_user = await self.bot.fetch_users(
                logins=[settings.TWITCH_PRIMARY_CHANNEL],
                token_for=self.bot.bot_id,
            )
            chat_user = self.bot.create_partialuser(self.bot.bot_id)
            await target_user[0].send_message(
                message,
                sender=chat_user,
                token_for=self.bot.bot_id,
            )
        except Exception as exc:
            self.logger.warning("[EventSub] game-change send failed: %s", exc)

    async def build_game_change_message(self, game_name: str) -> str:
        game_lookup = await find_game_lookup(game_name)
        message_parts = []

        if game_lookup is None or game_lookup.streams_count <= 0:
            message_parts.append(f"Игра: {game_name} | На канале впервые")
        else:
            rank_suffix = f" (#{game_lookup.rank})" if game_lookup.rank else ""
            message_parts.append(
                f"Игра: {game_lookup.name} | "
                f"Было стримов по игре: {game_lookup.streams_count} | "
                f"Последний стрим: {self._format_date(game_lookup.last_stream)} | "
                f"Времени в игре: {self._format_hours_minutes(game_lookup.hours_streamed)}{rank_suffix}"
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
    def _format_hours_minutes(value: float | None) -> str:
        if value is None:
            return "н/д"

        total_minutes = round(value * 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours and minutes:
            return f"{hours} ч {minutes} м"
        if hours:
            return f"{hours} ч"
        return f"{minutes} м"

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
            token_for=self.bot.bot_id,
            type="live",
        )
        return streams[0] if streams else None

    @staticmethod
    def _is_ignored_game_category(category_name: str | None) -> bool:
        return is_non_game_category(category_name)