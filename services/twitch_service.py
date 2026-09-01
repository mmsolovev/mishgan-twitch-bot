import time

import aiohttp

from config.settings import TWITCH_PRIMARY_CHANNEL, CLIENT_ID, CLIENT_SECRET

_app_token_cache: dict = {"token": None, "expires_at": 0.0}


async def get_current_game():
    url = f"https://api.twitch.tv/helix/streams?user_login={TWITCH_PRIMARY_CHANNEL}"
    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {await get_app_access_token()}"
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            if data.get("data"):
                return data["data"][0].get("game_name")
    return None


async def get_app_access_token():
    now = time.time()
    cached = _app_token_cache.get("token")
    if cached and _app_token_cache.get("expires_at", 0.0) > now + 60:
        return cached

    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, params=params) as resp:
            data = await resp.json()

    token = data["access_token"]
    _app_token_cache["token"] = token
    _app_token_cache["expires_at"] = time.time() + int(data.get("expires_in", 4000000))
    return token
