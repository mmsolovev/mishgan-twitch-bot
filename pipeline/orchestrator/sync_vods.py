"""
Standalone script: sync stream_recordings with current Twitch VODs.

Usage:
    python -m pipeline.orchestrator.sync_vods

Actions:
  1. Fetch all current VODs from Twitch API.
  2. Remove stale stream_recordings whose URL is no longer in the API response.
  3. Update existing recordings: fill duration_minutes / recorded_at if missing.
  4. For streams that have no Twitch recording yet, try to match by time range
     (VOD created_at within ±6h of stream start) and add it.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from sqlalchemy import select

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config.settings as settings
from database.db import AsyncSessionLocal
from database.models import Stream, StreamRecording
from pipeline.ingest.twitch_api import TwitchTokenExpiredError, fetch_user_id, fetch_vods
from pipeline.transform.streams_transform import build_vods_index, pick_vod_candidates

_LOG_FMT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=_LOG_FMT, handlers=[logging.StreamHandler()])
_log = logging.getLogger("sync_vods")


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


async def _fetch_vods(http: aiohttp.ClientSession) -> tuple[list[dict], set[str]]:
    """Fetch all VODs from Twitch API. Returns (vods, urls)."""
    token = settings.TWITCH_ACCESS_TOKEN
    client_id = settings.CLIENT_ID
    channel = settings.TWITCH_PRIMARY_CHANNEL

    if not (client_id and token and channel):
        _log.error("Missing CLIENT_ID / TWITCH_ACCESS_TOKEN / TWITCH_PRIMARY_CHANNEL")
        return [], set()

    try:
        user_id = await fetch_user_id(http, client_id=client_id, access_token=token, channel_login=channel)
    except TwitchTokenExpiredError:
        from services.token_service import try_refresh_token

        new_token = await try_refresh_token()
        if not new_token:
            _log.error("Token refresh failed")
            return [], set()
        settings.TWITCH_ACCESS_TOKEN = new_token
        token = new_token
        user_id = await fetch_user_id(http, client_id=client_id, access_token=token, channel_login=channel)

    vods = await fetch_vods(http, client_id=client_id, access_token=token, user_id=user_id)
    urls = {str(v.get("url") or "").strip() for v in vods}
    urls.discard("")
    _log.info("Fetched %d VOD(s), %d unique URL(s)", len(vods), len(urls))
    return vods, urls


def _build_vod_lookup(vods: list[dict]) -> dict[str, dict]:
    """Build url -> vod dict lookup for quick access."""
    lookup: dict[str, dict] = {}
    for v in vods:
        url = str(v.get("url") or "").strip()
        if url:
            lookup[url] = v
    return lookup


async def run() -> None:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        vods, vod_urls = await _fetch_vods(http)

    if not vod_urls:
        _log.warning("No VODs fetched, nothing to sync.")
        return

    vod_lookup = _build_vod_lookup(vods)
    vods_by_date = build_vods_index(vods)

    async with AsyncSessionLocal() as session:
        # --- 1. Remove stale recordings ---
        result = await session.execute(
            select(StreamRecording).where(StreamRecording.source == "twitch")
        )
        all_recordings = result.scalars().all()

        stale_count = 0
        kept_count = 0
        for rec in all_recordings:
            if rec.url and rec.url not in vod_urls:
                _log.info("  - Removing stale: %s", rec.url[:80])
                await session.delete(rec)
                stale_count += 1
            else:
                kept_count += 1
        _log.info("Stale cleanup: removed %d, kept %d", stale_count, kept_count)

        # --- 2. Update existing recordings: fill missing duration_minutes / recorded_at ---
        result = await session.execute(
            select(StreamRecording).where(StreamRecording.source == "twitch")
        )
        existing = result.scalars().all()

        updated = 0
        for rec in existing:
            if not rec.url or rec.url not in vod_lookup:
                continue
            vod = vod_lookup[rec.url]
            changed = False

            if rec.duration_minutes is None:
                dur = _parse_vod_duration(vod.get("duration"))
                if dur is not None:
                    rec.duration_minutes = dur
                    changed = True

            if rec.recorded_at is None:
                raw = vod.get("created_at")
                if raw:
                    try:
                        rec.recorded_at = datetime.fromisoformat(
                            str(raw).replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        changed = True
                    except (ValueError, TypeError):
                        pass

            if changed:
                updated += 1

        _log.info("Updated %d existing recording(s) with missing fields", updated)

        # --- 3. Add missing recordings for streams without one ---
        streams_result = await session.execute(
            select(Stream).where(Stream.started_at.isnot(None)).order_by(Stream.started_at.desc())
        )
        streams = streams_result.scalars().all()

        rec_result = await session.execute(
            select(StreamRecording.stream_id).where(StreamRecording.source == "twitch")
        )
        streams_with_recording = {row[0] for row in rec_result.all()}

        all_rec_result = await session.execute(
            select(StreamRecording.url).where(StreamRecording.source == "twitch")
        )
        existing_rec_urls = {row[0] for row in all_rec_result.all()}

        added = 0
        for stream in streams:
            if stream.id in streams_with_recording:
                continue

            if stream.ended_at:
                stream_end = stream.ended_at
            elif stream.duration_minutes:
                stream_end = stream.started_at + timedelta(minutes=stream.duration_minutes)
            else:
                stream_end = stream.started_at + timedelta(hours=12)

            vod_window_start = stream.started_at - timedelta(hours=6)
            vod_window_end = stream_end + timedelta(hours=6)

            candidates = pick_vod_candidates(vods_by_date=vods_by_date, stream_date=stream.started_at.date())

            for vod in candidates:
                url = str(vod.get("url") or "").strip()
                if not url or url in existing_rec_urls:
                    continue

                recorded_at_raw = vod.get("created_at")
                recorded_at = None
                if recorded_at_raw:
                    try:
                        recorded_at = datetime.fromisoformat(
                            str(recorded_at_raw).replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    except (ValueError, TypeError):
                        pass

                if recorded_at and (vod_window_start <= recorded_at <= vod_window_end):
                    duration_minutes = _parse_vod_duration(vod.get("duration"))
                    session.add(StreamRecording(
                        stream_id=stream.id,
                        source="twitch",
                        url=url,
                        duration_minutes=duration_minutes,
                        recorded_at=recorded_at,
                        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    ))
                    streams_with_recording.add(stream.id)
                    existing_rec_urls.add(url)
                    _log.info(
                        "  + Added VOD for stream %s (%s): %s (%dmin)",
                        stream.id, stream.started_at.date(), url[:70], duration_minutes or 0,
                    )
                    added += 1
                    break

        _log.info("Added %d new VOD recording(s)", added)

        await session.commit()
        _log.info("Done. Committed.")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
