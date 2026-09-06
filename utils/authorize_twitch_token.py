"""Перевыпуск user-токена бота через Twitch Device Code Flow.

twitchio v3 читает чат через EventSub channel.chat.message, а это требует
scope user:read:chat на user-токене. Старые токены (IRC-эпоха) его не имеют,
поэтому подписка падает с 403 "subscription missing proper authorization".

Запуск:
    .venv\\Scripts\\python.exe utils\\authorize_twitch_token.py

Использует CLIENT_ID/CLIENT_SECRET из .env, результат записывает обратно
в .env (TWITCH_TOKEN / TWITCH_REFRESH_TOKEN).
"""

import asyncio
import os
import sys
from pathlib import Path

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.token_service import update_env_tokens, validate_token  # noqa: E402

SCOPES = [
    "user:read:chat",
    "user:write:chat",
    "moderator:read:followers",
    "moderator:read:shoutouts",
    "moderator:manage:shoutouts",
    "user:bot",
    "channel:bot",
]

DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

POLL_TIMEOUT_SECONDS = 300


def _load_client_id() -> str:
    client_id = os.getenv("CLIENT_ID")
    if not client_id:
        for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("CLIENT_ID="):
                client_id = line.split("=", 1)[1].strip()
                break
    if not client_id:
        raise RuntimeError("CLIENT_ID is not set in .env")
    return client_id


def _load_client_secret() -> str:
    client_secret = os.getenv("CLIENT_SECRET")
    if not client_secret:
        for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("CLIENT_SECRET="):
                client_secret = line.split("=", 1)[1].strip()
                break
    if not client_secret:
        raise RuntimeError("CLIENT_SECRET is not set in .env")
    return client_secret


async def _request_device_code(session: aiohttp.ClientSession, client_id: str):
    params = {
        "client_id": client_id,
        "scopes": " ".join(SCOPES),
    }
    async with session.post(DEVICE_URL, params=params) as resp:
        body = await resp.json()
        if resp.status != 200:
            raise RuntimeError(f"Device code request failed ({resp.status}): {body}")
        return body


async def _poll_for_token(
    session: aiohttp.ClientSession,
    client_id: str,
    client_secret: str,
    device_code: str,
    interval: float,
):
    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }

    elapsed = 0.0
    while elapsed < POLL_TIMEOUT_SECONDS:
        await asyncio.sleep(interval)
        elapsed += interval

        async with session.post(TOKEN_URL, params=params) as resp:
            body = await resp.json()

        if resp.status == 200:
            return body

        error = body.get("error") or body.get("message")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise RuntimeError(f"Device flow failed ({resp.status}): {body}")

    raise TimeoutError("Authorization not completed in time")


async def main() -> None:
    client_id = _load_client_id()
    client_secret = _load_client_secret()

    async with aiohttp.ClientSession() as session:
        device = await _request_device_code(session, client_id)

        verification_uri = device.get("verification_uri") or device.get("verification_url")
        user_code = device.get("user_code", "")
        expires_in = device.get("expires_in", 0)
        interval = device.get("interval", 5)

        print("=" * 60)
        print("1. Открой ссылку и войди под аккаунтом бота:")
        print(f"   {verification_uri}")
        print()
        print("2. Введи код:")
        print(f"   {user_code}")
        print()
        print(f"Код истекает через {expires_in} сек. Ожидаю подтверждения...")
        print("=" * 60)

        token_data = await _poll_for_token(session, client_id, client_secret, device["device_code"], interval)

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    granted_scopes = token_data.get("scope", "")

    if not refresh_token:
        raise RuntimeError("Twitch did not return a refresh_token")

    update_env_tokens(access_token, refresh_token)

    validation = await validate_token(access_token)
    login = validation.get("login") if validation else "?"
    scopes_list = ", ".join(granted_scopes) if isinstance(granted_scopes, list) else granted_scopes

    print()
    print("Токен обновлён и записан в .env")
    print(f"Аккаунт: {login}")
    print(f"Scopes : {scopes_list}")

    missing = [scope for scope in SCOPES if scope not in scopes_list]
    if missing:
        print(f"Внимание: не выданы scopes: {', '.join(missing)}")
        print("Проверь, что ты авторизовал все разрешения на экране Twitch.")


if __name__ == "__main__":
    asyncio.run(main())