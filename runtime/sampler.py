from twitchio.http import Route

import config.settings as settings
from services.token_service import try_refresh_token
from utils.logger import get_logger

logger = get_logger("runtime.sampler")

async def fetch_live_stream(bot, token: str):
    streams = await bot.fetch_streams(
        user_logins=[settings.TWITCH_PRIMARY_CHANNEL],
        token=token,
        type="live",
    )
    return streams[0] if streams else None


async def fetch_followers_count(bot, broadcaster_id: int, token: str) -> int | None:
    route = Route(
        "GET",
        "channels/followers",
        query=[
            ("broadcaster_id", str(broadcaster_id)),
            ("first", "1"),
        ],
        token=token,
    )

    try:
        response = await bot._http.request(route, paginate=False, full_body=True)
    except Exception as exc:
        logger.warning(
            "Failed to fetch followers total. "
            "Check moderator:read:followers scope and moderator access. (%s)",
            exc,
        )
        return None

    total = response.get("total")
    return int(total) if total is not None else None


async def fetch_live_stream_with_refresh(bot, token: str):
    try:
        return await fetch_live_stream(bot, token)
    except Exception:
        new_token = await try_refresh_token()
        if new_token:
            settings.TWITCH_ACCESS_TOKEN = new_token
            return await fetch_live_stream(bot, new_token)
        raise
