from __future__ import annotations

"""
Load layer: writes targeting `stream_games` table (StreamGame).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Game, Stream, StreamGame
from pipeline.load.load_games import get_or_create_game


def unique_in_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


async def sync_stream_games(session: AsyncSession, stream: Stream, game_names: list[str], game_cache: dict[str, Game]) -> bool:
    """
    Ensures Stream.stream_games match the given ordered list of names.
    Returns True if association changed.
    """
    desired_names = unique_in_order([n for n in game_names if n])
    desired_set = set(desired_names)
    changed = False

    existing_by_name = {sg.game.name: sg for sg in stream.stream_games}

    for game_name, stream_game in list(existing_by_name.items()):
        if game_name not in desired_set:
            await session.delete(stream_game)
            changed = True

    existing_by_name = {sg.game.name: sg for sg in stream.stream_games if sg.game.name in desired_set}

    for position, game_name in enumerate(desired_names):
        game = await get_or_create_game(session, game_cache, game_name)
        stream_game = existing_by_name.get(game_name)
        if stream_game is None:
            session.add(StreamGame(stream_id=stream.id, game_id=game.id, position=position))
            changed = True
        elif stream_game.position != position:
            stream_game.position = position
            changed = True

    return changed


__all__ = ["sync_stream_games", "unique_in_order"]
