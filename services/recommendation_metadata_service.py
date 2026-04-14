import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import random
from weakref import WeakKeyDictionary

import aiohttp

from config.settings import IGDB_CLIENT_ID, IGDB_CLIENT_SECRET
from utils.logger import get_logger


TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
_STEAM_HOST_MARKERS = ("store.steampowered.com", "steamcommunity.com", "steam://")
_PC_MARKERS = {"pc (microsoft windows)", "linux", "mac"}
_PS_MARKERS = {"playstation 5", "playstation 4", "playstation 3", "playstation 2", "playstation"}

# IGDB docs: 4 req/sec and up to 8 open requests. We enforce both locally to avoid 429 spikes.
_IGDB_RATE_LIMIT_RPS = 4
_IGDB_MAX_INFLIGHT = 8

# Cache is intentionally short-ish: it protects IGDB from spam and speeds up repeated !рек.
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
                # Defensive: avoid a busy loop if clock math gets weird.
                await asyncio.sleep(0)


@dataclass
class _IGDBCacheEntry:
    value: "RecommendationMetadata | None"
    expires_at: float  # loop.time()


@dataclass
class _IGDBLoopState:
    token: str | None
    token_expires_at: datetime | None
    token_lock: asyncio.Lock
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
        token=None,
        token_expires_at=None,
        token_lock=asyncio.Lock(),
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


def _parse_release_date(value: int | str | None) -> tuple[datetime | None, str]:
    if value is None:
        return None, "unknown"

    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None, "unknown"

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None), "day"


def _truncate_text(value: str | None, max_length: int = 280) -> str | None:
    if not value:
        return None

    compact = " ".join(value.split())
    if len(compact) <= max_length:
        return compact

    return compact[: max_length - 3].rstrip() + "..."


def _build_rating_text(payload: dict) -> str | None:
    total_rating = payload.get("total_rating")
    total_rating_count = payload.get("total_rating_count")
    aggregated_rating = payload.get("aggregated_rating")
    aggregated_rating_count = payload.get("aggregated_rating_count")

    parts = []
    if total_rating:
        parts.append(f"IGDB {total_rating:.0f}/100")
    if total_rating_count:
        parts.append(f"оценок {int(total_rating_count)}")
    if aggregated_rating:
        parts.append(f"critic {aggregated_rating:.0f}/100")
    if aggregated_rating_count:
        parts.append(f"critic votes {int(aggregated_rating_count)}")

    return " | ".join(parts) if parts else None


def _extract_nested_value(item: dict, key: str) -> str:
    current = item
    for part in key.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
    return current.strip() if isinstance(current, str) else ""


def _join_names(items: list[dict] | None, key: str = "name") -> str | None:
    if not items:
        return None

    values = [_extract_nested_value(item, key) for item in items]
    values = [value for value in values if value]
    return ", ".join(values) if values else None


def _build_platforms_text(items: list[dict] | None) -> str | None:
    if not items:
        return None

    normalized = {(_extract_nested_value(item, "name") or "").casefold() for item in items}
    result = []
    if normalized & _PC_MARKERS:
        result.append("PC")
    if normalized & _PS_MARKERS:
        result.append("PS")
    return ", ".join(result) if result else None


def _get_supported_platform_tokens(items: list[dict] | None) -> set[str]:
    if not items:
        return set()

    normalized = {(_extract_nested_value(item, "name") or "").casefold() for item in items}
    result = set()
    if normalized & _PC_MARKERS:
        result.add("PC")
    if normalized & _PS_MARKERS:
        result.add("PS")
    return result


