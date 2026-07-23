"""
Pipeline ingest layer: считывание данных о канале из Twitch API.

"""

from __future__ import annotations

from typing import Any

import aiohttp


TWITCH_API = "https://api.twitch.tv/helix"


class TwitchTokenExpiredError(Exception):
    pass


def _headers(*, client_id: str, access_token: str) -> dict[str, str]:
    return {
        "Client-ID": client_id,
        "Authorization": f"Bearer {access_token}",
    }


async def fetch_user_id(
    session: aiohttp.ClientSession,
    *,
    client_id: str,
    access_token: str,
    channel_login: str,
) -> str:
    params = {"login": channel_login}
    async with session.get(
        f"{TWITCH_API}/users",
        headers=_headers(client_id=client_id, access_token=access_token),
        params=params,
    ) as resp:
        if resp.status == 401:
            raise TwitchTokenExpiredError("Twitch API returned 401 Unauthorized for /users")
        data: dict[str, Any] = await resp.json()

    rows = data.get("data") or []
    if not rows:
        raise ValueError(f"Twitch API returned empty users list for channel_login={channel_login!r}")

    return str(rows[0]["id"])


async def fetch_vods(
    session: aiohttp.ClientSession,
    *,
    client_id: str,
    access_token: str,
    user_id: str,
    first: int = 100,
) -> list[dict[str, Any]]:
    """
    Получает все записи стримов (VOD archive) канала.

    """
    vods: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        params: dict[str, Any] = {
            "user_id": user_id,
            "type": "archive",
            "first": int(first),
        }
        if cursor:
            params["after"] = cursor

        async with session.get(
            f"{TWITCH_API}/videos",
            headers=_headers(client_id=client_id, access_token=access_token),
            params=params,
        ) as resp:
            if resp.status == 401:
                raise TwitchTokenExpiredError("Twitch API returned 401 Unauthorized for /videos")
            data: dict[str, Any] = await resp.json()

        vods.extend(list(data.get("data") or []))
        cursor = (data.get("pagination") or {}).get("cursor") or None
        if not cursor:
            break

    return vods
