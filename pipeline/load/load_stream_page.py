from __future__ import annotations

"""
Load layer: writes stream-page data to DB (streams, stream_titles,
stream_games per-game metrics, game_stats aggregation).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Game, GameStats, Stream, StreamGame, StreamTitle
from pipeline.ingest.twitchtracker_parser import StreamGameEntry, StreamPageData
from pipeline.load.load_games import get_or_create_game


async def _find_stream_by_date(session: AsyncSession, dt: datetime) -> Stream | None:
    """Find existing stream by date (matching on started_at date part)."""
    from sqlalchemy import cast, Date
    result = await session.execute(
        select(Stream).where(cast(Stream.started_at, Date) == dt.date())
    )
    return result.scalars().first()


async def upsert_stream_from_page(
    session: AsyncSession,
    page: StreamPageData,
    external_id: str | None,
) -> tuple[Stream, bool]:
    """
    Upsert a Stream from parsed page data.

    Returns (stream, created).
    If external_id is provided and the stream doesn't have one, writes it.
    """
    stream = await _find_stream_by_date(session, page.date)
    created = False

    if stream is None:
        stream = Stream(
            external_id=external_id or page.date.date().isoformat(),
            started_at=page.started_at,
            ended_at=page.ended_at,
            created_at=datetime.now(timezone.utc),
        )
        session.add(stream)
        await session.flush()
        created = True

    changed = created

    # Write external_id (only for new Twitch IDs, don't overwrite date strings)
    if external_id and not stream.external_id:
        stream.external_id = external_id
        changed = True
    elif external_id and stream.external_id and not stream.external_id.startswith("20"):
        # external_id is already a Twitch ID, check if we need to update
        pass
    # If existing external_id is a date string and we now have a Twitch ID, upgrade it
    if external_id and stream.external_id and stream.external_id.startswith("20"):
        stream.external_id = external_id
        changed = True

    if stream.started_at != page.started_at:
        stream.started_at = page.started_at
        changed = True

    if stream.ended_at != page.ended_at:
        stream.ended_at = page.ended_at
        changed = True

    if stream.duration_minutes != page.duration_minutes:
        stream.duration_minutes = page.duration_minutes
        changed = True

    if stream.avg_viewers != page.avg_viewers:
        stream.avg_viewers = page.avg_viewers
        changed = True

    if stream.max_viewers != page.peak_viewers:
        stream.max_viewers = page.peak_viewers
        changed = True

    if stream.followers_gained != page.followers_gained:
        stream.followers_gained = page.followers_gained
        changed = True

    if stream.title != (page.title_changes[0].title if page.title_changes else ""):
        stream.title = page.title_changes[0].title if page.title_changes else ""
        changed = True

    stream.updated_at = datetime.now(timezone.utc)

    return stream, created


async def upsert_stream_titles(
    session: AsyncSession,
    stream: Stream,
    page: StreamPageData,
) -> int:
    """Insert new title changes. Returns number of titles added."""
    if not page.title_changes:
        return 0

    existing_result = await session.execute(
        select(StreamTitle.title, StreamTitle.started_at).where(StreamTitle.stream_id == stream.id)
    )
    existing_titles = {(row[0], row[1]) for row in existing_result.all()}

    added = 0
    for i, tc in enumerate(page.title_changes):
        # Parse the time string to a datetime
        title_time = _parse_title_time(tc.time, page.date)
        if (tc.title, title_time) in existing_titles:
            continue

        is_initial = (i == 0 and tc.offset is None)
        session.add(StreamTitle(
            stream_id=stream.id,
            title=tc.title,
            started_at=title_time,
            is_initial=is_initial,
            created_at=datetime.now(timezone.utc),
        ))
        added += 1

    return added


def _parse_title_time(time_str: str, base_date: datetime) -> datetime:
    """Parse '12:45' → datetime on base_date."""
    try:
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        return base_date.replace(hour=h, minute=m, second=0, microsecond=0)
    except (ValueError, IndexError):
        return base_date


async def upsert_stream_games_from_page(
    session: AsyncSession,
    stream: Stream,
    page: StreamPageData,
    game_cache: dict[str, Game],
) -> int:
    """
    Update stream_games rows with per-game metrics from page.
    Creates new StreamGame rows if missing. Returns number of rows added/updated.
    """
    if not page.games:
        return 0

    existing_result = await session.execute(
        select(StreamGame).where(StreamGame.stream_id == stream.id)
    )
    existing_by_game_name = {sg.game.name: sg for sg in existing_result.scalars().all()}

    changed = 0
    for i, entry in enumerate(page.games):
        game = await get_or_create_game(session, game_cache, entry.name)
        sg = existing_by_game_name.get(entry.name)

        if sg is None:
            sg = StreamGame(stream_id=stream.id, game_id=game.id, position=i)
            session.add(sg)
            changed += 1
        elif sg.position != i:
            sg.position = i
            changed += 1

        # Update per-game metrics (only from page, never from list)
        if sg.avg_viewers != entry.avg_viewers:
            sg.avg_viewers = entry.avg_viewers
            changed += 1
        if sg.peak_viewers != entry.peak_viewers:
            sg.peak_viewers = entry.peak_viewers
            changed += 1
        if sg.duration_minutes != entry.duration_minutes:
            sg.duration_minutes = entry.duration_minutes
            changed += 1
        if sg.followers_gained != entry.followers_gained:
            sg.followers_gained = entry.followers_gained
            changed += 1

    return changed


async def increment_game_stats_hours(
    session: AsyncSession,
    page: StreamPageData,
    game_cache: dict[str, Game],
) -> int:
    """
    Increment GameStats.streamed_hours for each game played.
    Duration is taken from page (total stream duration, split equally among games
    since per-game duration_minutes is already in stream_games).
    Returns number of rows updated.
    """
    if not page.games:
        return 0

    updated = 0
    for entry in page.games:
        game = await get_or_create_game(session, game_cache, entry.name)

        result = await session.execute(select(GameStats).where(GameStats.game_id == game.id))
        gs = result.scalar_one_or_none()

        if gs is None:
            gs = GameStats(game_id=game.id)
            session.add(gs)

        # Add per-game duration (from stream_games data, not total)
        hours = entry.duration_minutes / 60.0 if entry.duration_minutes else 0
        gs.streamed_hours = (gs.streamed_hours or 0) + hours
        gs.synced_at = datetime.now(timezone.utc)
        updated += 1

    return updated


async def update_streams_count(session: AsyncSession) -> int:
    """Recomputes GameStats.streams_count from stream_games table."""
    from sqlalchemy import func

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


__all__ = [
    "increment_game_stats_hours",
    "upsert_stream_from_page",
    "upsert_stream_games_from_page",
    "upsert_stream_titles",
    "update_streams_count",
]
