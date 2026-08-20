"""
One-time script: enrich existing users with data from the Twitch API.

Fills twitch_user_id, profile_image_url, display_name, twitch_url, and is_streamer
for users that currently only have login + display_name.

Usage:
    python database/enrich_users.py

Requirements:
    - PostgreSQL running with users table populated
    - CLIENT_ID and TWITCH_ACCESS_TOKEN configured in .env
"""

import asyncio
import logging

import aiohttp
from sqlalchemy import select, or_

from database.db import AsyncSessionLocal
from database.models import User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TWITCH_API = "https://api.twitch.tv/helix"
BATCH_SIZE = 100  # Twitch /users accepts up to 100 logins per request


async def enrich_users():
    from config.settings import CLIENT_ID, TWITCH_ACCESS_TOKEN

    if not CLIENT_ID or not TWITCH_ACCESS_TOKEN:
        log.error("CLIENT_ID or TWITCH_ACCESS_TOKEN not set in .env")
        return

    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {TWITCH_ACCESS_TOKEN}",
    }

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(
                or_(
                    User.twitch_user_id.is_(None),
                    User.profile_image_url.is_(None),
                    User.twitch_url.is_(None),
                )
            )
        )
        users = list(result.scalars().all())

    if not users:
        log.info("No users need enrichment")
        return

    log.info("Found %d users to enrich", len(users))

    enriched = 0
    not_found = 0

    async with aiohttp.ClientSession() as http:
        for i in range(0, len(users), BATCH_SIZE):
            batch = users[i : i + BATCH_SIZE]
            logins = [u.login for u in batch]

            async with http.get(
                f"{TWITCH_API}/users",
                headers=headers,
                params=[("login", login) for login in logins],
            ) as resp:
                if resp.status != 200:
                    log.warning("Twitch API returned %s, skipping batch", resp.status)
                    continue
                data = await resp.json()

            api_users = {row["login"].lower(): row for row in data.get("data") or []}

            async with AsyncSessionLocal() as session:
                for user in batch:
                    row = api_users.get(user.login.lower())
                    if row is None:
                        not_found += 1
                        log.warning("User %s not found on Twitch (deleted?)", user.login)
                        continue

                    user.twitch_user_id = str(row["id"])
                    user.display_name = row["display_name"]
                    user.profile_image_url = row.get("profile_image_url")
                    user.twitch_url = f"https://www.twitch.tv/{user.login}"
                    user.is_streamer = row.get("broadcaster_type") in ("partner", "affiliate")

                    session.add(user)
                    enriched += 1

                await session.commit()

            log.info("Processed batch %d–%d", i + 1, min(i + BATCH_SIZE, len(users)))

    log.info("Done: enriched=%d, not_found=%d", enriched, not_found)


if __name__ == "__main__":
    asyncio.run(enrich_users())
