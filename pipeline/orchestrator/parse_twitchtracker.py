from __future__ import annotations

"""
Orchestrator: TwitchTracker stream detail pages → database.

Pipeline:
1. Parse HTML stream pages from storage/pages/
2. Fetch VODs from Twitch API for stream_id matching
3. For each stream page:
   a. Upsert stream (find by date, update metrics, write external_id)
   b. Upsert title changes into stream_titles
   c. Upsert stream_games with per-game metrics
   d. Increment game_stats.streamed_hours for each game
4. Recompute GameStats.streams_count
5. Enrich new games with IGDB + HLTB (immediate)
"""

import asyncio
import time
from pathlib import Path

import aiohttp

import config.settings as settings
from database.db import AsyncSessionLocal
from database.models import Game
from pipeline.ingest.twitch_api import TwitchTokenExpiredError, fetch_user_id, fetch_vods
from pipeline.ingest.twitchtracker_parser import parse_stream_page_html
from pipeline.transform.streams_transform import build_vods_index
from pipeline.transform.stream_page_transform import resolve_external_id
from pipeline.load.load_stream_page import (
    increment_game_stats_hours,
    upsert_stream_from_page,
    upsert_stream_games_from_page,
    upsert_stream_titles,
    update_streams_count,
)
from pipeline.load.load_game_meta import apply_igdb_patch, apply_hltb_patch, select_igdb_enrichment_candidates
from pipeline.ingest.hltb_client import search_best
from pipeline.ingest.igdb_api import fetch_igdb_metadata
from sqlalchemy import select


def _log(message: str) -> None:
    print(message, flush=True)


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pages_dir(root: Path) -> Path:
    return root / "storage" / "pages"


def _find_stream_pages(pages_dir: Path) -> list[Path]:
    """Find all stream_*.html files in pages directory."""
    return sorted(pages_dir.glob("stream_*.html"))


def _normalize_key(value: str) -> str:
    return " ".join((value or "").casefold().split())


async def _fetch_vods_index(
    session: aiohttp.ClientSession,
    *,
    client_id: str,
    access_token: str,
    channel_login: str,
) -> dict:
    """Fetch all VODs and build date index."""
    from services.token_service import try_refresh_token

    try:
        user_id = await fetch_user_id(
            session,
            client_id=client_id,
            access_token=access_token,
            channel_login=channel_login,
        )
    except TwitchTokenExpiredError:
        _log("Token expired, attempting refresh...")
        new_token = await try_refresh_token()
        settings.TWITCH_ACCESS_TOKEN = new_token
        user_id = await fetch_user_id(
            session,
            client_id=client_id,
            access_token=new_token,
            channel_login=channel_login,
        )

    vods = await fetch_vods(
        session,
        client_id=client_id,
        access_token=access_token,
        user_id=user_id,
    )
    return build_vods_index(vods)


async def _enrich_new_games(
    db_session,
    game_cache: dict[str, Game],
) -> tuple[int, int, int, int]:
    """Enrich new games with HLTB + IGDB (immediate)."""
    from dataclasses import dataclass

    HLTB_DELAY_SECONDS = 1.0
    HLTB_REQUEST_TIMEOUT_SECONDS = 10.0
    HLTB_MIN_SIMILARITY = 0.6

    candidates = await select_igdb_enrichment_candidates(db_session, only_without_igdb=True)
    if not candidates:
        return (0, 0, 0, 0)

    hltb_last_call_at = 0.0
    updated_games = 0
    updated_fields = 0
    hltb_calls = 0
    igdb_calls = 0

    from datetime import datetime, timezone

    for idx, row in enumerate(candidates, start=1):
        if idx == 1 or idx % 10 == 0 or idx == len(candidates):
            _log(f"Games meta enrichment progress: {idx}/{len(candidates)}")
        key = _normalize_key(row.game_name)
        if not key:
            continue

        hltb_patch = {}
        if not row.has_hltb:
            since_last = time.time() - hltb_last_call_at
            if since_last < HLTB_DELAY_SECONDS:
                await asyncio.sleep(HLTB_DELAY_SECONDS - since_last)
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(search_best, row.game_name, min_similarity=HLTB_MIN_SIMILARITY),
                    timeout=HLTB_REQUEST_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                result = None
            hltb_last_call_at = time.time()
            hltb_calls += 1
            if result is not None:
                hltb_patch["hltb_all_styles"] = float(result.hltb_hours)

        igdb_patch = {}
        if not row.has_igdb:
            meta = await fetch_igdb_metadata(row.game_name)
            igdb_calls += 1
            if meta is not None:
                igdb_patch = {k: v for k, v in {
                    "steam_url": getattr(meta, "steam_url", None),
                    "description_en": getattr(meta, "description_short", None),
                }.items() if v}

        if hltb_patch:
            if await apply_hltb_patch(db_session, game_id=row.game_id, patch=hltb_patch):
                updated_games += 1
                updated_fields += len(hltb_patch)
        if igdb_patch:
            if await apply_igdb_patch(db_session, game_id=row.game_id, patch=igdb_patch):
                updated_games += 1
                updated_fields += len(igdb_patch)

    return (updated_games, updated_fields, hltb_calls, igdb_calls)


