from __future__ import annotations

"""
Load layer: writes targeting `streams` table (Stream).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Game, Stream, StreamGame
from pipeline.ingest.twitchtracker_parser import TwitchTrackerStreamRow
from pipeline.load.load_participants import sync_stream_participants_from_title
from pipeline.load.load_stream_games import sync_stream_games


@dataclass(frozen=True, slots=True)
class SyncStats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0


async def sync_streams(
    session: AsyncSession,
    streams_data: list[TwitchTrackerStreamRow],
    game_cache: dict[str, Game],
    *,
    prune_missing: bool = False,
    sync_participants_from_title: bool = False,
) -> SyncStats:
    result = await session.execute(select(Stream))
    all_streams = result.scalars().all()

    existing_by_external_id: dict[str, Stream] = {
        (stream.external_id or ""): stream
        for stream in all_streams
        if stream.external_id
    }

    existing_by_date: dict[datetime, Stream] = {
        stream.started_at: stream
        for stream in all_streams
        if stream.started_at
    }

    desired_external_ids: set[str] = set()
    stats = SyncStats()

    for data in streams_data:
        external_id = data.date.isoformat()
        desired_external_ids.add(external_id)

        stream = existing_by_external_id.get(external_id) or existing_by_date.get(data.date)
        created = False

        if stream is None:
            stream = Stream(external_id=external_id, started_at=data.date)
            session.add(stream)
            await session.flush()
            created = True

        changed = created

        if stream.external_id != external_id:
            stream.external_id = external_id
            changed = True

        if stream.started_at != data.date:
            stream.started_at = data.date
            changed = True

        if stream.title != data.title:
            stream.title = data.title
            changed = True

        duration_minutes = int(data.duration_hours * 60) if data.duration_hours else None
        if stream.duration_minutes != duration_minutes:
            stream.duration_minutes = duration_minutes
            changed = True

        if stream.avg_viewers != data.avg_viewers:
            stream.avg_viewers = data.avg_viewers
            changed = True

        if stream.max_viewers != data.max_viewers:
            stream.max_viewers = data.max_viewers
            changed = True

        if stream.followers_gained != data.followers:
            stream.followers_gained = data.followers
            changed = True

        if stream.views_gained != data.views:
            stream.views_gained = data.views
            changed = True

        if await sync_stream_games(session, stream, data.games, game_cache):
            changed = True

        if sync_participants_from_title:
            if await sync_stream_participants_from_title(session, stream, data.title):
                changed = True

        if created:
            stats = SyncStats(added=stats.added + 1, updated=stats.updated, unchanged=stats.unchanged, deleted=stats.deleted)
        elif changed:
            stats = SyncStats(added=stats.added, updated=stats.updated + 1, unchanged=stats.unchanged, deleted=stats.deleted)
        else:
            stats = SyncStats(added=stats.added, updated=stats.updated, unchanged=stats.unchanged + 1, deleted=stats.deleted)

    if prune_missing:
        deleted = 0
        for stream in all_streams:
            if stream.external_id and stream.external_id not in desired_external_ids:
                for sg in list(stream.stream_games):
                    await session.delete(sg)
                await session.delete(stream)
                deleted += 1

        if deleted:
            stats = SyncStats(added=stats.added, updated=stats.updated, unchanged=stats.unchanged, deleted=stats.deleted + deleted)

    return stats


__all__ = [
    "SyncStats",
    "sync_streams",
]
