from __future__ import annotations

"""
Orchestrator: storage/*.json -> database + enrichment.

Pipeline order:
- ingest: read twitchtracker JSON files
- load: sync streams and game stats
- load: recompute streams_count
- ingest/load: sync stream VOD URLs from Twitch API
- ingest/load: enrich games from HLTB + IGDB
"""

import asyncio
import json
from pathlib import Path
import time
from typing import Any

import aiohttp

import config.settings as settings
from database.db import AsyncSessionLocal
from database.models import Game, Stream
from pipeline.ingest.hltb_client import search_best
from pipeline.ingest.igdb_api import fetch_igdb_metadata
from pipeline.ingest.twitch_api import TwitchTokenExpiredError, fetch_user_id, fetch_vods
from pipeline.ingest.twitchtracker_parser import load_games_json, load_streams_json
from pipeline.load.load_game_stats import sync_game_stats, update_streams_count
from pipeline.load.load_game_meta import apply_igdb_patch, apply_hltb_patch, select_igdb_enrichment_candidates
from pipeline.load.load_streams import sync_streams
from sqlalchemy import select


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cache_path(project_root: Path) -> Path:
    return project_root / "storage" / "cache" / "import_json_to_db_enrich_cache.json"


def _now_ts() -> int:
    return int(time.time())


def _log(message: str) -> None:
    print(message, flush=True)