async def run() -> int:
    from services.token_service import ensure_valid_token

    try:
        new_token = await ensure_valid_token()
        settings.TWITCH_ACCESS_TOKEN = new_token
    except RuntimeError as exc:
        _log(f"Token validation failed: {exc}")
        return 1

    root = _default_project_root()
    pages_dir = _pages_dir(root)
    page_files = _find_stream_pages(pages_dir)

    if not page_files:
        _log("No stream_*.html files found in storage/pages/")
        return 0

    _log(f"Found {len(page_files)} stream page(s)")

    # Parse all pages
    parsed_pages = []
    for path in page_files:
        page = parse_stream_page_html(path)
        if page is not None:
            parsed_pages.append(page)
        else:
            _log(f"Skipping {path.name}: failed to parse")

    _log(f"Successfully parsed {len(parsed_pages)} page(s)")

    if not parsed_pages:
        return 0

    # Fetch VODs from Twitch API
    vods_by_date = {}
    if settings.CLIENT_ID and settings.TWITCH_ACCESS_TOKEN and settings.TWITCH_PRIMARY_CHANNEL:
        _log("Fetching VODs from Twitch API...")
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            try:
                vods_by_date = await _fetch_vods_index(
                    http,
                    client_id=settings.CLIENT_ID,
                    access_token=settings.TWITCH_ACCESS_TOKEN,
                    channel_login=settings.TWITCH_PRIMARY_CHANNEL,
                )
                _log(f"Fetched VODs for {len(vods_by_date)} date(s)")
            except TwitchTokenExpiredError:
                _log("Failed to fetch VODs: token expired")
            except Exception as exc:
                _log(f"Failed to fetch VODs: {exc}")
    else:
        _log("Skipping VOD fetch: missing Twitch credentials")

    async with AsyncSessionLocal() as session:
        # Pre-load game cache
        from sqlalchemy import select as sa_select
        game_result = await session.execute(sa_select(Game))
        all_games = game_result.scalars().all()
        game_cache = {game.name: game for game in all_games}

        streams_added = 0
        streams_updated = 0
        titles_added = 0
        stream_games_updated = 0
        game_stats_updated = 0

        for page in parsed_pages:
            # Resolve external_id from VOD
            twitch_stream_id = resolve_external_id(page, vods_by_date)

            # Upsert stream
            stream, created = await upsert_stream_from_page(session, page, twitch_stream_id)
            if created:
                streams_added += 1
            else:
                streams_updated += 1

            # Upsert titles
            titles_added += await upsert_stream_titles(session, stream, page)

            # Upsert stream_games with metrics
            stream_games_updated += await upsert_stream_games_from_page(session, stream, page, game_cache)

            # Increment game_stats.streamed_hours
            game_stats_updated += await increment_game_stats_hours(session, page, game_cache)

        # Recompute streams_count
        _log("Updating streams_count...")
        streams_count_updated = await update_streams_count(session)

        # Enrich new games immediately
        _log("Enriching new games (IGDB + HLTB)...")
        games_enriched, fields_updated, hltb_calls, igdb_calls = await _enrich_new_games(session, game_cache)

        _log("Committing transaction...")
        await session.commit()

        _log("Stream page import:")
        _log(f"  Streams -> added: {streams_added}, updated: {streams_updated}")
        _log(f"  Titles -> added: {titles_added}")
        _log(f"  Stream games -> updated: {stream_games_updated}")
        _log(f"  GameStats hours incremented: {game_stats_updated}")
        _log(f"  GameStats.streams_count updated: {streams_count_updated}")
        _log(f"  Games enriched: {games_enriched} ({hltb_calls} HLTB, {igdb_calls} IGDB calls)")
        _log("Done!")
        return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
