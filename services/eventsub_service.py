import asyncio
import random
import time

from twitchio.ext.eventsub.websocket import EventSubWSClient
from twitchio.http import Route

from config.settings import BOT_ID, TWITCH_ACCESS_TOKEN, TWITCH_CHANNEL, TWITCH_NICK


class EventSubService:
    def __init__(self, bot):
        self.bot = bot
        self.client = EventSubWSClient(bot)
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
            "channel_shoutout_create": self.on_shoutout_create,
            "channel_shoutout_receive": self.on_shoutout_receive,
        }

    async def setup(self):
        if not TWITCH_ACCESS_TOKEN:
            print("[EventSub] skipped: TWITCH_TOKEN is missing")
            return

        target_user, bot_user = await self.resolve_users()
        self.broadcaster_id = int(target_user.id)
        self.moderator_id = int(bot_user.id)
        await self.prime_channel_state(target_user.id)
        results = await self.subscribe_topics(target_user.id, bot_user.id)

        self.connected = any(results.values())
        self.subscriptions = results

        print(
            f"[EventSub] setup complete for {target_user.name} "
            f"(broadcaster_id={target_user.id}, bot_id={bot_user.id})"
        )

    async def resolve_users(self):
        users = await self.bot.fetch_users(
            names=[TWITCH_CHANNEL, TWITCH_NICK],
            token=TWITCH_ACCESS_TOKEN,
            force=True,
        )

        by_name = {user.name.lower(): user for user in users}
        target_user = by_name.get(TWITCH_CHANNEL.lower())
        bot_user = by_name.get(TWITCH_NICK.lower())

        if not target_user:
            raise RuntimeError(f"Target channel '{TWITCH_CHANNEL}' was not found")

        if not bot_user:
            raise RuntimeError(f"Bot account '{TWITCH_NICK}' was not found")

        if BOT_ID and str(bot_user.id) != str(BOT_ID):
            raise RuntimeError(
                f"BOT_ID mismatch: .env has {BOT_ID}, but Twitch API returned {bot_user.id} for {TWITCH_NICK}"
            )

        return target_user, bot_user

    async def prime_channel_state(self, broadcaster_id: int):
        channel_info = await self.bot.fetch_channel(str(broadcaster_id), token=TWITCH_ACCESS_TOKEN)
        self.channel_state = {
            "title": channel_info.title,
            "category_name": channel_info.game_name,
            "category_id": str(channel_info.game_id),
        }

    async def subscribe_topics(self, broadcaster_id: int, moderator_id: int):
        subscriptions = {
            "channel.update": lambda: self.client.subscribe_channel_update(broadcaster_id, TWITCH_ACCESS_TOKEN),
            "stream.online": lambda: self.client.subscribe_channel_stream_start(
                broadcaster_id, TWITCH_ACCESS_TOKEN
            ),
            "stream.offline": lambda: self.client.subscribe_channel_stream_end(
                broadcaster_id, TWITCH_ACCESS_TOKEN
            ),
            "channel.raid.to": lambda: self.client.subscribe_channel_raid(
                TWITCH_ACCESS_TOKEN,
                to_broadcaster=broadcaster_id,
            ),
            "channel.shoutout.create": lambda: self.client.subscribe_channel_shoutout_create(
                broadcaster_id,
                moderator_id,
                TWITCH_ACCESS_TOKEN,
            ),
            "channel.shoutout.receive": lambda: self.client.subscribe_channel_shoutout_receive(
                broadcaster_id,
                moderator_id,
                TWITCH_ACCESS_TOKEN,
            ),
        }

        results = {}
        for name, subscribe in subscriptions.items():
            try:
                await subscribe()
                results[name] = True
                print(f"[EventSub] subscribed: {name}")
            except Exception as exc:
                results[name] = False
                print(f"[EventSub] failed to subscribe {name}: {exc}")

        return results

    async def dispatch(self, event_name: str, payload):
        handler = self._handlers.get(event_name)
        if not handler:
            print(f"[EventSub] no handler for {event_name}")
            return

        await handler(payload.data)

    async def on_channel_update(self, data):
        previous_title = self.channel_state.get("title")
        previous_category = self.channel_state.get("category_name")

        changes = []
        if previous_category != data.category_name:
            changes.append(f"game: '{previous_category}' -> '{data.category_name}'")
        if previous_title != data.title:
            changes.append(f"title: '{previous_title}' -> '{data.title}'")

        self.channel_state = {
            "title": data.title,
            "category_name": data.category_name,
            "category_id": data.category_id,
        }

        if changes:
            print(f"[EventSub] channel.update for {data.broadcaster.name}: {', '.join(changes)}")
            return

        print(
            f"[EventSub] channel.update for {data.broadcaster.name}: "
            "update received without title/category change"
        )

    async def on_stream_start(self, data):
        print(
            f"[EventSub] stream.online for {data.broadcaster.name}: "
            f"started_at={data.started_at.isoformat()} type={data.type}"
        )

    async def on_stream_end(self, data):
        print(f"[EventSub] stream.offline for {data.broadcaster.name}")

    async def on_raid(self, data):
        print(
            f"[EventSub] channel.raid: {data.raider.name} -> {data.reciever.name} "
            f"viewers={data.viewer_count}"
        )
        await self.maybe_send_raid_shoutout(data)

    async def on_shoutout_create(self, data):
        print(
            f"[EventSub] channel.shoutout.create: {data.broadcaster.name} -> {data.to_broadcaster.name} "
            f"viewer_count={data.viewer_count}"
        )
        self.last_shoutout_at = time.time()
        self.next_shoutout_available_at = data.cooldown_ends_at.timestamp()

    async def on_shoutout_receive(self, data):
        print(
            f"[EventSub] channel.shoutout.receive: {data.from_broadcaster.name} -> {data.broadcaster.name} "
            f"viewer_count={data.viewer_count}"
        )

    async def maybe_send_raid_shoutout(self, data):
        raider_login = data.raider.name.lower()
        now = time.time()

        self.prune_recent_raids(now)

        if data.viewer_count < 50:
            print(f"[EventSub] shoutout skipped for {raider_login}: viewer_count < 50")
            return

        if raider_login == TWITCH_CHANNEL.lower():
            print(f"[EventSub] shoutout skipped for {raider_login}: same as target channel")
            return

        if raider_login in self.recent_raids:
            print(f"[EventSub] shoutout skipped for {raider_login}: duplicate raid event")
            return

        if now < self.next_shoutout_available_at:
            print(f"[EventSub] shoutout skipped for {raider_login}: broadcaster cooldown active")
            return

        self.recent_raids[raider_login] = now

        delay = random.uniform(2.5, 5.5)
        print(f"[EventSub] scheduling shoutout for {raider_login} after {delay:.2f}s")
        await asyncio.sleep(delay)

        try:
            await self.send_shoutout(data.raider.id, data.raider.name)
        except Exception as exc:
            print(f"[EventSub] shoutout failed for {raider_login}: {exc}")
            return

        self.last_shoutout_at = time.time()
        self.next_shoutout_available_at = self.last_shoutout_at + 120
        print(f"[EventSub] shoutout sent for {raider_login}")

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
            token=TWITCH_ACCESS_TOKEN,
        )

        try:
            await self.bot._http.request(route, paginate=False)
        except Exception as exc:
            raise RuntimeError(
                f"Helix shoutout request failed for {to_broadcaster_login}. "
                f"Check moderator:manage:shoutouts scope, moderator status, and Twitch cooldowns. ({exc})"
            ) from exc
