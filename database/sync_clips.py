"""
One-time script: fetch ALL clips from the Tabula Twitch channel
and populate the clips table.

Iterates over streams in the DB and queries Twitch /clips with
started_at / ended_at date filters so no clip is missed.

Creates clip creators as users if missing, links clips to streams,
and resolves game IDs via Twitch API.

Automatically refreshes the access token on 401 errors.

Usage:
    python database/sync_clips.py

Requirements:
    - PostgreSQL running with streams, games, users tables populated
    - CLIENT_ID, TWITCH_ACCESS_TOKEN, TWITCH_REFRESH_TOKEN configured in .env
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from sqlalchemy import select

from database.db import AsyncSessionLocal
from database.models import Clip, Game, GameAlias, Stream, User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TWITCH_API = "https://api.twitch.tv/helix"
BROADCASTER_LOGIN = "tabula"
CLIPS_PAGE_SIZE = 100
GAMES_PAGE_SIZE = 100
RATE_DELAY = 0.7


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_dt(raw: str) -> datetime:
    raw = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    return dt.replace(tzinfo=None)


def _to_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_headers(client_id: str, access_token: str) -> dict:
    return {
        "Client-ID": client_id,
        "Authorization": f"Bearer {access_token}",
    }


async def _refresh_token() -> str | None:
    from services.token_service import try_refresh_token
    return await try_refresh_token()


async def _api_get(http, url, headers, params, *, client_id: str):
    """GET with rate-limit handling and automatic token refresh on 401."""
    await asyncio.sleep(RATE_DELAY)
    async with http.get(url, headers=headers, params=params) as resp:
        if resp.status == 429:
            retry_after = int(resp.headers.get("Retry-After", "10"))
            log.warning("Rate limited, sleeping %ds", retry_after)
            await asyncio.sleep(retry_after)
            return await _api_get(http, url, headers, params, client_id=client_id)

        if resp.status == 401:
            log.warning("Token expired (401), refreshing…")
            new_token = await _refresh_token()
            if new_token:
                headers = _build_headers(client_id, new_token)
                log.info("Token refreshed, retrying request")
                return await _api_get(http, url, headers, params, client_id=client_id)
            log.error("Token refresh failed, skipping request")
            return {}

        if resp.status != 200:
            log.warning("API %s returned %s", url, resp.status)
            return {}
        return await resp.json()


async def _resolve_broadcaster_id(http, headers, *, client_id: str) -> str:
    data = await _api_get(
        http, f"{TWITCH_API}/users", headers, {"login": BROADCASTER_LOGIN},
        client_id=client_id,
    )
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError(f"Broadcaster {BROADCASTER_LOGIN!r} not found on Twitch")
    return str(rows[0]["id"])


async def _fetch_clips_for_window(
    http, headers, broadcaster_id: str, started_at: datetime, ended_at: datetime,
    *, client_id: str,
) -> list[dict]:
    clips: list[dict] = []
    cursor: str | None = None

    while True:
        params: dict = {
            "broadcaster_id": broadcaster_id,
            "started_at": _to_iso(started_at),
            "ended_at": _to_iso(ended_at),
            "first": CLIPS_PAGE_SIZE,
        }
        if cursor:
            params["after"] = cursor

        data = await _api_get(
            http, f"{TWITCH_API}/clips", headers, params, client_id=client_id,
        )
        batch = data.get("data") or []
        clips.extend(batch)

        pagination = data.get("pagination") or {}
        cursor = pagination.get("cursor")
        if not cursor or not batch:
            break

    return clips


async def _fetch_all_clips_by_streams(
    http, headers, broadcaster_id: str, streams: list[Stream],
    *, client_id: str,
) -> list[dict]:
    seen: set[str] = set()
    all_clips: list[dict] = []
    now = _utcnow()

    for idx, stream in enumerate(streams):
        started = stream.started_at
        if idx + 1 < len(streams):
            ended = streams[idx + 1].started_at
        else:
            ended = stream.ended_at or now

        window_clips = await _fetch_clips_for_window(
            http, headers, broadcaster_id, started, ended, client_id=client_id,
        )

        new_count = 0
        for clip in window_clips:
            cid = clip["id"]
            if cid not in seen:
                seen.add(cid)
                all_clips.append(clip)
                new_count += 1

        log.info(
            "Stream %d/%d (%s): %d clips (%d new)",
            idx + 1,
            len(streams),
            started.strftime("%Y-%m-%d"),
            len(window_clips),
            new_count,
        )

    return all_clips


async def _fetch_games(http, headers, twitch_game_ids: set[str], *, client_id: str) -> dict[str, str]:
    id_to_name: dict[str, str] = {}
    ids = [gid for gid in twitch_game_ids if gid and gid != "0"]

    for i in range(0, len(ids), GAMES_PAGE_SIZE):
        batch = ids[i : i + GAMES_PAGE_SIZE]
        params = [("id", gid) for gid in batch]
        data = await _api_get(
            http, f"{TWITCH_API}/games", headers, params, client_id=client_id,
        )
        for row in data.get("data") or []:
            id_to_name[str(row["id"])] = row["name"]

    return id_to_name


async def _get_or_create_user(session, *, twitch_user_id: str, login: str, display_name: str) -> User:
    result = await session.execute(select(User).where(User.login == login))
    user = result.scalar_one_or_none()
    if user is not None:
        if user.twitch_user_id is None:
            user.twitch_user_id = twitch_user_id
        if user.display_name is None:
            user.display_name = display_name
        return user

    user = User(
        twitch_user_id=twitch_user_id,
        login=login,
        display_name=display_name,
        twitch_url=f"https://www.twitch.tv/{login}",
    )
    session.add(user)
    await session.flush()
    return user


async def _get_or_create_game(session, twitch_game_id: str, game_name: str) -> int | None:
    if not game_name:
        return None

    result = await session.execute(select(Game).where(Game.name == game_name))
    game = result.scalar_one_or_none()
    if game is not None:
        return game.id

    normalized = game_name.lower().strip()
    game = Game(name=game_name, slug=normalized.replace(" ", "-"))
    session.add(game)
    await session.flush()

    session.add(
        GameAlias(
            game_id=game.id,
            alias=game_name,
            normalized_alias=normalized,
            is_primary=True,
            source="twitch_api",
        )
    )
    await session.flush()
    return game.id


async def _find_stream_for_clip(session, clip_dt: datetime) -> int | None:
    result = await session.execute(
        select(Stream.id)
        .where(Stream.started_at <= clip_dt)
        .where(
            (Stream.ended_at >= clip_dt) | (Stream.ended_at.is_(None))
        )
        .order_by(Stream.started_at.desc())
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def _backfill_game_ids(http, headers, *, client_id: str) -> int:
    """For existing clips with game_id IS NULL, fetch game from Twitch API by clip external_id."""
    updated = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Clip).where(Clip.game_id.is_(None))
        )
        clips_to_fix = list(result.scalars().all())

    if not clips_to_fix:
        log.info("No clips with missing game_id")
        return 0

    log.info("Backfilling game_id for %d clips…", len(clips_to_fix))

    # Twitch /clips?id=X returns clip with game_id
    for i in range(0, len(clips_to_fix), CLIPS_PAGE_SIZE):
        batch = clips_to_fix[i : i + CLIPS_PAGE_SIZE]
        params = [("id", c.external_id) for c in batch]

        data = await _api_get(
            http, f"{TWITCH_API}/clips", headers, params, client_id=client_id,
        )
        api_clips = {c["id"]: c for c in (data.get("data") or [])}

        # collect game_ids for batch
        twitch_gids = {
            c.get("game_id", "")
            for c in api_clips.values()
            if c.get("game_id")
        }
        game_name_map = await _fetch_games(http, headers, twitch_gids, client_id=client_id)

        async with AsyncSessionLocal() as session:
            for clip_record in batch:
                api_clip = api_clips.get(clip_record.external_id)
                if not api_clip:
                    continue

                twitch_gid = api_clip.get("game_id", "")
                game_name = game_name_map.get(str(twitch_gid)) if twitch_gid else None
                if not game_name:
                    continue

                game_id = await _get_or_create_game(session, str(twitch_gid), game_name)
                if game_id:
                    clip_record.game_id = game_id
                    session.add(clip_record)
                    updated += 1

            await session.commit()

        log.info("Backfill batch %d–%d done", i + 1, min(i + CLIPS_PAGE_SIZE, len(clips_to_fix)))

    log.info("Backfill complete: updated=%d", updated)
    return updated


async def sync_clips():
    from config.settings import CLIENT_ID, TWITCH_ACCESS_TOKEN

    if not CLIENT_ID or not TWITCH_ACCESS_TOKEN:
        log.error("CLIENT_ID or TWITCH_ACCESS_TOKEN not set in .env")
        return

    # Refresh token at start to ensure it's valid
    access_token = await _ensure_valid_token(TWITCH_ACCESS_TOKEN)
    headers = _build_headers(CLIENT_ID, access_token)

    async with aiohttp.ClientSession() as http:
        broadcaster_id = await _resolve_broadcaster_id(http, headers, client_id=CLIENT_ID)
        log.info("Resolved %s → broadcaster_id=%s", BROADCASTER_LOGIN, broadcaster_id)

        # --- Phase 1: fetch clips from Twitch ---
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Stream).where(Stream.started_at.isnot(None)).order_by(Stream.started_at)
            )
            streams = list(result.scalars().all())

        if not streams:
            log.error("No streams found in DB")
            return

        log.info("Loaded %d streams from DB", len(streams))

        clips = await _fetch_all_clips_by_streams(
            http, headers, broadcaster_id, streams, client_id=CLIENT_ID,
        )
        log.info("Total unique clips fetched: %d", len(clips))

        if not clips:
            return

        # --- Phase 2: resolve game names ---
        twitch_game_ids = {c.get("game_id", "") for c in clips if c.get("game_id")}
        game_name_map = await _fetch_games(http, headers, twitch_game_ids, client_id=CLIENT_ID)
        log.info("Resolved %d game names from Twitch API", len(game_name_map))

        # --- Phase 3: write to DB ---
        inserted = 0
        skipped_no_stream = 0
        skipped_duplicate = 0

        async with AsyncSessionLocal() as session:
            for clip in clips:
                external_id = clip["id"]
                clip_dt = _parse_dt(clip["created_at"])

                existing = await session.execute(
                    select(Clip.id).where(Clip.external_id == external_id).limit(1)
                )
                if existing.first():
                    skipped_duplicate += 1
                    continue

                stream_id = await _find_stream_for_clip(session, clip_dt)
                if stream_id is None:
                    skipped_no_stream += 1
                    continue

                twitch_gid = clip.get("game_id", "")
                game_name = game_name_map.get(str(twitch_gid)) if twitch_gid else None
                game_id = await _get_or_create_game(session, str(twitch_gid), game_name)

                creator_user_id = None
                creator_login = clip.get("creator_name", "").lower()
                creator_display = clip.get("creator_name", "")
                creator_tid = clip.get("creator_id", "")
                if creator_login:
                    user = await _get_or_create_user(
                        session,
                        twitch_user_id=str(creator_tid),
                        login=creator_login,
                        display_name=creator_display,
                    )
                    creator_user_id = user.id

                session.add(
                    Clip(
                        external_id=external_id,
                        stream_id=stream_id,
                        game_id=game_id,
                        creator_user_id=creator_user_id,
                        title=clip.get("title", ""),
                        url=clip["url"],
                        thumbnail_url=clip.get("thumbnail_url"),
                        created_at=clip_dt,
                        duration_seconds=int(float(clip.get("duration", 0))),
                        views_count=clip.get("view_count", 0),
                        synced_at=_utcnow(),
                    )
                )
                inserted += 1

                if inserted % 100 == 0:
                    await session.commit()
                    log.info("Committed %d clips…", inserted)

            await session.commit()

        log.info(
            "Sync done: inserted=%d, skipped_duplicate=%d, skipped_no_stream=%d",
            inserted, skipped_duplicate, skipped_no_stream,
        )

        # --- Phase 4: backfill missing game_ids from previous runs ---
        await _backfill_game_ids(http, headers, client_id=CLIENT_ID)


async def _ensure_valid_token(token: str) -> str:
    from services.token_service import ensure_valid_token
    return await ensure_valid_token()


if __name__ == "__main__":
    asyncio.run(sync_clips())
