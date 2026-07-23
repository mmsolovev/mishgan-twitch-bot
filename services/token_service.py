import os
import shutil
from pathlib import Path

import aiohttp

from config.settings import CLIENT_ID, CLIENT_SECRET
from utils.logger import get_logger

logger = get_logger("token_service")

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
ENV_BACKUP_PATH = ENV_PATH.with_suffix(".env.bak")

CURRENT_ACCESS_TOKEN: str | None = None
CURRENT_REFRESH_TOKEN: str | None = None


def _load_env_tokens() -> tuple[str | None, str | None]:
    access_token = None
    refresh_token = None
    if not ENV_PATH.exists():
        return access_token, refresh_token
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("TWITCH_TOKEN="):
            raw = line.split("=", 1)[1]
            access_token = raw.removeprefix("oauth:")
        elif line.startswith("TWITCH_REFRESH_TOKEN="):
            refresh_token = line.split("=", 1)[1]
    return access_token, refresh_token


async def validate_token(access_token: str) -> dict | None:
    url = "https://id.twitch.tv/oauth2/validate"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
    except Exception:
        return None


async def refresh_access_token(refresh_token: str) -> dict | None:
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                body = await resp.text()
                logger.warning("Refresh token request failed: %s %s", resp.status, body)
                return None
    except Exception as exc:
        logger.warning("Refresh token request error: %s", exc)
        return None


def update_env_tokens(access_token: str, refresh_token: str) -> None:
    if ENV_PATH.exists() and not ENV_BACKUP_PATH.exists():
        shutil.copy2(ENV_PATH, ENV_BACKUP_PATH)

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = []
    found_token = False
    found_refresh = False
    for line in lines:
        if line.startswith("TWITCH_TOKEN="):
            new_lines.append(f"TWITCH_TOKEN=oauth:{access_token}\n")
            found_token = True
        elif line.startswith("TWITCH_REFRESH_TOKEN="):
            new_lines.append(f"TWITCH_REFRESH_TOKEN={refresh_token}\n")
            found_refresh = True
        else:
            new_lines.append(line)
    if not found_token:
        new_lines.append(f"TWITCH_TOKEN=oauth:{access_token}\n")
    if not found_refresh:
        new_lines.append(f"TWITCH_REFRESH_TOKEN={refresh_token}\n")
    ENV_PATH.write_text("".join(new_lines), encoding="utf-8")


def get_current_tokens() -> tuple[str | None, str | None]:
    global CURRENT_ACCESS_TOKEN, CURRENT_REFRESH_TOKEN
    if CURRENT_ACCESS_TOKEN and CURRENT_REFRESH_TOKEN:
        return CURRENT_ACCESS_TOKEN, CURRENT_REFRESH_TOKEN
    CURRENT_ACCESS_TOKEN, CURRENT_REFRESH_TOKEN = _load_env_tokens()
    return CURRENT_ACCESS_TOKEN, CURRENT_REFRESH_TOKEN


def set_current_tokens(access_token: str, refresh_token: str) -> None:
    global CURRENT_ACCESS_TOKEN, CURRENT_REFRESH_TOKEN
    CURRENT_ACCESS_TOKEN = access_token
    CURRENT_REFRESH_TOKEN = refresh_token


async def ensure_valid_token() -> str:
    global CURRENT_ACCESS_TOKEN, CURRENT_REFRESH_TOKEN

    access_token, refresh_token = get_current_tokens()
    if not access_token:
        raise RuntimeError("TWITCH_TOKEN is not set in .env")

    validation = await validate_token(access_token)
    if validation:
        logger.info("Token is valid for login=%s", validation.get("login"))
        return access_token

    logger.info("Token expired or invalid, attempting refresh...")

    if not refresh_token:
        raise RuntimeError(
            "Token is invalid and TWITCH_REFRESH_TOKEN is not set. "
            "Re-authorize at https://id.twitch.tv/oauth2/authorize"
        )

    result = await refresh_access_token(refresh_token)
    if not result or "access_token" not in result:
        raise RuntimeError(
            "Token refresh failed. Re-authorize at https://id.twitch.tv/oauth2/authorize"
        )

    new_access = result["access_token"]
    new_refresh = result.get("refresh_token", refresh_token)

    update_env_tokens(new_access, new_refresh)
    set_current_tokens(new_access, new_refresh)

    os.environ["TWITCH_TOKEN"] = f"oauth:{new_access}"
    os.environ["TWITCH_REFRESH_TOKEN"] = new_refresh

    logger.info("Token refreshed and .env updated successfully")
    return new_access


async def try_refresh_token() -> str | None:
    global CURRENT_ACCESS_TOKEN, CURRENT_REFRESH_TOKEN

    _, refresh_token = get_current_tokens()
    if not refresh_token:
        return None

    result = await refresh_access_token(refresh_token)
    if not result or "access_token" not in result:
        return None

    new_access = result["access_token"]
    new_refresh = result.get("refresh_token", refresh_token)

    update_env_tokens(new_access, new_refresh)
    set_current_tokens(new_access, new_refresh)

    os.environ["TWITCH_TOKEN"] = f"oauth:{new_access}"
    os.environ["TWITCH_REFRESH_TOKEN"] = new_refresh

    logger.info("Token refreshed successfully via try_refresh_token")
    return new_access
