from __future__ import annotations

"""
Load layer: writes targeting `game_stats` table (GameStats).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Game, GameStats, StreamGame
from pipeline.ingest.twitchtracker_parser import TwitchTrackerGameRow
from pipeline.load.load_games import get_or_create_game


@dataclass(frozen=True, slots=True)
class SyncStats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0


async def sync_game_stats(
    session: AsyncSession,
    games_data: list[TwitchTrackerGameRow],
    game_cache: dict[str, Game],
    *,
    prune_missing: bool = False,
) -> SyncStats:
    stats = SyncStats()
    desired_game_ids: set[int] = set()

    for data in games_data:
        game = await get_or_create_game(session, game_cache, data.name)
        desired_game_ids.add(int(game.id))

        result = await session.execute(select(GameStats).where(GameStats.game_id == game.id))
        game_stats = result.scalar_one_or_none()
        created = False

        if game_stats is None:
            game_stats = GameStats(game_id=game.id)
            session.add(game_stats)
            await session.flush()
            created = True

        changed = created

        field_map = {
            "streamed_hours": data.hours_streamed,
            "avg_viewers": data.avg_viewers,
            "max_viewers": data.max_viewers,
            "followers_per_hour": data.followers_per_hour,
            "last_stream": data.last_stream,
        }
        for attr, value in field_map.items():
            if getattr(game_stats, attr, None) != value:
                setattr(game_stats, attr, value)
                changed = True

        game_stats.synced_at = datetime.utcnow()

        if created:
            stats = SyncStats(added=stats.added + 1, updated=stats.updated, unchanged=stats.unchanged, deleted=stats.deleted)
        elif changed:
            stats = SyncStats(added=stats.added, updated=stats.updated + 1, unchanged=stats.unchanged, deleted=stats.deleted)
        else:
            stats = SyncStats(added=stats.added, updated=stats.updated, unchanged=stats.unchanged + 1, deleted=stats.deleted)

    if prune_missing:
        deleted = 0
        result = await session.execute(select(GameStats))
        for row in result.scalars().all():
            if int(row.game_id) not in desired_game_ids:
                await session.delete(row)
                deleted += 1

        if deleted:
            stats = SyncStats(added=stats.added, updated=stats.updated, unchanged=stats.unchanged, deleted=stats.deleted + deleted)

    return stats


async def update_streams_count(session: AsyncSession) -> int:
    """
    Recomputes GameStats.streams_count from stream_games table.
    Returns number of rows updated.
    """
    result = await session.execute(
        select(StreamGame.game_id, func.count(StreamGame.stream_id)).group_by(StreamGame.game_id)
    )
    counts_by_game_id = dict(result.all())

    updated = 0
    result = await session.execute(select(GameStats))
    for stats in result.scalars().all():
        new_value = int(counts_by_game_id.get(stats.game_id, 0))
        if int(stats.streams_count or 0) != new_value:
            stats.streams_count = new_value
            updated += 1

    return updated


__all__ = ["SyncStats", "sync_game_stats", "update_streams_count"]