async def _fetch_igdb_token(session: aiohttp.ClientSession) -> tuple[str | None, datetime | None]:
    if not IGDB_CLIENT_ID or not IGDB_CLIENT_SECRET:
        return None, None

    try:
        async with session.post(
            TWITCH_TOKEN_URL,
            params={
                "client_id": IGDB_CLIENT_ID,
                "client_secret": IGDB_CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
        ) as response:
            if response.status != 200:
                logger = get_logger("recommendations.metadata")
                logger.warning("IGDB token request failed: %s", response.status)
                return None, None

            payload = await response.json()
            access_token = payload.get("access_token")
            expires_in = int(payload.get("expires_in") or 0)
            expires_at = datetime.utcnow() + timedelta(seconds=max(0, expires_in - 60))
            return access_token, expires_at
    except aiohttp.ClientError as exc:
        logger = get_logger("recommendations.metadata")
        logger.warning("IGDB token request error: %s", exc)
        return None, None


async def _get_igdb_token(
    state: _IGDBLoopState,
    session: aiohttp.ClientSession,
    *,
    force_refresh: bool = False,
) -> str | None:
    if not force_refresh and state.token and state.token_expires_at and datetime.utcnow() < state.token_expires_at:
        return state.token

    async with state.token_lock:
        if not force_refresh and state.token and state.token_expires_at and datetime.utcnow() < state.token_expires_at:
            return state.token

        token, expires_at = await _fetch_igdb_token(session)
        if not token or not expires_at:
            return None

        state.token = token
        state.token_expires_at = expires_at
        return state.token


async def _igdb_query(state: _IGDBLoopState, session: aiohttp.ClientSession, body: str) -> list[dict] | None:
    logger = get_logger("recommendations.metadata")

    # Retry strategy:
    # - 401: refresh token once
    # - 429 / 5xx / transient network: exponential backoff with jitter
    max_attempts = 5
    refreshed_token = False

    for attempt in range(1, max_attempts + 1):
        token = await _get_igdb_token(state, session, force_refresh=refreshed_token)
        if not token or not IGDB_CLIENT_ID:
            return None

        headers = {
            "Client-ID": IGDB_CLIENT_ID,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        await state.rate.acquire()
        try:
            async with state.inflight:
                async with session.post(IGDB_GAMES_URL, headers=headers, data=body.encode("utf-8")) as response:
                    if response.status == 200:
                        return await response.json()

                    if response.status == 401 and not refreshed_token:
                        # Token might be expired/invalidated: refresh and retry once.
                        refreshed_token = True
                        continue

                    if response.status in {429, 500, 502, 503, 504}:
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            try:
                                delay = max(0.0, float(retry_after))
                            except ValueError:
                                delay = 0.0
                        else:
                            delay = 0.25 * (2 ** (attempt - 1))
                        delay = min(4.0, delay)
                        delay = delay + random.uniform(0, 0.25)

                        logger.warning("IGDB query failed (%s), retry in %.2fs (attempt %s/%s)",
                                       response.status, delay, attempt, max_attempts)
                        await asyncio.sleep(delay)
                        continue

                    logger.warning("IGDB query failed: %s", response.status)
                    return None
        except aiohttp.ClientError as exc:
            delay = 0.25 * (2 ** (attempt - 1))
            delay = min(4.0, delay) + random.uniform(0, 0.25)
            logger.warning("IGDB query error: %s, retry in %.2fs (attempt %s/%s)", exc, delay, attempt, max_attempts)
            await asyncio.sleep(delay)

    return None


def _cache_key(query: str) -> str:
    return " ".join((query or "").casefold().split())


def _pick_best_search_result(query: str, results: list[dict]) -> dict | None:
    import time

    normalized_query = " ".join((query or "").casefold().split())
    if not normalized_query:
        return None

    now = int(time.time())

    def is_valid(item):
        return item.get("category") not in {1, 2, 3}  # DLC, expansion, bundle

    candidates = [item for item in results if is_valid(item)]

    if not candidates:
        candidates = results  # fallback

    # мягкий фильтр платформ
    supported = [
        item for item in candidates
        if _get_supported_platform_tokens(item.get("platforms"))
    ]
    if supported:
        candidates = supported

    def score(item):
        name = " ".join((item.get("name") or "").casefold().split())
        if not name:
            return -999

        score = 0

        if name == normalized_query:
            score += 200

        if all(word in name for word in normalized_query.split()):
            score += 80

        release = item.get("first_release_date") or 0
        if release > now:
            score += 50

        score += (item.get("total_rating_count") or 0) * 0.01

        if "edition" in name:
            score -= 30
        if "bundle" in name:
            score -= 40

        return score

    return max(candidates, key=score)


async def fetch_recommendation_metadata(query: str) -> RecommendationMetadata | None:
    search_query = (query or "").strip()
    if not search_query or not IGDB_CLIENT_ID or not IGDB_CLIENT_SECRET:
        return None

    logger = get_logger("recommendations.metadata")
    state = _get_state()

    key = _cache_key(search_query)
    now = asyncio.get_running_loop().time()
    cached = state.meta_cache.get(key)
    if cached is not None and now < cached.expires_at:
        return cached.value

    inflight = state.meta_inflight.get(key)
    if inflight is not None:
        return await inflight

    async def _do_fetch() -> RecommendationMetadata | None:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            body = (
                "fields "
                "id,name,slug,summary,first_release_date,"
                "total_rating,total_rating_count,"
                "aggregated_rating,aggregated_rating_count,"
                "genres.name,platforms.name,"
                "websites.url,cover.url,"
                "category,version_parent,parent_game;"
                f' search "{search_query.replace(chr(34), "")}";'
                " limit 20;"
            )
            results = await _igdb_query(state, session, body)
            if not results:
                return None

            best_match = _pick_best_search_result(search_query, results)
            if not best_match:
                return None

            game_id = best_match.get("id")
            if not game_id:
                logger.warning("IGDB result missing id for query '%s'", search_query)
                return None

            release_date, release_precision = _parse_release_date(best_match.get("first_release_date"))
            websites = best_match.get("websites") or []
            steam_url = None
            for website in websites:
                url = (website.get("url") or "").strip()
                if any(marker in url.casefold() for marker in _STEAM_HOST_MARKERS):
                    steam_url = url
                    break

            cover = best_match.get("cover") or {}
            cover_url = (cover.get("url") or "").strip()
            if cover_url.startswith("//"):
                cover_url = f"https:{cover_url}"
            elif cover_url.startswith("/"):
                cover_url = f"https://images.igdb.com{cover_url}"

            return RecommendationMetadata(
                title=best_match.get("name") or search_query,
                description_short=None,
                release_date=release_date,
                release_precision=release_precision,
                steam_url=steam_url,
                rating_text=_build_rating_text(best_match),
                platforms_text=_build_platforms_text(best_match.get("platforms")),
                genres_text=_join_names(best_match.get("genres")),
                cover_url=cover_url or None,
                source_name="igdb",
                source_game_id=str(game_id),
                source_payload=json.dumps(best_match, ensure_ascii=False),
            )

    task = asyncio.create_task(_do_fetch())
    state.meta_inflight[key] = task
    try:
        result = await task
    finally:
        state.meta_inflight.pop(key, None)

    ttl = _META_CACHE_TTL_SECONDS if result is not None else _META_NEGATIVE_CACHE_TTL_SECONDS
    now = asyncio.get_running_loop().time()
    state.meta_cache[key] = _IGDBCacheEntry(value=result, expires_at=now + ttl)
    return result


async def fetch_top_upcoming_games(limit: int = 15) -> list[RecommendationMetadata]:
    import time

    now = int(time.time())
    month_later = now + 30 * 24 * 60 * 60

    logger = get_logger("recommendations.metadata")
    state = _get_state()

    timeout = aiohttp.ClientTimeout(total=15)

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
            first_release_date <= {month_later};

        sort hypes desc;
        limit 50;
        """

        results = await _igdb_query(state, session, body)

        if not results:
            return []

        output: list[RecommendationMetadata] = []

        for game in results:
            # ---- фильтрация в Python ----
            release_ts = game.get("first_release_date")
            hypes = game.get("hypes") or 0

            if not release_ts:
                continue

            if release_ts < now or release_ts > month_later:
                continue



            # ---- release date ----
            release_date, release_precision = _parse_release_date(release_ts)

            # ---- cover ----
            cover = game.get("cover") or {}
            cover_url = (cover.get("url") or "").strip()

            if cover_url.startswith("//"):
                cover_url = f"https:{cover_url}"
            elif cover_url.startswith("/"):
                cover_url = f"https://images.igdb.com{cover_url}"

            # ---- steam (если есть) ----
            steam_url = None
            for website in game.get("websites") or []:
                url = (website.get("url") or "").strip()
                if any(marker in url.casefold() for marker in _STEAM_HOST_MARKERS):
                    steam_url = url
                    break

            output.append(
                RecommendationMetadata(
                    title=game.get("name") or "Unknown",
                    description_short=_truncate_text(game.get("summary")),
                    release_date=release_date,
                    release_precision=release_precision,
                    steam_url=steam_url,
                    rating_text=_build_rating_text(game),
                    platforms_text=_build_platforms_text(game.get("platforms")),
                    genres_text=_join_names(game.get("genres")),
                    cover_url=cover_url or None,
                    source_name="igdb",
                    source_game_id=str(game.get("id")),
                    source_payload=json.dumps(game, ensure_ascii=False),
                )
            )

            if len(output) >= limit:
                break

        # сортировка: самые ожидаемые вверх
        output.sort(
            key=lambda x: (
                x.release_date is None,
                -(game.get("hypes") or 0)
            )
        )

        return output
