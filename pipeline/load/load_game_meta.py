from __future__ import annotations

"""
Load layer: writes targeting `game_metadata_igdb` and `game_metadata_hltb` tables.
"""

from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import Game, GameMetadataIGDB, GameMetadataHLTB


@dataclass(frozen=True, slots=True)
class EnrichmentCandidate:
    game_id: int
    game_name: str
    has_hltb: bool
    has_igdb: bool


async def select_igdb_enrichment_candidates(
    session: AsyncSession,
    *,
    only_game_id: int = 0,
    limit: int = 0,
) -> list[EnrichmentCandidate]:
    """
    Finds games where IGDB metadata is missing or incomplete.
    """
    q = (
        select(Game)
        .outerjoin(GameMetadataIGDB, Game.id == GameMetadataIGDB.game_id)
        .where(
            or_(
                GameMetadataIGDB.igdb_id.is_(None),
                GameMetadataIGDB.steam_url.is_(None),
                GameMetadataIGDB.steam_url == "",
                GameMetadataIGDB.description_ru.is_(None),
                GameMetadataIGDB.release_date.is_(None),
            )
        )
        .order_by(Game.id)
    )

    if int(only_game_id) > 0:
        q = q.where(Game.id == int(only_game_id))

    if int(limit) > 0:
        q = q.limit(int(limit))

    result = await session.execute(q)
    games = result.scalars().all()

    candidates = []
    for game in games:
        hltb_result = await session.execute(
            select(GameMetadataHLTB).where(GameMetadataHLTB.game_id == game.id)
        )
        has_hltb = hltb_result.scalar_one_or_none() is not None
        candidates.append(EnrichmentCandidate(game_id=int(game.id), game_name=game.name, has_hltb=has_hltb, has_igdb=False))
    return candidates


async def apply_igdb_patch(session: AsyncSession, *, game_id: int, igdb_id: str | None = None, patch: dict) -> bool:
    """
    Apply patch fields to GameMetadataIGDB row. Creates row if missing.
    Returns True if anything changed.
    """
    if not patch:
        return False

    result = await session.execute(select(GameMetadataIGDB).where(GameMetadataIGDB.game_id == game_id))
    row = result.scalar_one_or_none()
    created = False
    if row is None:
        row = GameMetadataIGDB(game_id=game_id, igdb_id=igdb_id or f"pending-{game_id}")
        session.add(row)
        await session.flush()
        created = True

    changed = created
    for k, v in patch.items():
        if hasattr(row, k) and getattr(row, k) != v:
            setattr(row, k, v)
            changed = True

    if changed:
        session.add(row)
    return changed


async def apply_hltb_patch(session: AsyncSession, *, game_id: int, patch: dict) -> bool:
    """
    Apply patch fields to GameMetadataHLTB row. Creates row if missing.
    Returns True if anything changed.
    """
    if not patch:
        return False

    result = await session.execute(select(GameMetadataHLTB).where(GameMetadataHLTB.game_id == game_id))
    row = result.scalar_one_or_none()
    created = False
    if row is None:
        row = GameMetadataHLTB(game_id=game_id)
        session.add(row)
        await session.flush()
        created = True

    changed = created
    for k, v in patch.items():
        if hasattr(row, k) and getattr(row, k) != v:
            setattr(row, k, v)
            changed = True

    if changed:
        session.add(row)
    return changed


__all__ = [
    "EnrichmentCandidate",
    "apply_hltb_patch",
    "apply_igdb_patch",
    "select_igdb_enrichment_candidates",
]
