from __future__ import annotations

"""
Load layer: writes targeting `games` table (Game).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Game, GameAlias


async def get_or_create_game(
    session: AsyncSession, game_cache: dict[str, Game], name: str
) -> Game:
    game = game_cache.get(name)
    if game is not None:
        return game

    result = await session.execute(select(Game).where(Game.name == name))
    game = result.scalar_one_or_none()

    if game is None:
        slug = name.lower().replace(" ", "-")
        game = Game(name=name, slug=slug, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        session.add(game)
        await session.flush()

        normalized = name.lower().strip()
        alias = GameAlias(
            game_id=game.id,
            alias=name,
            normalized_alias=normalized,
            is_primary=True,
            source="manual",
            created_at=datetime.now(timezone.utc),
        )
        session.add(alias)
        await session.flush()

    game_cache[name] = game
    return game


__all__ = ["get_or_create_game"]
