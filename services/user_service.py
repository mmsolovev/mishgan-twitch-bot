"""
Centralized user service: get-or-create from Chatter or login,
background Twitch API enrichment for new users.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import CLIENT_ID, TWITCH_ACCESS_TOKEN
from database.models import User

logger = logging.getLogger(__name__)

TWITCH_API = "https://api.twitch.tv/helix"


async def get_or_create_user(session: AsyncSession, chatter) -> User:
    """Find or create a User from a twitchio Chatter object.

    Populates all available fields from the Chatter (twitch_user_id, login,
    display_name, is_streamer).  For newly created users a background task
    fetches the full profile (profile_image_url) from the Twitch API.
    """
    login: str = chatter.name
    now = _utcnow()

    result = await session.execute(select(User).where(User.login == login))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            twitch_user_id=chatter.id,
            login=login,
            display_name=chatter.display_name,
            is_streamer=getattr(chatter, "_is_broadcaster", False)
            or getattr(chatter, "_is_verified", False),
            last_seen_at=now,
            created_at=now,
        )
        session.add(user)
        await session.flush()
        asyncio.create_task(_enrich_user_from_twitch(login))
    else:
        user.last_seen_at = now

    return user


async def get_or_create_user_by_login(session: AsyncSession, login: str) -> User:
    """Find or create a User by login name (pipeline / no Chatter context)."""
    result = await session.execute(select(User).where(User.login == login))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(login=login, display_name=f"@{login}")
        session.add(user)
        await session.flush()
    return user


async def _enrich_user_from_twitch(login: str) -> None:
    """Background task: fetch full profile from Twitch API and update DB."""
    await asyncio.sleep(1)

    if not CLIENT_ID or not TWITCH_ACCESS_TOKEN:
        return

    try:
        headers = {
            "Client-ID": CLIENT_ID,
            "Authorization": f"Bearer {TWITCH_ACCESS_TOKEN}",
        }
        async with aiohttp.ClientSession() as http:
            async with http.get(
                f"{TWITCH_API}/users",
                headers=headers,
                params={"login": login},
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        "Twitch API /users returned %s for %s", resp.status, login
                    )
                    return
                data = await resp.json()

        rows = data.get("data") or []
        if not rows:
            return

        row = rows[0]

        from database.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.login == login)
            )
            user = result.scalar_one_or_none()
            if user is None:
                return

            user.profile_image_url = row.get("profile_image_url")
            await session.commit()
    except Exception as exc:
        logger.warning("Twitch API enrichment failed for %s: %s", login, exc)
