from __future__ import annotations

"""
Load layer: writes stream-page data to DB (streams, stream_titles,
stream_games per-game metrics, game_stats aggregation).
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Game, GameStats, Stream, StreamGame, StreamTitle
from pipeline.ingest.twitchtracker_parser import StreamGameEntry, StreamPageData
from pipeline.load.load_games import get_or_create_game


def _log(message: str) -> None:
    print(message, flush=True)


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
            created_at=datetime.utcnow(),
        )
        session.add(stream)
        await session.flush()
        created = True
        _log(f"  Created stream id={stream.id}, external_id={stream.external_id}")
    else:
        _log(f"  Found existing stream id={stream.id}")

    changed = created

    if external_id and stream.external_id != external_id:
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

    stream.updated_at = datetime.utcnow()

    return stream, created


async def upsert_stream_titles(
    session: AsyncSession,
    stream: Stream,
    page: StreamPageData,
) -> int:
    """Insert new title changes. Returns number of titles added."""
    if not page.title_changes:
        _log("  No title changes parsed")
        return 0

    _log(f"  Processing {len(page.title_changes)} title change(s), stream_id={stream.id}")

    existing_result = await session.execute(
        select(StreamTitle.title, StreamTitle.started_at).where(StreamTitle.stream_id == stream.id)
    )
    existing_titles = {(row[0], row[1]) for row in existing_result.all()}
    _log(f"  Existing titles in DB: {len(existing_titles)}")

    added = 0
    for i, tc in enumerate(page.title_changes):
        title_time = _parse_title_time(tc.time, page.date)
        key = (tc.title, title_time)
        if key in existing_titles:
            _log(f"  Skipping duplicate title: {tc.title[:50]} @ {title_time}")
            continue

        is_initial = (i == 0 and tc.offset is None)
        st = StreamTitle(
            stream_id=stream.id,
            title=tc.title,
            started_at=title_time,
            is_initial=is_initial,
            created_at=datetime.utcnow(),
        )
        session.add(st)
        added += 1
        _log(f"  + Title: {tc.title[:60]} @ {title_time} (initial={is_initial})")

    _log(f"  Titles added this run: {added}")
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
    Creates new StreamGame rows if missing. Returns number of rows changed.
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
            _log(f"  + StreamGame: {entry.name} (id={game.id})")
        elif sg.position != i:
            sg.position = i
            changed += 1

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
) -> list[tuple[str, float]]:
    """
    Increment GameStats.streamed_hours and update last_stream for each game played.
    Returns list of (game_name, hours_added).
    """
    if not page.games:
        return []

    stream_date = page.started_at or page.date

    results: list[tuple[str, float]] = []
    for entry in page.games:
        game = await get_or_create_game(session, game_cache, entry.name)

        result = await session.execute(select(GameStats).where(GameStats.game_id == game.id))
        gs = result.scalar_one_or_none()

        if gs is None:
            gs = GameStats(game_id=game.id)
            session.add(gs)

        hours = entry.duration_minutes / 60.0 if entry.duration_minutes else 0
        old_hours = gs.streamed_hours or 0
        gs.streamed_hours = old_hours + hours
        gs.synced_at = datetime.utcnow()

        # Update last_stream if this stream is newer
        if stream_date and (gs.last_stream is None or stream_date > gs.last_stream):
            gs.last_stream = stream_date

        results.append((entry.name, hours))

    return results


async def update_streams_count(
    session: AsyncSession,
    only_game_ids: set[int] | None = None,
) -> list[tuple[str, int, int]]:
    """
    Recomputes GameStats.streams_count from stream_games table.
    If only_game_ids is provided, only those games are updated.
    Returns list of (game_name, old_count, new_count) for updated rows.
    """
    from sqlalchemy import func

    result = await session.execute(
        select(StreamGame.game_id, func.count(StreamGame.stream_id)).group_by(StreamGame.game_id)
    )
    counts_by_game_id = dict(result.all())

    # Load game names
    game_result = await session.execute(select(Game.id, Game.name))
    id_to_name = {int(row[0]): row[1] for row in game_result.all()}

    q = select(GameStats)
    if only_game_ids:
        q = q.where(GameStats.game_id.in_([int(i) for i in only_game_ids if int(i) > 0]))

    updated: list[tuple[str, int, int]] = []
    result = await session.execute(q)
    for stats in result.scalars().all():
        new_value = int(counts_by_game_id.get(stats.game_id, 0))
        old_value = int(stats.streams_count or 0)
        if old_value != new_value:
            game_name = id_to_name.get(int(stats.game_id), f"#{stats.game_id}")
            stats.streams_count = new_value
            updated.append((game_name, old_value, new_value))

    return updated


__all__ = [
    "increment_game_stats_hours",
    "upsert_stream_from_page",
    "upsert_stream_games_from_page",
    "upsert_stream_titles",
    "update_streams_count",
]