def _normalize_key(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _load_cache(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        return {"hltb": {}, "igdb": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("hltb", {})
    data.setdefault("igdb", {})
    return data


def _save_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _cache_fresh(entry: Any, *, ttl_days: int) -> bool:
    if not isinstance(entry, dict):
        return False
    updated_at = int(entry.get("updated_at") or 0)
    ttl_seconds = max(0, int(ttl_days) * 24 * 60 * 60)
    return (_now_ts() - updated_at) < ttl_seconds


async def _sync_vods(session, *, only_stream_ids: set[int] | None = None) -> tuple[int, int]:
    if not (settings.CLIENT_ID and settings.TWITCH_ACCESS_TOKEN and settings.TWITCH_PRIMARY_CHANNEL):
        print("Skipping VOD sync: missing CLIENT_ID/TWITCH_ACCESS_TOKEN/TWITCH_PRIMARY_CHANNEL.")
        return (0, 0)

    from services.token_service import try_refresh_token
    from pipeline.load.load_streams import VodSyncStats
    from pipeline.transform.streams_transform import build_vods_index, is_match, pick_vod_candidates, StreamForVodMatch

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        try:
            user_id = await fetch_user_id(
                http,
                client_id=settings.CLIENT_ID,
                access_token=settings.TWITCH_ACCESS_TOKEN,
                channel_login=settings.TWITCH_PRIMARY_CHANNEL,
            )
        except TwitchTokenExpiredError:
            new_token = await try_refresh_token()
            if not new_token:
                print("Skipping VOD sync: token refresh failed.")
                return (0, 0)
            settings.TWITCH_ACCESS_TOKEN = new_token
            user_id = await fetch_user_id(
                http,
                client_id=settings.CLIENT_ID,
                access_token=new_token,
                channel_login=settings.TWITCH_PRIMARY_CHANNEL,
            )

        vods = await fetch_vods(
            http,
            client_id=settings.CLIENT_ID,
            access_token=settings.TWITCH_ACCESS_TOKEN,
            user_id=user_id,
        )

    vod_urls = {str(v.get("url") or "").strip() for v in vods}
    vod_urls.discard("")
    vods_by_date = build_vods_index(vods)

    removed = 0
    matched = 0

    if only_stream_ids:
        ids = [int(i) for i in only_stream_ids if int(i) > 0]
        if not ids:
            return (0, 0)

    q = select(Stream)
    if only_stream_ids:
        q = q.where(Stream.id.in_(list(only_stream_ids)))
    result = await session.execute(q)
    streams = result.scalars().all()

    from database.models import StreamRecording
    for stream in streams:
        rec_result = await session.execute(
            select(StreamRecording).where(StreamRecording.stream_id == stream.id, StreamRecording.source == "twitch")
        )
        existing_rec = rec_result.scalar_one_or_none()
        if existing_rec and existing_rec.url and existing_rec.url not in vod_urls:
            await session.delete(existing_rec)
            removed += 1

    for stream in streams:
        rec_result = await session.execute(
            select(StreamRecording).where(StreamRecording.stream_id == stream.id, StreamRecording.source == "twitch")
        )
        existing_rec = rec_result.scalar_one_or_none()
        if existing_rec:
            continue
        if not stream.started_at:
            continue

        stream_view = StreamForVodMatch(id=int(stream.id), date=stream.started_at, title=stream.title)
        candidates = pick_vod_candidates(vods_by_date=vods_by_date, stream_date=stream.started_at.date())
        for vod in candidates:
            if is_match(stream_view, vod):
                url = str(vod.get("url") or "").strip()
                if url:
                    session.add(StreamRecording(stream_id=stream.id, source="twitch", url=url))
                    matched += 1
                break

    return (removed, matched)


async def _enrich_games_meta(
    session,
    *,
    cache: dict[str, Any],
    only_game_ids: set[int] | None = None,
) -> tuple[int, int, int, int]:
    HLTB_MIN_SIMILARITY = 0.60
    HLTB_DELAY_SECONDS = 1.25
    HLTB_REQUEST_TIMEOUT_SECONDS = 20
    HLTB_CACHE_TTL_DAYS = 30
    IGDB_CACHE_TTL_DAYS = 30

    candidates = await select_igdb_enrichment_candidates(session)
    if only_game_ids is not None:
        ids = {int(i) for i in only_game_ids if int(i) > 0}
        candidates = [row for row in candidates if int(row.game_id) in ids]
    if not candidates:
        _log("Games meta enrichment: no candidates.")
        return (0, 0, 0, 0)

    _log(f"Games meta enrichment: {len(candidates)} candidates.")
    updated_games = 0
    updated_fields = 0
    hltb_calls = 0
    igdb_calls = 0
    hltb_last_call_at = 0.0

    from datetime import datetime, timezone

    for idx, row in enumerate(candidates, start=1):
        if idx == 1 or idx % 10 == 0 or idx == len(candidates):
            _log(f"Games meta enrichment progress: {idx}/{len(candidates)}")
        key = _normalize_key(row.game_name)
        if not key:
            continue

        hltb_patch: dict[str, Any] = {}
        if not row.has_hltb:
            hltb_entry = cache["hltb"].get(key)
            if _cache_fresh(hltb_entry, ttl_days=HLTB_CACHE_TTL_DAYS) and isinstance(
                hltb_entry.get("hltb_hours"), (int, float)
            ):
                hltb_patch["hltb_all_styles"] = float(hltb_entry["hltb_hours"])
            else:
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
                    cache["hltb"][key] = {
                        "hltb_hours": float(result.hltb_hours),
                        "matched_name": result.matched_name,
                        "similarity": float(result.similarity),
                        "updated_at": _now_ts(),
                    }
                else:
                    cache["hltb"][key] = {"hltb_hours": None, "updated_at": _now_ts()}

        igdb_patch: dict[str, Any] = {}
        if not row.has_igdb:
            igdb_entry = cache["igdb"].get(key)
            if _cache_fresh(igdb_entry, ttl_days=IGDB_CACHE_TTL_DAYS):
                igdb_patch = {k: v for k, v in igdb_entry.items() if k != "updated_at" and v}
            else:
                meta = await fetch_igdb_metadata(row.game_name)
                igdb_calls += 1
                if meta is not None:
                    igdb_patch = {k: v for k, v in {
                        "steam_url": getattr(meta, "steam_url", None),
                        "description_ru": getattr(meta, "genres_text", None),
                    }.items() if v}
                    cache["igdb"][key] = {
                        **igdb_patch,
                        "updated_at": _now_ts(),
                    }
                else:
                    cache["igdb"][key] = {"updated_at": _now_ts()}

        if hltb_patch:
            if await apply_hltb_patch(session, game_id=row.game_id, patch=hltb_patch):
                updated_games += 1
                updated_fields += len(hltb_patch)

        if igdb_patch:
            if await apply_igdb_patch(session, game_id=row.game_id, patch=igdb_patch):
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
    streams_json = root / "storage" / "streams.json"
    games_json = root / "storage" / "games.json"
    cache_path = _cache_path(root)
    cache = _load_cache(cache_path)

    _log("Loading JSON files...")
    streams_data = load_streams_json(streams_json)
    games_data = load_games_json(games_json)
    _log(f"Loaded streams: {len(streams_data)}, games: {len(games_data)}")

    async with AsyncSessionLocal() as session:
        game_result = await session.execute(select(Game))
        all_games = game_result.scalars().all()
        game_cache = {game.name: game for game in all_games}
        known_game_ids = {int(game.id) for game in game_cache.values() if game.id is not None}

        eid_result = await session.execute(
            select(Stream.external_id).where(Stream.external_id.isnot(None))
        )
        existing_stream_external_ids = {str(row[0]) for row in eid_result.all() if row[0]}

        _log("Syncing streams...")
        stream_stats = await sync_streams(
            session, streams_data, game_cache,
            prune_missing=True, sync_participants_from_title=True,
        )
        _log("Syncing game stats...")
        game_stats = await sync_game_stats(session, games_data, game_cache, prune_missing=True)
        _log("Updating streams_count...")
        streams_count_updated = await update_streams_count(session)

        current_game_ids = {int(game.id) for game in game_cache.values() if game.id is not None}
        added_game_ids = current_game_ids - known_game_ids
        incoming_stream_external_ids = {row.date.isoformat() for row in streams_data}
        added_stream_external_ids = incoming_stream_external_ids - existing_stream_external_ids

        sid_result = await session.execute(
            select(Stream.id).where(Stream.external_id.in_(list(added_stream_external_ids)))
        )
        added_stream_ids = {int(row[0]) for row in sid_result.all() if row[0] is not None}

        _log(f"New entities in this run -> games: {len(added_game_ids)}, streams: {len(added_stream_ids)}")

        _log("Syncing VOD URLs...")
        vod_removed, vod_matched = await _sync_vods(session, only_stream_ids=added_stream_ids)
        _log("Enriching games metadata (HLTB + IGDB)...")
        games_updated, games_fields_updated, hltb_calls, igdb_calls = await _enrich_games_meta(
            session, cache=cache, only_game_ids=added_game_ids,
        )

        _log("Committing transaction...")
        await session.commit()
        _save_cache(cache_path, cache)

        _log("JSON import to DB:")
        _log(f"Streams -> added: {stream_stats.added}, updated: {stream_stats.updated}, unchanged: {stream_stats.unchanged}, deleted: {stream_stats.deleted}")
        _log(f"Game stats -> added: {game_stats.added}, updated: {game_stats.updated}, unchanged: {game_stats.unchanged}, deleted: {game_stats.deleted}")
        _log(f"GameStats.streams_count updated rows: {streams_count_updated}")
        _log("Post-import enrichment:")
        _log(f"VOD sync -> removed outdated: {vod_removed}, matched new: {vod_matched}")
        _log(f"Games metadata -> updated games: {games_updated}, updated fields: {games_fields_updated}, hltb calls: {hltb_calls}, igdb calls: {igdb_calls}")
        _log("Done!")
        return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
