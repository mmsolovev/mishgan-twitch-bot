from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config.settings import (
    ADMINS,
    ALLOWED_USERS,
    GAMES_SHEET_URL,
    RECOMMENDATIONS_BANNED_USERS,
    RECOMMENDATIONS_LIMIT,
    RECOMMENDATIONS_STREAMER_LOGIN,
)
from database.db import AsyncSessionLocal
from database.models import Game, GameMetadataIGDB, User, game_recommendations, streamer_games
from services.games_service import build_game_response, find_game_lookup
from pipeline.ingest.igdb_api import fetch_recommendation_metadata
from pipeline.load.load_recommendations import (
    add_recommendation as _db_add_recommendation,
    create_game as _db_create_game,
    create_game_with_igdb as _db_create_game_with_igdb,
    find_game_by_query as _db_find_game_by_query,
    find_user_recommendation as _db_find_user_recommendation,
    load_user_recommendations as _db_load_user_recommendations,
    remove_recommendation as _db_remove_recommendation,
    set_streamer_interested as _db_set_streamer_interested,
    add_igdb_note as _db_add_igdb_note,
)
from pipeline.transform.recommendations_transform import (
    normalize_recommendation_name as _tx_normalize_recommendation_name,
    normalize_user_login as _tx_normalize_user_login,
)


STATUS_UPCOMING = "upcoming"
STATUS_RELEASED = "released"
STATUS_STREAMED = "streamed"
STATUS_REJECTED = "rejected"
STATUS_NOT_FOUND = "not_found"

ACTIVE_RECOMMENDATION_STATUSES = {STATUS_UPCOMING, STATUS_RELEASED}
ADMIN_DELETE_ALL_MARKERS = {"*", "all", "все"}


@dataclass
class RecommendationSummary:
    id: int
    title: str
    status: str
    release_date: datetime | None
    steam_url: str | None
    igdb_score: float | None
    cover_url: str | None
    recommenders: list[str]
    votes_count: int


@dataclass
class RecommendationActionResult:
    outcome: str
    recommendation: RecommendationSummary | None
    message: str
    accepted: bool = False


def _normalize_user_login(value: str) -> str:
    return _tx_normalize_user_login(value)


def _doc_suffix() -> str:
    if GAMES_SHEET_URL:
        return f" | Все в листах РЕЛИЗЫ и СОВЕТЫ тут: {GAMES_SHEET_URL}"
    return ""


def build_recommendations_help_message() -> str:
    return (
        "MrDestructoid Написать в чат: !рек [точное название игры] — предложить игру для стрима. "
        "Название лучше писать максимально точное"
        + _doc_suffix()
    )


def normalize_recommendation_name(value: str) -> str:
    return _tx_normalize_recommendation_name(value)


async def _get_game_recommenders(session: AsyncSession, game_id: int) -> list[str]:
    result = await session.execute(
        select(User.login)
        .join(game_recommendations, game_recommendations.c.user_id == User.id)
        .where(game_recommendations.c.game_id == game_id)
    )
    return [row[0] for row in result.all()]


async def _get_recommenders_count(session: AsyncSession, game_id: int) -> int:
    result = await session.execute(
        select(game_recommendations).where(game_recommendations.c.game_id == game_id)
    )
    return len(result.all())


async def build_recommendation_summary(session: AsyncSession, game: Game) -> RecommendationSummary:
    recommenders = await _get_game_recommenders(session, game.id)
    votes_count = await _get_recommenders_count(session, game.id)

    release_date = None
    steam_url = None
    igdb_score = None
    cover_url = None

    if game.igdb_metadata:
        release_date = game.igdb_metadata.release_date
        steam_url = game.igdb_metadata.steam_url
        igdb_score = game.igdb_metadata.igdb_score
        cover_url = game.igdb_metadata.cover_url

    is_streamed = await _check_if_streamed(session, game.name)

    if is_streamed:
        status = STATUS_STREAMED
    elif release_date and release_date > datetime.now(timezone.utc):
        status = STATUS_UPCOMING
    else:
        status = STATUS_RELEASED

    return RecommendationSummary(
        id=game.id,
        title=game.name,
        status=status,
        release_date=release_date,
        steam_url=steam_url,
        igdb_score=igdb_score,
        cover_url=cover_url,
        recommenders=recommenders,
        votes_count=votes_count,
    )


