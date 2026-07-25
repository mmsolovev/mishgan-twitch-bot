from __future__ import annotations

"""
Ingest layer: fetch datasets from IGDB (upcoming games list, single-game metadata).
"""

import asyncio
import json
import random
import time as _time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from weakref import WeakKeyDictionary

import aiohttp

from services.igdb_service import build_igdb_auth_headers
from pipeline.transform.igdb_transform import (
    build_platforms_text,
    build_rating_text,
    extract_steam_url,
    normalize_cover_url,
    normalize_genres_text,
    parse_release_date,
    pick_best_match,
    truncate_text,
)
from utils.logger import get_logger


IGDB_GAMES_URL = "https://api.igdb.com/v4/games"

# IGDB docs: 4 req/sec and up to 8 open requests. We enforce both locally to avoid 429 spikes.
_IGDB_RATE_LIMIT_RPS = 4
_IGDB_MAX_INFLIGHT = 8

# Cache is intentionally short-ish: it protects IGDB from spam and speeds up repeated lookups.
_META_CACHE_TTL_SECONDS = 60 * 60  # 1 hour
_META_NEGATIVE_CACHE_TTL_SECONDS = 20  # "not found" cache


class _SlidingWindowRateLimiter:
    def __init__(self, *, max_calls: int, period_seconds: float):
        self._max_calls = max(1, int(max_calls))
        self._period = float(period_seconds)
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            async with self._lock:
                now = loop.time()
                while self._calls and (now - self._calls[0]) >= self._period:
                    self._calls.popleft()

                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return

                sleep_for = self._period - (now - self._calls[0])

            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                await asyncio.sleep(0)


@dataclass
class _IGDBCacheEntry:
    value: "RecommendationMetadata | None"
    expires_at: float  # loop.time()


@dataclass
class _IGDBLoopState:
    inflight: asyncio.Semaphore
    rate: _SlidingWindowRateLimiter
    meta_cache: dict[str, _IGDBCacheEntry]
    meta_inflight: dict[str, asyncio.Task["RecommendationMetadata | None"]]


_state_by_loop: "WeakKeyDictionary[asyncio.AbstractEventLoop, _IGDBLoopState]" = WeakKeyDictionary()


def _get_state() -> _IGDBLoopState:
    loop = asyncio.get_running_loop()
    state = _state_by_loop.get(loop)
    if state is not None:
        return state

    state = _IGDBLoopState(
        inflight=asyncio.Semaphore(_IGDB_MAX_INFLIGHT),
        rate=_SlidingWindowRateLimiter(max_calls=_IGDB_RATE_LIMIT_RPS, period_seconds=1.0),
        meta_cache={},
        meta_inflight={},
    )
    _state_by_loop[loop] = state
    return state


@dataclass
class RecommendationMetadata:
    title: str
    description_short: str | None
    release_date: datetime | None
    release_precision: str
    steam_url: str | None
    rating_text: str | None
    platforms_text: str | None
    genres_text: str | None
    cover_url: str | None
    source_name: str
    source_game_id: str
    source_payload: str | None


async def _igdb_query(state: _IGDBLoopState, session: aiohttp.ClientSession, body: str) -> list[dict]:
    await state.rate.acquire()
    async with state.inflight:
        headers = await build_igdb_auth_headers()

        for attempt in range(1, 6):
            try:
                async with session.post(IGDB_GAMES_URL, data=body.encode("utf-8"), headers=headers) as resp:
                    # Handle token expiration / 429 / transient 5xx
                    if resp.status == 401:
                        headers = await build_igdb_auth_headers(force_refresh=True)
                        continue
                    if resp.status == 429 or resp.status >= 500:
                        await asyncio.sleep(min(5.0, 0.25 * (2**attempt)) + random.random() * 0.1)
                        continue

                    data = await resp.json()
            except (asyncio.TimeoutError, aiohttp.ClientError):
                await asyncio.sleep(min(5.0, 0.25 * (2**attempt)) + random.random() * 0.1)
                continue

            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]

            return []

    return []


