"""
One-time script: populate genres and platforms tables from IGDB.

Fetches all genres and platforms from the IGDB API, stores full names
in the DB, and assigns short abbreviations used by the delivery layer.

Usage:
    python database/sync_genres_platforms.py

Requirements:
    - IGDB_CLIENT_ID, IGDB_CLIENT_SECRET configured in .env
    - PostgreSQL running with genres / platforms tables
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from database.db import AsyncSessionLocal
from database.models import Genre, Platform
from services.igdb_service import build_igdb_auth_headers
from sqlalchemy import select


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

IGDB_API = "https://api.igdb.com/v4"
RATE_DELAY = 0.3

# ---------- abbreviation mappings ----------

GENRE_ABBREVIATIONS: dict[str, str] = {
    "Role-playing (RPG)": "RPG",
}

# IGDB platform name → short display abbreviation
# Only platforms that need a shorter label are listed; the rest keep full name.
PLATFORM_ABBREVIATIONS: dict[str, str] = {
    # PlayStation
    "PlayStation":        "PS1",
    "PlayStation 2":      "PS2",
    "PlayStation 3":      "PS3",
    "PlayStation 4":      "PS4",
    "PlayStation 5":      "PS5",
    "PlayStation Portable": "PSP",
    "PlayStation Vita":   "PSVita",
    # Xbox
    "Xbox":               "Xbox",
    "Xbox 360":           "Xbox 360",
    "Xbox One":           "Xbox One",
    "Xbox Series X|S":    "Xbox Series",
    # Nintendo
    "Nintendo Switch":    "Switch",
    "Nintendo 3DS":       "3DS",
    "Nintendo DS":        "DS",
    "Nintendo Wii U":     "Wii U",
    "Nintendo Wii":       "Wii",
    # PC
    "PC (Microsoft Windows)": "PC",
    "Linux":              "Linux",
    "macOS":              "macOS",
    # Mobile
    "Android":            "Mobile",
    "iOS":                "Mobile",
}


async def _igdb_post(http: aiohttp.ClientSession, endpoint: str, body: str) -> list[dict]:
    headers = await build_igdb_auth_headers()
    await asyncio.sleep(RATE_DELAY)
    async with http.post(f"{IGDB_API}/{endpoint}", data=body.encode("utf-8"), headers=headers) as resp:
        if resp.status == 401:
            headers = await build_igdb_auth_headers(force_refresh=True)
            await asyncio.sleep(RATE_DELAY)
            async with http.post(f"{IGDB_API}/{endpoint}", data=body.encode("utf-8"), headers=headers) as retry:
                if retry.status != 200:
                    log.warning("IGDB %s returned %s on retry", endpoint, retry.status)
                    return []
                return await retry.json()
        if resp.status != 200:
            log.warning("IGDB %s returned %s", endpoint, resp.status)
            return []
        return await resp.json()


async def fetch_genres(http: aiohttp.ClientSession) -> list[dict]:
    return await _igdb_post(http, "genres", "fields id,name,slug; limit 500;")


async def fetch_platforms(http: aiohttp.ClientSession) -> list[dict]:
    return await _igdb_post(http, "platforms", "fields id,name,slug; limit 500;")


async def upsert_genres(items: list[dict]) -> int:
    inserted = 0
    async with AsyncSessionLocal() as session:
        for item in items:
            name = (item.get("name") or "").strip()
            slug = (item.get("slug") or "").strip() or None
            igdb_id = item.get("id")
            if not name:
                continue

            result = await session.execute(select(Genre).where(Genre.name == name))
            genre = result.scalar_one_or_none()

            if genre is None:
                genre = Genre(
                    name=name,
                    slug=slug,
                    abbreviation=GENRE_ABBREVIATIONS.get(name),
                    created_at=_utcnow(),
                )
                session.add(genre)
                inserted += 1
            else:
                if genre.created_at is None:
                    genre.created_at = _utcnow()
                if genre.abbreviation is None and name in GENRE_ABBREVIATIONS:
                    genre.abbreviation = GENRE_ABBREVIATIONS[name]
                if genre.slug is None and slug:
                    genre.slug = slug

        await session.commit()
    return inserted


async def upsert_platforms(items: list[dict]) -> int:
    inserted = 0
    async with AsyncSessionLocal() as session:
        for item in items:
            name = (item.get("name") or "").strip()
            slug = (item.get("slug") or "").strip() or None
            if not name:
                continue

            result = await session.execute(select(Platform).where(Platform.name == name))
            platform = result.scalar_one_or_none()

            if platform is None:
                if slug:
                    slug_check = await session.execute(
                        select(Platform.id).where(Platform.slug == slug).limit(1)
                    )
                    if slug_check.first():
                        slug = None

                platform = Platform(
                    name=name,
                    slug=slug,
                    abbreviation=PLATFORM_ABBREVIATIONS.get(name),
                    created_at=_utcnow(),
                )
                session.add(platform)
                inserted += 1
            else:
                if platform.created_at is None:
                    platform.created_at = _utcnow()
                if platform.abbreviation is None and name in PLATFORM_ABBREVIATIONS:
                    platform.abbreviation = PLATFORM_ABBREVIATIONS[name]
                if platform.slug is None and slug:
                    slug_check = await session.execute(
                        select(Platform.id).where(Platform.slug == slug, Platform.id != platform.id).limit(1)
                    )
                    if not slug_check.first():
                        platform.slug = slug

        await session.commit()
    return inserted


async def sync_genres_platforms() -> None:
    async with aiohttp.ClientSession() as http:
        genres = await fetch_genres(http)
        log.info("Fetched %d genres from IGDB", len(genres))

        platforms = await fetch_platforms(http)
        log.info("Fetched %d platforms from IGDB", len(platforms))

    genres_inserted = await upsert_genres(genres)
    log.info("Genres: %d new, %d total", genres_inserted, len(genres))

    platforms_inserted = await upsert_platforms(platforms)
    log.info("Platforms: %d new, %d total", platforms_inserted, len(platforms))

    log.info("Done.")


if __name__ == "__main__":
    asyncio.run(sync_genres_platforms())
