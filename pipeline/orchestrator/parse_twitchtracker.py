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
from database.models import Game, Stream, StreamRecording
from pipeline.ingest.twitch_api import TwitchTokenExpiredError, fetch_user_id, fetch_vods
from pipeline.ingest.twitchtracker_parser import parse_stream_page_html
from pipeline.transform.streams_transform import (
    build_vods_index,
    pick_vod_candidates,
)
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


async def _fetch_vods_data(
    session: aiohttp.ClientSession,
    *,
    client_id: str,
    access_token: str,
    channel_login: str,
) -> tuple[dict, list[dict]]:
    """Fetch all VODs, return (vods_by_date_index, raw_vods_list)."""
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
    return build_vods_index(vods), vods


async def _sync_vod_recordings(
    session,
    streams: list[Stream],
    vods_by_date: dict,
    vod_urls_all: set[str],
) -> int:
    """Match ALL VODs to streams by time range and write to stream_recordings.

    A VOD is matched if its created_at falls between stream.started_at and stream.ended_at
    (or started_at + duration if ended_at is missing).
    Returns count added.
    """
    from datetime import datetime as dt, timedelta, timezone

    added = 0
    for stream in streams:
        if not stream.started_at:
            _log(f"  Stream id={stream.id}: no started_at, skipping VOD match")
            continue

        # Determine stream end time
        if stream.ended_at:
            stream_end = stream.ended_at
        elif stream.duration_minutes:
            stream_end = stream.started_at + timedelta(minutes=stream.duration_minutes)
        else:
            stream_end = stream.started_at + timedelta(hours=12)

        # Twitch API returns VOD created_at in UTC, stream.started_at is local time.
        # Use a wide window to account for timezone offset.
        vod_window_start = stream.started_at - timedelta(hours=6)
        vod_window_end = stream_end + timedelta(hours=6)

        _log(f"  Stream id={stream.id}: {stream.started_at} -> {stream_end}")
        _log(f"  VOD window: {vod_window_start} -> {vod_window_end}")

        # Get existing recording URLs for this stream
        rec_result = await session.execute(
            select(StreamRecording.url).where(
                StreamRecording.stream_id == stream.id,
                StreamRecording.source == "twitch",
            )
        )
        existing_urls = {row[0] for row in rec_result.all()}

        candidates = pick_vod_candidates(
            vods_by_date=vods_by_date,
            stream_date=stream.started_at.date(),
        )
        _log(f"  VOD candidates for {stream.started_at.date()}: {len(candidates)}")

        matched = 0
        for vod in candidates:
            url = str(vod.get("url") or "").strip()
            if not url or url in existing_urls:
                continue

            # Parse VOD created_at
            recorded_at_raw = vod.get("created_at")
            recorded_at = None
            if recorded_at_raw:
                try:
                    recorded_at = dt.fromisoformat(str(recorded_at_raw).replace("Z", "+00:00")).replace(tzinfo=None)
                except (ValueError, TypeError):
                    pass

            # Match: VOD created_at falls within wide window
            in_range = recorded_at and (vod_window_start <= recorded_at <= vod_window_end)
            if in_range:
                duration_minutes = _parse_vod_duration(vod.get("duration"))
                session.add(StreamRecording(
                    stream_id=stream.id,
                    source="twitch",
                    url=url,
                    duration_minutes=duration_minutes,
                    recorded_at=recorded_at,
                    created_at=dt.now(timezone.utc).replace(tzinfo=None),
                ))
                existing_urls.add(url)
                _log(f"  + VOD: {url[:70]}... ({duration_minutes}min, recorded_at={recorded_at})")
                added += 1
                matched += 1
            else:
                _log(f"    VOD skip: url={url[:50]}... recorded_at={recorded_at} (not in range)")

        if matched == 0 and candidates:
            _log(f"  No VOD matched for stream id={stream.id}")

    # --- Cleanup: remove stale VOD recordings not in current Twitch API response ---
    if vod_urls_all:
        stale_result = await session.execute(
            select(StreamRecording).where(StreamRecording.source == "twitch")
        )
        stale_count = 0
        for rec in stale_result.scalars().all():
            if rec.url and rec.url not in vod_urls_all:
                _log(f"  - Removing stale VOD: {rec.url[:70]}...")
                await session.delete(rec)
                stale_count += 1
        if stale_count:
            _log(f"  Removed {stale_count} stale VOD recording(s)")

    return added


def _parse_vod_duration(value: str | None) -> int | None:
    """Parse Twitch VOD duration string like '7h29m15s' or '1h5m' to minutes."""
    if not value:
        return None
    s = str(value).strip().lower()
    total = 0
    current = ""
    for ch in s:
        if ch.isdigit() or ch == ".":
            current += ch
        elif ch == "h":
            total += int(float(current) * 60) if current else 0
            current = ""
        elif ch == "m":
            total += int(current) if current else 0
            current = ""
        elif ch == "s":
            current = ""
    return total if total > 0 else None


