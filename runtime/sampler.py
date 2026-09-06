from twitchio.http import Route

import config.settings as settings
from services.token_service import try_refresh_token
from utils.logger import get_logger

logger = get_logger("runtime.sampler")


async def fetch_live_stream(bot):
    streams = await bot.fetch_streams(
        user_logins=[settings.TWITCH_PRIMARY_CHANNEL],
        token_for=bot.bot_id,
        type="live",
    )
    return streams[0] if streams else None


async def fetch_followers_count(bot, broadcaster_id: int) -> int | None:
    route = Route(
        "GET",
        "channels/followers",
        params={"broadcaster_id": str(broadcaster_id), "first": "1"},
        token_for=bot.bot_id,
    )

    try:
        response = await bot._http.request_json(route)
    except Exception as exc:
        logger.warning(
            "Failed to fetch followers total. "
            "Check moderator:read:followers scope and moderator access. (%s)",
            exc,
        )
        return None

    total = response.get("total")
    return int(total) if total is not None else None


async def fetch_live_stream_with_refresh(bot):
    try:
        return await fetch_live_stream(bot)
    except Exception:
        new_token = await try_refresh_token()
        if new_token:
            settings.TWITCH_ACCESS_TOKEN = new_token
            return await fetch_live_stream(bot)
        raise