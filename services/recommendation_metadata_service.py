import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json

import aiohttp

from config.settings import IGDB_CLIENT_ID, IGDB_CLIENT_SECRET
from utils.logger import get_logger


TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
_STEAM_HOST_MARKERS = ("store.steampowered.com", "steamcommunity.com", "steam://")
_PC_MARKERS = {"pc (microsoft windows)", "linux", "mac"}
_PS_MARKERS = {"playstation 5", "playstation 4", "playstation 3", "playstation 2", "playstation"}
_igdb_token: str | None = None
_igdb_token_expires_at: datetime | None = None
_igdb_token_lock = asyncio.Lock()


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


async def _get_igdb_token(session: aiohttp.ClientSession) -> str | None:
    global _igdb_token, _igdb_token_expires_at

    if _igdb_token and _igdb_token_expires_at and datetime.utcnow() < _igdb_token_expires_at:
        return _igdb_token

    async with _igdb_token_lock:
        if _igdb_token and _igdb_token_expires_at and datetime.utcnow() < _igdb_token_expires_at:
            return _igdb_token

        token, expires_at = await _fetch_igdb_token(session)
        if not token or not expires_at:
            return None

        _igdb_token = token
        _igdb_token_expires_at = expires_at
        return _igdb_token


async def _igdb_query(session: aiohttp.ClientSession, body: str) -> list[dict] | None:
    token = await _get_igdb_token(session)
    if not token or not IGDB_CLIENT_ID:
        return None

    headers = {
        "Client-ID": IGDB_CLIENT_ID,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        async with session.post(IGDB_GAMES_URL, headers=headers, data=body.encode("utf-8")) as response:
            if response.status != 200:
                logger = get_logger("recommendations.metadata")
                logger.warning("IGDB query failed: %s", response.status)
                return None
            return await response.json()
    except aiohttp.ClientError as exc:
        logger = get_logger("recommendations.metadata")
        logger.warning("IGDB query error: %s", exc)
        return None


def _pick_best_search_result(query: str, results: list[dict]) -> dict | None:
    normalized_query = " ".join((query or "").casefold().split())
    if not normalized_query:
        return None

    supported_results = [item for item in results if _get_supported_platform_tokens(item.get("platforms"))]
    if not supported_results:
        return None

    exact = []
    partial = []

    for item in supported_results:
        name = " ".join((item.get("name") or "").casefold().split())
        if not name:
            continue
        if name == normalized_query:
            exact.append(item)
        elif normalized_query in name:
            partial.append(item)

    if exact:
        exact.sort(
            key=lambda item: (
                -len(_get_supported_platform_tokens(item.get("platforms"))),
                item.get("first_release_date") or 0,
            ),
            reverse=True,
        )
        return exact[0]
    if partial:
        partial.sort(
            key=lambda item: (
                -len(_get_supported_platform_tokens(item.get("platforms"))),
                item.get("first_release_date") or 0,
            ),
            reverse=True,
        )
        return partial[0]

    supported_results.sort(
        key=lambda item: (
            -len(_get_supported_platform_tokens(item.get("platforms"))),
            item.get("first_release_date") or 0,
        ),
        reverse=True,
    )
    return supported_results[0]


async def fetch_recommendation_metadata(query: str) -> RecommendationMetadata | None:
    search_query = (query or "").strip()
    if not search_query or not IGDB_CLIENT_ID or not IGDB_CLIENT_SECRET:
        return None

    logger = get_logger("recommendations.metadata")

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        body = (
            "fields "
            "id,name,summary,first_release_date,total_rating,total_rating_count,"
            "aggregated_rating,aggregated_rating_count,genres.name,platforms.name,"
            "websites.url,cover.url,category,version_parent;"
            f' search "{search_query.replace(chr(34), "")}";'
            " where version_parent = null & platforms != null;"
            " limit 10;"
        )
        results = await _igdb_query(session, body)
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
            description_short=_truncate_text(best_match.get("summary")),
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