async def fetch_recommendation_metadata(search_query: str) -> RecommendationMetadata | None:
    logger = get_logger("recommendations.metadata")
    state = _get_state()

    key = " ".join((search_query or "").casefold().split())
    if not key:
        return None

    now = asyncio.get_running_loop().time()
    cached = state.meta_cache.get(key)
    if cached is not None and cached.expires_at > now:
        return cached.value

    inflight = state.meta_inflight.get(key)
    if inflight is not None:
        return await inflight

    timeout = aiohttp.ClientTimeout(total=30)

    async def _do_fetch() -> RecommendationMetadata | None:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            body = f"""
            fields id,name,summary,first_release_date,total_rating,total_rating_count,aggregated_rating,aggregated_rating_count,
                   genres.name,platforms.name,websites.url,cover.url;
            search "{search_query.replace('"', '')}";
            limit 10;
            """
            results = await _igdb_query(state, session, body)
            if not results:
                return None

            best_match = pick_best_match(results, search_query)
            if best_match is None:
                return None

            game_id = best_match.get("id")
            release_date, release_precision = parse_release_date(best_match.get("first_release_date"))
            steam_url = extract_steam_url(best_match.get("websites"))
            cover_url = normalize_cover_url(best_match.get("cover"))

            meta = RecommendationMetadata(
                title=best_match.get("name") or search_query,
                description_short=None,
                release_date=release_date,
                release_precision=release_precision,
                steam_url=steam_url,
                rating_text=build_rating_text(best_match),
                platforms_text=build_platforms_text(best_match.get("platforms")),
                genres_text=normalize_genres_text(best_match.get("genres")),
                cover_url=cover_url or None,
                source_name="igdb",
                source_game_id=str(game_id),
                source_payload=json.dumps(best_match, ensure_ascii=False),
            )
            return meta

    task = asyncio.create_task(_do_fetch())
    state.meta_inflight[key] = task
    try:
        result = await task
    except Exception:
        logger.exception("IGDB fetch failed", extra={"query": search_query})
        result = None
    finally:
        state.meta_inflight.pop(key, None)

    ttl = _META_CACHE_TTL_SECONDS if result is not None else _META_NEGATIVE_CACHE_TTL_SECONDS
    state.meta_cache[key] = _IGDBCacheEntry(value=result, expires_at=asyncio.get_running_loop().time() + ttl)
    return result


async def fetch_top_upcoming_games(limit: int = 15) -> list[RecommendationMetadata]:
    now = int(_time.time())
    month_later = now + 30 * 24 * 60 * 60

    state = _get_state()
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        body = f"""
        fields
            id,name,summary,
            first_release_date,
            hypes,follows,
            genres.name,platforms.name,
            websites.url,cover.url;
        where
            first_release_date != null &
            first_release_date >= {now} &
            first_release_date <= {month_later} &
            platforms.name = ("PC (Microsoft Windows)", "PlayStation 5", "PlayStation 4");
        sort hypes desc;
        limit 50;
        """

        results = await _igdb_query(state, session, body)
        if not results:
            return []

        output: list[RecommendationMetadata] = []

        for game in results:
            release_ts = game.get("first_release_date")
            if not release_ts:
                continue
            if release_ts < now or release_ts > month_later:
                continue

            platforms_text = build_platforms_text(game.get("platforms"))
            if not platforms_text:
                continue

            release_date, release_precision = parse_release_date(release_ts)
            cover_url = normalize_cover_url(game.get("cover"))
            steam_url = extract_steam_url(game.get("websites"))

            output.append(
                RecommendationMetadata(
                    title=game.get("name") or "Unknown",
                    description_short=truncate_text(game.get("summary")),
                    release_date=release_date,
                    release_precision=release_precision,
                    steam_url=steam_url,
                    rating_text=build_rating_text(game),
                    platforms_text=platforms_text,
                    genres_text=normalize_genres_text(game.get("genres")),
                    cover_url=cover_url or None,
                    source_name="igdb",
                    source_game_id=str(game.get("id")),
                    source_payload=json.dumps(game, ensure_ascii=False),
                )
            )

            if len(output) >= limit:
                break

        return output


async def fetch_games_by_ids(game_ids: list[str]) -> list[dict]:
    """
    Fetches game data from IGDB for a given list of game IDs.
    """
    if not game_ids:
        return []

    state = _get_state()
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # IGDB allows up to 500 IDs per request
        body = f"""
            fields id, name, first_release_date;
            where id = ({",".join(game_ids)});
            limit 500;
        """
        return await _igdb_query(state, session, body)


async def fetch_igdb_metadata(game_name: str) -> RecommendationMetadata | None:
    return await fetch_recommendation_metadata(game_name)


async def ingest_top_upcoming_games(limit: int = 15) -> list[RecommendationMetadata]:
    return await fetch_top_upcoming_games(limit=int(limit))


async def ingest_recommendation_metadata(game_name: str) -> RecommendationMetadata | None:
    return await fetch_recommendation_metadata(game_name)


__all__ = [
    "RecommendationMetadata",
    "fetch_games_by_ids",
    "fetch_igdb_metadata",
    "fetch_recommendation_metadata",
    "fetch_top_upcoming_games",
    "ingest_recommendation_metadata",
    "ingest_top_upcoming_games",
]