async def _enrich_new_games(
    db_session,
    game_cache: dict[str, Game],
    only_game_ids: set[int] | None = None,
) -> tuple[int, int, int, int]:
    """Enrich games with HLTB + IGDB. If only_game_ids is provided, only those games."""
    HLTB_DELAY_SECONDS = 1.0
    HLTB_REQUEST_TIMEOUT_SECONDS = 10.0
    HLTB_MIN_SIMILARITY = 0.6

    # Non-gaming categories that should not be enriched via IGDB/HLTB
    SKIP_GAMES: set[str] = {
        "irl", "just chatting", "asmr", "mukbang", "hot tub", "beach",
        "pools", "talk shows", "science & technology", "food & drink",
        "fitness", "travel", "outdoors", "art", "music", "dance",
    }

    candidates = await select_igdb_enrichment_candidates(db_session)
    if only_game_ids is not None:
        ids = {int(i) for i in only_game_ids if int(i) > 0}
        candidates = [row for row in candidates if int(row.game_id) in ids]
    if not candidates:
        return (0, 0, 0, 0)

    hltb_last_call_at = 0.0
    updated_games = 0
    updated_fields = 0
    hltb_calls = 0
    igdb_calls = 0

    for idx, row in enumerate(candidates, start=1):
        if idx == 1 or idx % 10 == 0 or idx == len(candidates):
            _log(f"Games meta enrichment progress: {idx}/{len(candidates)}")
        key = _normalize_key(row.game_name)
        if not key or key in SKIP_GAMES:
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
    vods_by_date: dict = {}
    vod_urls_all: set[str] = set()
    if settings.CLIENT_ID and settings.TWITCH_ACCESS_TOKEN and settings.TWITCH_PRIMARY_CHANNEL:
        _log("Fetching VODs from Twitch API...")
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            try:
                vods_by_date, raw_vods = await _fetch_vods_data(
                    http,
                    client_id=settings.CLIENT_ID,
                    access_token=settings.TWITCH_ACCESS_TOKEN,
                    channel_login=settings.TWITCH_PRIMARY_CHANNEL,
                )
                vod_urls_all = {str(v.get("url") or "").strip() for v in raw_vods}
                vod_urls_all.discard("")
                _log(f"Fetched VODs for {len(vods_by_date)} date(s), {len(vod_urls_all)} URLs")
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
        processed_game_ids: set[int] = set()
        all_hours: list[tuple[str, float]] = []
        processed_streams: list[Stream] = []

        for page in parsed_pages:
            _log(f"\nProcessing stream: {page.date.date()}")
            twitch_stream_id = resolve_external_id(page, vods_by_date)
            _log(f"  Resolved external_id: {twitch_stream_id or '(date fallback)'}")

            stream, created = await upsert_stream_from_page(session, page, twitch_stream_id)
            if created:
                streams_added += 1
            else:
                streams_updated += 1
            processed_streams.append(stream)

            titles_added += await upsert_stream_titles(session, stream, page)

            stream_games_updated += await upsert_stream_games_from_page(session, stream, page, game_cache)

            hours = await increment_game_stats_hours(session, page, game_cache)
            all_hours.extend(hours)

            for entry in page.games:
                game = game_cache.get(entry.name)
                if game:
                    processed_game_ids.add(int(game.id))

        _log("\nSyncing VOD recordings...")
        vod_recordings_added = await _sync_vod_recordings(
            session, processed_streams, vods_by_date, vod_urls_all,
        )

        _log("\nUpdating streams_count...")
        streams_count_updated = await update_streams_count(session, only_game_ids=processed_game_ids)

        _log("Enriching games (IGDB + HLTB)...")
        games_enriched, fields_updated, hltb_calls, igdb_calls = await _enrich_new_games(
            session, game_cache, only_game_ids=processed_game_ids,
        )

        _log("\nCommitting transaction...")
        await session.commit()

        _log("\nStream page import:")
        _log(f"  Streams -> added: {streams_added}, updated: {streams_updated}")
        _log(f"  Titles -> added: {titles_added}")
        _log(f"  Stream games -> updated fields: {stream_games_updated}")
        _log(f"  VOD recordings -> added: {vod_recordings_added}")
        if all_hours:
            _log("  Hours added:")
            for name, hrs in all_hours:
                _log(f"    {name}: +{hrs:.1f}h")
        if streams_count_updated:
            _log("  streams_count updated:")
            for name, old, new in streams_count_updated:
                _log(f"    {name}: {old} -> {new}")
        _log(f"  Games enriched: {games_enriched} ({hltb_calls} HLTB, {igdb_calls} IGDB calls)")
        _log("Done!")
        return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