async def _check_if_streamed(session: AsyncSession, game_name: str) -> bool:
    from database.models import StreamGame
    result = await session.execute(
        select(StreamGame)
        .join(Game, Game.id == StreamGame.game_id)
        .where(Game.name == game_name)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _user_is_privileged(user_login: str) -> bool:
    normalized_login = _normalize_user_login(user_login)
    return normalized_login in {login.casefold() for login in ADMINS} or normalized_login in {
        login.casefold() for login in ALLOWED_USERS
    }


def _user_is_admin(user_login: str) -> bool:
    return _normalize_user_login(user_login) in {login.casefold() for login in ADMINS}


def can_recommend_as_streamer(user_login: str) -> bool:
    return _user_is_admin(user_login)


def _user_is_banned(user_login: str) -> bool:
    return _normalize_user_login(user_login) in RECOMMENDATIONS_BANNED_USERS


def _is_streamer_recommendation(user_login: str) -> bool:
    return _normalize_user_login(user_login) == _normalize_user_login(RECOMMENDATIONS_STREAMER_LOGIN)


async def _find_streamed_game_match(query: str):
    return await find_game_lookup(query)


async def _enforce_user_limit(session: AsyncSession, user_login: str) -> str | None:
    if _user_is_privileged(user_login):
        return None
    active_votes = await _db_load_user_recommendations(session, user_login)
    if len(active_votes) < RECOMMENDATIONS_LIMIT:
        return None
    oldest = active_votes[0]
    removed_title = oldest.get("name", "")
    await _db_remove_recommendation(session, oldest["user_id"], oldest["game_id"])
    return removed_title


def _format_limit_suffix(removed_title: str | None) -> str:
    if not removed_title:
        return ""
    return f" Самая старая рекомендация «{removed_title}» убрана из списка."


def _make_result(outcome: str, message: str, recommendation: RecommendationSummary | None = None, accepted: bool = False) -> RecommendationActionResult:
    return RecommendationActionResult(outcome=outcome, recommendation=recommendation, message=message, accepted=accepted)


def _make_fake_add_result(query: str) -> RecommendationActionResult:
    return _make_result(
        outcome="created",
        message=f"Игра «{query.strip()}» занесена в таблицу.{_doc_suffix()}",
        accepted=True,
    )


def _make_fake_delete_result(query: str | None = None) -> RecommendationActionResult:
    if query:
        return _make_result(outcome="deleted", message=f"Рекомендация по игре «{query.strip()}» удалена.")
    return _make_result(outcome="deleted", message="Последняя рекомендация удалена.")


def _format_add_message(title: str, removed_title: str | None = None, already_existing: bool = False) -> str:
    prefix = (
        f"Игра «{title}» уже есть в списке, рекомендация добавлена."
        if already_existing
        else f"Игра «{title}» занесена в список рекомендаций."
    )
    return prefix + _format_limit_suffix(removed_title)


async def recommend_game(query: str, user_login: str, user_display_name: str) -> RecommendationActionResult:
    normalized_query = normalize_recommendation_name(query)
    if not normalized_query:
        return _make_result("invalid", build_recommendations_help_message())

    if _user_is_banned(user_login):
        return _make_fake_add_result(query)

    streamed_match = await _find_streamed_game_match(query)
    streamer_mode = _is_streamer_recommendation(user_login)
    if streamed_match is not None and not streamer_mode:
        return _make_result("already_streamed", f"Уже была на стримах {await build_game_response(query)}")

    async with AsyncSessionLocal() as session:
        existing = await _db_find_game_by_query(session, query)
        if existing:
            user_rec = await _db_find_user_recommendation(session, existing.id, user_login)
            if user_rec:
                return _make_result("duplicate_vote", f"Уже рекомендована «{existing.name}».")

            removed_title = await _enforce_user_limit(session, user_login)
            await _db_add_recommendation(session, existing, user_login)
            await session.commit()
            summary = await build_recommendation_summary(session, existing)
            return _make_result(
                "voted",
                _format_add_message(summary.title, removed_title=removed_title, already_existing=True),
                recommendation=summary,
                accepted=True,
            )

        metadata = await fetch_recommendation_metadata(query)
        if metadata is None:
            return _make_result(
                "not_found",
                f"Не удалось найти игру «{query}». Название лучше писать максимально точное.",
            )

        metadata_streamed_match = await _find_streamed_game_match(metadata.title)
        if metadata_streamed_match is not None and not streamer_mode:
            return _make_result("already_streamed", f"Уже была на стримах {await build_game_response(query)}")

        existing = await _db_find_game_by_query(session, metadata.title)
        if existing:
            user_rec = await _db_find_user_recommendation(session, existing.id, user_login)
            if user_rec:
                return _make_result("duplicate_vote", f"Уже рекомендована «{existing.name}».")

            removed_title = await _enforce_user_limit(session, user_login)
            await _db_add_recommendation(session, existing, user_login)
            await session.commit()
            summary = await build_recommendation_summary(session, existing)
            return _make_result(
                "voted",
                _format_add_message(summary.title, removed_title=removed_title, already_existing=True),
                recommendation=summary,
                accepted=True,
            )

        removed_title = await _enforce_user_limit(session, user_login)
        game = await _db_create_game_with_igdb(
            session,
            name=metadata.title,
            igdb_id=metadata.source_game_id,
            release_date=metadata.release_date,
            steam_url=metadata.steam_url,
            igdb_score=_parse_igdb_score(metadata.rating_text),
            description_ru=metadata.description_short,
            cover_url=metadata.cover_url,
            raw_payload=metadata.source_payload,
        )
        await _db_add_recommendation(session, game, user_login)
        if metadata.source_name == "igdb":
            await _db_add_igdb_note(session, game.id, user_login="igdb")

        if _is_streamer_recommendation(user_login):
            await _db_set_streamer_interested(session, game.id, user_login, True)

        await session.commit()
        summary = await build_recommendation_summary(session, game)
        return _make_result(
            "created",
            _format_add_message(summary.title, removed_title=removed_title),
            recommendation=summary,
            accepted=True,
        )


def _parse_igdb_score(rating_text: str | None) -> float | None:
    if not rating_text:
        return None
    import re
    match = re.search(r"(\d+(?:\.\d+)?)", rating_text)
    if match:
        return float(match.group(1))
    return None


async def delete_own_last_recommendation(user_login: str) -> RecommendationActionResult:
    if _user_is_banned(user_login):
        return _make_fake_delete_result()

    async with AsyncSessionLocal() as session:
        user = await _get_user(session, user_login)
        if not user:
            return _make_result("not_found", "Нет активных рекомендаций.")

        result = await session.execute(
            select(game_recommendations, Game)
            .join(Game, Game.id == game_recommendations.c.game_id)
            .where(game_recommendations.c.user_id == user.id)
            .order_by(game_recommendations.c.created_at.desc())
            .limit(1)
        )
        row = result.first()
        if not row:
            return _make_result("not_found", "Нет активных рекомендаций.")

        game_name = row.Game.name
        user_id = row.game_recommendations.user_id
        game_id = row.game_recommendations.game_id
        await _db_remove_recommendation(session, user_id, game_id)
        await session.commit()
        return _make_result("deleted", f"Последняя рекомендация по игре «{game_name}» удалена.")


async def delete_own_recommendation_by_title(query: str, user_login: str) -> RecommendationActionResult:
    if not normalize_recommendation_name(query):
        return _make_result("invalid", "Напиши: !рек - [название игры]")

    if _user_is_banned(user_login):
        return _make_fake_delete_result(query)

    async with AsyncSessionLocal() as session:
        game = await _db_find_game_by_query(session, query)
        if not game:
            return _make_result("not_found", f"Не нашел рекомендацию для «{query}».")

        user_rec = await _db_find_user_recommendation(session, game.id, user_login)
        if not user_rec:
            return _make_result("not_found", f"Не нашел рекомендацию для «{game.name}».")

        await _db_remove_recommendation(session, user_rec["user_id"], user_rec["game_id"])
        await session.commit()
        return _make_result("deleted", f"Рекомендация по игре «{game.name}» удалена.")


async def admin_delete_recommendations(target_user: str, query: str | None, actor_login: str) -> RecommendationActionResult:
    if not _user_is_admin(actor_login):
        return _make_result("forbidden", "Доступ к команде ограничен.")

    if _user_is_banned(actor_login):
        return _make_fake_delete_result(query)

    normalized_target = _normalize_user_login(target_user)
    if not normalized_target:
        return _make_result("invalid", "Напиши: !рек -- [ник] [название игры] или !рек -- [ник]")

    async with AsyncSessionLocal() as session:
        if normalized_target in ADMIN_DELETE_ALL_MARKERS:
            if not query:
                return _make_result("invalid", "Для удаления игры целиком напиши: !рек -- * [название игры]")

            game = await _db_find_game_by_query(session, query)
            if not game:
                return _make_result("not_found", f"Игра «{query}» в рекомендациях не найдена.")

            votes_count = await _get_recommenders_count(session, game.id)
            await session.delete(game)
            await session.flush()
            await session.commit()
            return _make_result("deleted", f"Игра «{game.name}» удалена из рекомендаций целиком. Удалено голосов: {votes_count}.")

        if not query:
            user = await _get_user(session, normalized_target)
            if not user:
                return _make_result("not_found", f"У пользователя {target_user} нет активных рекомендаций.")

            votes = await _db_load_user_recommendations(session, normalized_target)
            if not votes:
                return _make_result("not_found", f"У пользователя {target_user} нет активных рекомендаций.")

            removed_titles = []
            for vote in votes:
                removed_titles.append(vote.get("name", ""))
                await _db_remove_recommendation(session, vote["user_id"], vote["game_id"])

            await session.commit()
            return _make_result(
                "deleted",
                f"У пользователя {target_user} удалено рекомендаций: {len(removed_titles)}. Игры: {', '.join(removed_titles[:5])}",
            )

        game = await _db_find_game_by_query(session, query)
        if not game:
            return _make_result("not_found", f"Игра «{query}» в рекомендациях не найдена.")

        user_rec = await _db_find_user_recommendation(session, game.id, normalized_target)
        if not user_rec:
            return _make_result("not_found", f"У пользователя {target_user} нет рекомендации для «{game.name}».")

        await _db_remove_recommendation(session, user_rec["user_id"], user_rec["game_id"])
        await session.commit()
        return _make_result("deleted", f"У пользователя {target_user} удалена рекомендация по игре «{game.name}».")


async def _get_user(session: AsyncSession, login: str) -> User | None:
    result = await session.execute(select(User).where(User.login == _normalize_user_login(login)))
    return result.scalar_one_or_none()


async def refresh_recommendation_lifecycle() -> int:
    return 0
