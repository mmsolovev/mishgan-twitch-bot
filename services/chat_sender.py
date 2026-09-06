import asyncio

import aiohttp

import config.settings as settings
from services.twitch_service import get_app_access_token
from utils.logger import get_logger

logger = get_logger("chat_sender")

CHAT_MESSAGES_URL = "https://api.twitch.tv/helix/chat/messages"
USERS_URL = "https://api.twitch.tv/helix/users"
MAX_MESSAGE_LENGTH = 500

_id_cache: dict[str, str] = {}
_id_lock = asyncio.Lock()


def _truncate_message(message: str) -> str:
    if len(message) <= MAX_MESSAGE_LENGTH:
        return message
    return message[: MAX_MESSAGE_LENGTH - 3].rstrip() + "..."


async def resolve_user_id(login: str) -> str | None:
    login = login.lower()
    if login in _id_cache:
        return _id_cache[login]

    token = await get_app_access_token()
    headers = {"Client-ID": settings.CLIENT_ID, "Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession() as session:
        async with session.get(USERS_URL, params={"login": login}, headers=headers) as resp:
            if resp.status != 200:
                logger.warning("resolve_user_id failed for %s: %s", login, resp.status)
                return None
            data = await resp.json()

    if not data.get("data"):
        logger.warning("resolve_user_id: no user for login %s", login)
        return None

    user_id = data["data"][0]["id"]
    async with _id_lock:
        _id_cache[login] = user_id
    return user_id


async def send_message(broadcaster_login: str, message: str) -> str | None:
    """Отправить сообщение через Send Chat Message API (даёт Chat Bot Badge).

    Возвращает message_id при успешной доставке через API, иначе None
    (caller может сделать fallback).
    """
    try:
        sender_id = await resolve_user_id(settings.TWITCH_NICK)
        broadcaster_id = await resolve_user_id(broadcaster_login)
        if not sender_id or not broadcaster_id:
            logger.warning("send_message skipped: could not resolve ids (sender=%s, bcast=%s)",
                           sender_id, broadcaster_id)
            return None

        token = await get_app_access_token()
        headers = {
            "Client-ID": settings.CLIENT_ID,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "broadcaster_id": broadcaster_id,
            "sender_id": sender_id,
            "message": _truncate_message(message),
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(CHAT_MESSAGES_URL, headers=headers, json=payload) as resp:
                body = await resp.json()

        if resp.status == 200 and body.get("data") and body["data"][0].get("is_sent"):
            return body["data"][0].get("message_id")

        drop = body.get("data", [{}])[0].get("drop_reason")
        logger.warning("send_message not sent: status=%s drop_reason=%s", resp.status, drop)
        return None
    except Exception as exc:
        logger.warning("send_message exception: %s", exc)
        return None
