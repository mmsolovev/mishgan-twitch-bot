"""
Populate game_metadata_igdb for games that lack IGDB metadata.

For each game in the DB without a game_metadata_igdb row, searches IGDB
by name, fetches full metadata, and writes it along with genre/platform
links.  Safe to re-run: only processes games that still have no IGDB row.

Usage:
    python database/sync_game_metadata.py

Requirements:
    - IGDB_CLIENT_ID, IGDB_CLIENT_SECRET in .env
    - PostgreSQL with games, genres, platforms tables populated
"""

import asyncio
import logging
import random
from collections import deque
from datetime import datetime, timezone
from weakref import WeakKeyDictionary

import aiohttp
from sqlalchemy import select

from database.db import AsyncSessionLocal
from database.models import (
    Game,
    GameMetadataIGDB,
    Genre,
    Platform,
    game_genres,
    game_platforms,
)
from services.igdb_service import build_igdb_auth_headers
from pipeline.transform.igdb_transform import (
    extract_steam_url,
    normalize_cover_url,
    parse_release_date,
    pick_best_match,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
_RATE_LIMIT_RPS = 4
_MAX_INFLIGHT = 8


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- rate limiter (same sliding-window as igdb_api) ----------


class _RateLimiter:
    def __init__(self, *, max_calls: int, period: float):
        self._max = max(1, int(max_calls))
        self._period = float(period)
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            async with self._lock:
                now = loop.time()
                while self._calls and (now - self._calls[0]) >= self._period:
                    self._calls.popleft()
                if len(self._calls) < self._max:
                    self._calls.append(now)
                    return
                sleep_for = self._period - (now - self._calls[0])
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                await asyncio.sleep(0)


class _LoopState:
    def __init__(self) -> None:
        self.rate = _RateLimiter(max_calls=_RATE_LIMIT_RPS, period=1.0)
        self.inflight = asyncio.Semaphore(_MAX_INFLIGHT)
        self.cache: dict[str, dict | None] = {}
        self.cache_ttl = 3600


_state_by_loop: "WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopState]" = WeakKeyDictionary()


def _get_state() -> _LoopState:
    loop = asyncio.get_running_loop()
    state = _state_by_loop.get(loop)
    if state is not None:
        return state
    state = _LoopState()
    _state_by_loop[loop] = state
    return state


async def _igdb_search(
    state: _LoopState,
    http: aiohttp.ClientSession,
    game_name: str,
) -> dict | None:
    """Search IGDB by game name, return best-match dict or None."""
    key = " ".join(game_name.casefold().split())
    now = asyncio.get_running_loop().time()
    cached = state.cache.get(key)
    if key in state.cache:
        return cached

    safe_name = game_name.replace('"', "")
    body = (
        "fields id,name,summary,first_release_date,"
        "total_rating,total_rating_count,"
        "aggregated_rating,aggregated_rating_count,"
        "genres.name,genres.id,"
        "platforms.name,platforms.id,"
        "websites.url,cover.url;\n"
        f'search "{safe_name}";\n'
        "limit 5;"
    )

    await state.rate.acquire()
    async with state.inflight:
        headers = await build_igdb_auth_headers()
        for attempt in range(1, 6):
            try:
                async with http.post(
                    IGDB_GAMES_URL, data=body.encode("utf-8"), headers=headers
                ) as resp:
                    if resp.status == 401:
                        headers = await build_igdb_auth_headers(force_refresh=True)
                        continue
                    if resp.status == 429 or resp.status >= 500:
                        wait = min(5.0, 0.25 * (2**attempt)) + random.random() * 0.1
                        await asyncio.sleep(wait)
                        continue
                    data = await resp.json()
                    if isinstance(data, list):
                        best = pick_best_match(
                            [d for d in data if isinstance(d, dict)], game_name
                        )
                        state.cache[key] = best
                        return best
                    state.cache[key] = None
                    return None
            except (asyncio.TimeoutError, aiohttp.ClientError):
                await asyncio.sleep(min(5.0, 0.25 * (2**attempt)) + random.random() * 0.1)
                continue

    state.cache[key] = None
    return None


# ---------- DB helpers ----------


def _score_from_igdb(payload: dict) -> float | None:
    total = payload.get("total_rating")
    if total is not None:
        return round(float(total), 1)
    return None


async def _find_genre_id(session, name: str) -> int | None:
    row = (await session.execute(
        select(Genre.id).where(Genre.name == name).limit(1)
    )).first()
    return row[0] if row else None


async def _find_platform_id(session, name: str) -> int | None:
    row = (await session.execute(
        select(Platform.id).where(Platform.name == name).limit(1)
    )).first()
    return row[0] if row else None


async def _link_genres(session, game_id: int, genres: list[dict]) -> None:
    for g in genres:
        name = (g.get("name") or "").strip()
        if not name:
            continue
        genre_id = await _find_genre_id(session, name)
        if genre_id is None:
            continue
        exists = (await session.execute(
            select(game_genres.c.game_id)
            .where(game_genres.c.game_id == game_id, game_genres.c.genre_id == genre_id)
            .limit(1)
        )).first()
        if not exists:
            await session.execute(
                game_genres.insert().values(game_id=game_id, genre_id=genre_id)
            )


async def _link_platforms(session, game_id: int, platforms: list[dict]) -> None:
    for p in platforms:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        platform_id = await _find_platform_id(session, name)
        if platform_id is None:
            continue
        exists = (await session.execute(
            select(game_platforms.c.game_id)
            .where(
                game_platforms.c.game_id == game_id,
                game_platforms.c.platform_id == platform_id,
            )
            .limit(1)
        )).first()
        if not exists:
            await session.execute(
                game_platforms.insert().values(
                    game_id=game_id, platform_id=platform_id
                )
            )


# ---------- main ----------


async def sync_game_metadata() -> None:
    state = _get_state()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Game))
        games = list(result.scalars().all())

    if not games:
        log.info("No games in DB")
        return

    log.info("Processing %d games", len(games))

    timeout = aiohttp.ClientTimeout(total=30)
    processed = 0
    skipped = 0
    updated = 0
    inserted = 0

    async with aiohttp.ClientSession(timeout=timeout) as http:
        for game in games:
            payload = await _igdb_search(state, http, game.name)
            if payload is None:
                log.warning("No IGDB match for %r", game.name)
                skipped += 1
                continue

            igdb_id = str(payload.get("id") or "")
            igdb_name = payload.get("name")
            summary = payload.get("summary")
            release_date, _ = parse_release_date(payload.get("first_release_date"))
            steam_url = extract_steam_url(payload.get("websites"))
            cover_url = normalize_cover_url(payload.get("cover"))
            igdb_score = _score_from_igdb(payload)

            async with AsyncSessionLocal() as session:
                with session.no_autoflush:
                    # Check if target igdb_id is already taken by another row
                    conflict = await session.execute(
                        select(GameMetadataIGDB.game_id)
                        .where(GameMetadataIGDB.igdb_id == igdb_id, GameMetadataIGDB.game_id != game.id)
                        .limit(1)
                    )
                    id_conflict = conflict.first() is not None

                    result = await session.execute(
                        select(GameMetadataIGDB).where(
                            GameMetadataIGDB.game_id == game.id
                        ).limit(1)
                    )
                    existing = result.scalar_one_or_none()

                    if existing is not None:
                        if id_conflict:
                            log.warning("igdb_id %r belongs to another game, skipping %r", igdb_id, game.name)
                            skipped += 1
                            continue
                        existing.igdb_id = igdb_id
                        existing.igdb_name = igdb_name
                        existing.description_en = summary
                        existing.release_date = release_date
                        existing.steam_url = steam_url
                        existing.cover_url = cover_url or None
                        existing.igdb_score = igdb_score
                        existing.raw_payload = payload
                        existing.synced_at = _utcnow()
                        updated += 1
                    else:
                        if id_conflict:
                            log.warning("igdb_id %r already exists in DB, skipping %r", igdb_id, game.name)
                            skipped += 1
                            continue
                        meta = GameMetadataIGDB(
                            game_id=game.id,
                            igdb_id=igdb_id,
                            igdb_name=igdb_name,
                            description_en=summary,
                            release_date=release_date,
                            steam_url=steam_url,
                            cover_url=cover_url or None,
                            igdb_score=igdb_score,
                            raw_payload=payload,
                            synced_at=_utcnow(),
                        )
                        session.add(meta)
                        inserted += 1
                    # Replace genre/platform links with fresh data from IGDB
                    await session.execute(
                        game_genres.delete().where(game_genres.c.game_id == game.id)
                    )
                    await session.execute(
                        game_platforms.delete().where(game_platforms.c.game_id == game.id)
                    )
                    await _link_genres(session, game.id, payload.get("genres") or [])
                    await _link_platforms(session, game.id, payload.get("platforms") or [])
                    await session.commit()

            processed += 1
            if processed % 25 == 0:
                log.info("Progress: %d/%d processed", processed, len(games))

    log.info("Done: inserted=%d, updated=%d, skipped=%d", inserted, updated, skipped)


if __name__ == "__main__":
    asyncio.run(sync_game_metadata())
