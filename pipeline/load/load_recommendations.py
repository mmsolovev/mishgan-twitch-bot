from __future__ import annotations

"""
Load layer: writes targeting `games`, `game_metadata_igdb`, `game_recommendations`,
`streamer_games` tables — replaces old RecommendedGame / RecommendedGameVote.
"""

from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import (
    Game,
    GameMetadataIGDB,
    GameAlias,
    User,
    game_recommendations,
    streamer_games,
)
from pipeline.transform.recommendations_transform import normalize_recommendation_name, normalize_user_login


ACTIVE_RECOMMENDATION_STATUSES = {"upcoming", "released"}


async def existing_recommendation_titles(session: AsyncSession) -> set[str]:
    result = await session.execute(select(Game.name))
    return {str(r[0]) for r in result.all() if r and r[0]}


async def find_game_by_normalized_name(session: AsyncSession, normalized_name: str) -> Game | None:
    if not normalized_name:
        return None
    result = await session.execute(
        select(Game).join(GameAlias, GameAlias.game_id == Game.id)
        .where(GameAlias.normalized_alias == normalized_name)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_game_by_query(session: AsyncSession, query: str) -> Game | None:
    normalized = normalize_recommendation_name(query)
    if not normalized:
        return None
    result = await session.execute(
        select(Game).join(GameAlias, GameAlias.game_id == Game.id)
        .where(GameAlias.normalized_alias == normalized)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_game_with_igdb(session: AsyncSession, normalized_name: str) -> Game | None:
    if not normalized_name:
        return None
    result = await session.execute(
        select(Game)
        .options(selectinload(Game.igdb_metadata))
        .join(GameAlias, GameAlias.game_id == Game.id)
        .where(GameAlias.normalized_alias == normalized_name)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def add_recommendation(
    session: AsyncSession,
    game: Game,
    user_login: str,
    *,
    note: str | None = None,
) -> bool:
    normalized_login = normalize_user_login(user_login)
    if not normalized_login:
        raise ValueError("user_login is required")

    user = await _get_or_create_user(session, normalized_login)

    existing = await session.execute(
        select(game_recommendations).where(
            game_recommendations.c.user_id == user.id,
            game_recommendations.c.game_id == game.id,
        )
    )
    if existing.first():
        return False

    session.execute(
        game_recommendations.insert().values(user_id=user.id, game_id=game.id, recommendation_note=note)
    )
    await session.flush()
    return True


async def create_game(
    session: AsyncSession,
    name: str,
    *,
    slug: str | None = None,
) -> Game:
    normalized = normalize_recommendation_name(name)
    if not normalized:
        raise ValueError("Game name is empty")

    now = datetime.now(timezone.utc)
    if not slug:
        slug = name.lower().replace(" ", "-")

    game = Game(name=name.strip(), slug=slug, created_at=now, updated_at=now)
    session.add(game)
    await session.flush()

    alias = GameAlias(
        game_id=game.id,
        alias=name.strip(),
        normalized_alias=normalized,
        is_primary=True,
        source="manual",
        created_at=now,
    )
    session.add(alias)
    await session.flush()
    return game


async def create_game_with_igdb(
    session: AsyncSession,
    *,
    name: str,
    igdb_id: str | None = None,
    release_date: datetime | None = None,
    steam_url: str | None = None,
    igdb_score: float | None = None,
    description_ru: str | None = None,
    cover_url: str | None = None,
    raw_payload: str | None = None,
) -> Game:
    normalized = normalize_recommendation_name(name)
    if not normalized:
        raise ValueError("Game name is empty")

    now = datetime.now(timezone.utc)
    slug = name.lower().replace(" ", "-")

    game = Game(name=name.strip(), slug=slug, created_at=now, updated_at=now)
    session.add(game)
    await session.flush()

    alias = GameAlias(
        game_id=game.id,
        alias=name.strip(),
        normalized_alias=normalized,
        is_primary=True,
        source="igdb",
        created_at=now,
    )
    session.add(alias)

    import json as _json
    igdb_meta = GameMetadataIGDB(
        game_id=game.id,
        igdb_id=igdb_id or f"pending-{game.id}",
        release_date=release_date,
        steam_url=steam_url,
        igdb_score=igdb_score,
        description_ru=description_ru,
        cover_url=cover_url,
        raw_payload=_json.loads(raw_payload) if raw_payload else None,
        synced_at=now,
    )
    session.add(igdb_meta)
    await session.flush()
    return game


async def add_igdb_note(session: AsyncSession, game_id: int, user_login: str = "igdb") -> None:
    user = await _get_or_create_user(session, user_login)
    existing = await session.execute(
        select(game_recommendations).where(
            game_recommendations.c.user_id == user.id,
            game_recommendations.c.game_id == game_id,
        )
    )
    if not existing.first():
        session.execute(
            game_recommendations.insert().values(user_id=user.id, game_id=game_id, recommendation_note="Игра популярна")
        )
        await session.flush()


async def find_existing_game(
    session: AsyncSession,
    *,
    query: str,
    metadata_title: str | None = None,
) -> Game | None:
    game = await find_game_by_query(session, query)
    if game:
        return game
    if metadata_title:
        game = await find_game_by_query(session, metadata_title)
        if game:
            return game
    return None


async def find_user_recommendation(session: AsyncSession, game_id: int, user_login: str) -> dict | None:
    user = await _get_or_create_user(session, normalize_user_login(user_login))
    result = await session.execute(
        select(game_recommendations).where(
            game_recommendations.c.user_id == user.id,
            game_recommendations.c.game_id == game_id,
        )
    )
    row = result.first()
    return dict(row._mapping) if row else None


async def load_user_recommendations(session: AsyncSession, user_login: str) -> list[dict]:
    user = await _get_or_create_user(session, normalize_user_login(user_login))
    result = await session.execute(
        select(game_recommendations, Game)
        .join(Game, Game.id == game_recommendations.c.game_id)
        .where(game_recommendations.c.user_id == user.id)
        .order_by(game_recommendations.c.created_at.asc())
    )
    return [dict(row._mapping) for row in result.all()]


async def remove_recommendation(session: AsyncSession, user_id: int, game_id: int) -> None:
    await session.execute(
        delete(game_recommendations).where(
            game_recommendations.c.user_id == user_id,
            game_recommendations.c.game_id == game_id,
        )
    )
    await session.flush()

    remaining = await session.execute(
        select(game_recommendations).where(game_recommendations.c.game_id == game_id).limit(1)
    )
    if not remaining.first():
        game = await session.get(Game, game_id)
        if game:
            await session.delete(game)
            await session.flush()


async def iter_games_missing_short_description(session: AsyncSession, *, limit: int = 0) -> list[Game]:
    q = (
        select(Game)
        .outerjoin(GameMetadataIGDB, Game.id == GameMetadataIGDB.game_id)
        .where(
            (GameMetadataIGDB.description_ru.is_(None)) | (GameMetadataIGDB.description_ru == "")
        )
    )
    if int(limit) > 0:
        q = q.limit(int(limit))
    result = await session.execute(q)
    return list(result.scalars().all())


async def set_game_short_description(session: AsyncSession, game: Game, description_short: str) -> bool:
    value = (description_short or "").strip()
    if not value:
        return False

    result = await session.execute(select(GameMetadataIGDB).where(GameMetadataIGDB.game_id == game.id))
    meta = result.scalar_one_or_none()
    if meta is None:
        return False
    if (meta.description_ru or "").strip() == value:
        return False
    meta.description_ru = value
    session.add(meta)
    return True


async def set_streamer_interested(session: AsyncSession, game_id: int, user_login: str, interested: bool) -> bool:
    user = await _get_or_create_user(session, normalize_user_login(user_login))
    result = await session.execute(
        select(streamer_games).where(
            streamer_games.c.streamer_id == user.id,
            streamer_games.c.game_id == game_id,
        )
    )
    row = result.first()
    if row is None:
        session.execute(
            streamer_games.insert().values(
                streamer_id=user.id, game_id=game_id, interested=interested,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()
        return True
    if bool(row.interested) != interested:
        await session.execute(
            streamer_games.update()
            .where(
                streamer_games.c.streamer_id == user.id,
                streamer_games.c.game_id == game_id,
            )
            .values(interested=interested, updated_at=datetime.now(timezone.utc))
        )
        await session.flush()
        return True
    return False


async def get_upcoming_games(session: AsyncSession) -> list[Game]:
    result = await session.execute(
        select(Game)
        .options(selectinload(Game.igdb_metadata))
        .join(GameMetadataIGDB, Game.id == GameMetadataIGDB.game_id)
        .where(GameMetadataIGDB.release_date > datetime.now(timezone.utc))
    )
    return list(result.scalars().all())


async def update_release_dates(session: AsyncSession, games_to_update: list[Game]) -> int:
    if not games_to_update:
        return 0
    now = datetime.now(timezone.utc)
    for game in games_to_update:
        game.updated_at = now
        session.add(game)
    await session.flush()
    return len(games_to_update)


async def _get_or_create_user(session: AsyncSession, login: str) -> User:
    result = await session.execute(select(User).where(User.login == login))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(login=login, display_name=f"@{login}")
        session.add(user)
        await session.flush()
    return user


__all__ = [
    "ACTIVE_RECOMMENDATION_STATUSES",
    "add_igdb_note",
    "add_recommendation",
    "create_game",
    "create_game_with_igdb",
    "existing_recommendation_titles",
    "find_existing_game",
    "find_game_by_normalized_name",
    "find_game_by_query",
    "find_game_with_igdb",
    "find_user_recommendation",
    "get_upcoming_games",
    "iter_games_missing_short_description",
    "load_user_recommendations",
    "remove_recommendation",
    "set_game_short_description",
    "set_streamer_interested",
    "update_release_dates",
]
