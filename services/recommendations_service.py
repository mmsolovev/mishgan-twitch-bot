import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import joinedload

from config.settings import (
    ADMINS,
    ALLOWED_USERS,
    GAMES_SHEET_URL,
    RECOMMENDATIONS_BANNED_USERS,
    RECOMMENDATIONS_LIMIT,
    RECOMMENDATIONS_STREAMER_LOGIN,
)
from database.db import SessionLocal
from database.models import Game, RecommendedGame, RecommendedGameVote
from services.games_service import build_game_response, find_game_lookup
from services.recommendation_metadata_service import fetch_recommendation_metadata


STATUS_UPCOMING = "upcoming"
STATUS_RELEASED = "released"
STATUS_STREAMED = "streamed"
STATUS_REJECTED = "rejected"
STATUS_NOT_FOUND = "not_found"

RELEASE_PRECISION_UNKNOWN = "unknown"
RELEASE_PRECISION_DAY = "day"
RELEASE_PRECISION_MINUTE = "minute"
ACTIVE_RECOMMENDATION_STATUSES = {STATUS_UPCOMING, STATUS_RELEASED}
ADMIN_DELETE_ALL_MARKERS = {"*", "all", "все"}


@dataclass
class RecommendationSummary:
    id: int
    title: str
    status: str
    release_date: datetime | None
    steam_url: str | None
    rating_text: str | None
    platforms_text: str | None
    genres_text: str | None
    recommenders: list[str]
    votes_count: int


@dataclass
class RecommendationActionResult:
    outcome: str
    recommendation: RecommendationSummary | None
    message: str
    accepted: bool = False


def _normalize_user_login(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _doc_suffix() -> str:
    if GAMES_SHEET_URL:
        return f" Таблица: {GAMES_SHEET_URL}"
    return ""


def build_recommendations_help_message() -> str:
    return (
        "Написать в чат: !рек [точное название игры] — предложить игру для стрима. "
        "Пиши название максимально точно."
        + _doc_suffix()
    )


def normalize_recommendation_name(value: str) -> str:
    normalized = " ".join((value or "").casefold().split())
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return " ".join(normalized.split())


def find_recommendation_by_query(session, query: str) -> RecommendedGame | None:
    normalized_name = normalize_recommendation_name(query)
    if not normalized_name:
        return None

    return (
        session.query(RecommendedGame)
        .options(joinedload(RecommendedGame.votes))
        .filter_by(normalized_name=normalized_name)
        .first()
    )


def add_vote(
    session,
    recommendation: RecommendedGame,
    user_login: str,
    user_display_name: str,
    created_at: datetime | None = None,
) -> bool:
    normalized_login = _normalize_user_login(user_login)
    if not normalized_login:
        raise ValueError("user_login is required")

    existing_vote = (
        session.query(RecommendedGameVote)
        .filter_by(recommended_game_id=recommendation.id, user_login=normalized_login)
        .first()
    )
    if existing_vote:
        return False

    now = created_at or datetime.utcnow()
    vote = RecommendedGameVote(
        recommended_game=recommendation,
        user_login=normalized_login,
        user_display_name=(user_display_name or user_login or "").strip() or normalized_login,
        created_at=now,
    )
    session.add(vote)
    recommendation.updated_at = now
    session.flush()
    return True


def create_recommendation(
    session,
    query_name: str,
    title: str,
    *,
    release_date: datetime | None = None,
    release_precision: str = RELEASE_PRECISION_UNKNOWN,
    description_short: str | None = None,
    steam_url: str | None = None,
    rating_text: str | None = None,
    platforms_text: str | None = None,
    genres_text: str | None = None,
    cover_url: str | None = None,
    source_name: str | None = None,
    source_game_id: str | None = None,
    source_payload: str | None = None,
    status: str | None = None,
) -> RecommendedGame:
    normalized_name = normalize_recommendation_name(title or query_name)
    if not normalized_name:
        raise ValueError("Recommendation query/title is empty")

    now = datetime.utcnow()
    recommendation = RecommendedGame(
        query_name=(query_name or title).strip(),
        normalized_name=normalized_name,
        title=title.strip(),
        status=status or determine_recommendation_status(release_date=release_date),
        release_date=release_date,
        release_precision=release_precision,
        description_short=description_short,
        steam_url=steam_url,
        rating_text=rating_text,
        platforms_text=platforms_text,
        genres_text=genres_text,
        cover_url=cover_url,
        source_name=source_name,
        source_game_id=source_game_id,
        source_payload=source_payload,
        created_at=now,
        updated_at=now,
        last_checked_at=now,
    )
    session.add(recommendation)
    session.flush()
    return recommendation


def determine_recommendation_status(
    *,
    release_date: datetime | None,
    matched_game: Game | None = None,
    streamer_interested: bool = False,
) -> str:
    if matched_game is not None and streamer_interested:
        if release_date and release_date > datetime.utcnow():
            return STATUS_UPCOMING
        return STATUS_STREAMED

    if matched_game is not None:
        return STATUS_STREAMED

    if release_date is None:
        return STATUS_RELEASED

    return STATUS_UPCOMING if release_date > datetime.utcnow() else STATUS_RELEASED


def sync_recommendation_matches(session) -> int:
    updated_count = 0

    recommendations = (
        session.query(RecommendedGame)
        .filter(RecommendedGame.status.in_([STATUS_UPCOMING, STATUS_RELEASED, STATUS_STREAMED]))
        .all()
    )

    games_by_name = {
        normalize_recommendation_name(game.name): game
        for game in session.query(Game).all()
        if game.name
    }

    for recommendation in recommendations:
        matched_game = games_by_name.get(recommendation.normalized_name)
        previous_matched_game_id = recommendation.matched_game_id
        previous_status = recommendation.status

        recommendation.matched_game = matched_game
        recommendation.status = determine_recommendation_status(
            release_date=recommendation.release_date,
            matched_game=matched_game,
            streamer_interested=bool(recommendation.streamer_interested),
        )

        if recommendation.matched_game_id != previous_matched_game_id or recommendation.status != previous_status:
            recommendation.updated_at = datetime.utcnow()
            updated_count += 1

    if updated_count:
        session.flush()

    return updated_count


def refresh_recommendation_lifecycle() -> int:
    session = SessionLocal()
    try:
        updated_count = sync_recommendation_matches(session)
        session.commit()
        return updated_count
    finally:
        session.close()


def build_recommendation_summary(recommendation: RecommendedGame) -> RecommendationSummary:
    recommenders = [vote.user_display_name for vote in recommendation.votes]
    return RecommendationSummary(
        id=recommendation.id,
        title=recommendation.title,
        status=recommendation.status,
        release_date=recommendation.release_date,
        steam_url=recommendation.steam_url,
        rating_text=recommendation.rating_text,
        platforms_text=recommendation.platforms_text,
        genres_text=recommendation.genres_text,
        recommenders=recommenders,
        votes_count=len(recommenders),
    )


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


def _find_existing_recommendation(
    session,
    query: str,
    metadata_title: str | None = None,
    source_name: str | None = None,
    source_game_id: str | None = None,
) -> RecommendedGame | None:
    recommendation = find_recommendation_by_query(session, query)
    if recommendation:
        return recommendation

    if metadata_title:
        recommendation = find_recommendation_by_query(session, metadata_title)
        if recommendation:
            return recommendation

    if source_name and source_game_id:
        return (
            session.query(RecommendedGame)
            .options(joinedload(RecommendedGame.votes))
            .filter_by(source_name=source_name, source_game_id=source_game_id)
            .first()
        )

    return None


def _find_streamed_game_match(query: str):
    return find_game_lookup(query)


def _is_streamer_recommendation(user_login: str) -> bool:
    return _normalize_user_login(user_login) == _normalize_user_login(RECOMMENDATIONS_STREAMER_LOGIN)


def _set_streamer_interested(recommendation: RecommendedGame, interested: bool) -> bool:
    changed = bool(recommendation.streamer_interested) != bool(interested)
    recommendation.streamer_interested = bool(interested)
    if changed:
        recommendation.updated_at = datetime.utcnow()
    return changed


def _remove_streamer_vote_if_present(session, recommendation: RecommendedGame) -> bool:
    streamer_vote = _find_user_vote_for_recommendation(session, recommendation.id, RECOMMENDATIONS_STREAMER_LOGIN)
    if not streamer_vote:
        return False

    session.delete(streamer_vote)
    session.flush()
    recommendation.updated_at = datetime.utcnow()
    return True


def _delete_recommendation_if_orphaned(session, recommendation: RecommendedGame) -> bool:
    refreshed = (
        session.query(RecommendedGame)
        .options(joinedload(RecommendedGame.votes))
        .filter_by(id=recommendation.id)
        .first()
    )
    if refreshed and not refreshed.votes:
        session.delete(refreshed)
        session.flush()
        return True
    return False


def _remove_vote(session, vote: RecommendedGameVote) -> tuple[str, bool]:
    title = vote.recommended_game.title
    recommendation = vote.recommended_game
    session.delete(vote)
    session.flush()
    deleted_recommendation = _delete_recommendation_if_orphaned(session, recommendation)
    return title, deleted_recommendation


def _find_user_vote_for_recommendation(session, recommendation_id: int, user_login: str) -> RecommendedGameVote | None:
    return (
        session.query(RecommendedGameVote)
        .filter_by(recommended_game_id=recommendation_id, user_login=_normalize_user_login(user_login))
        .first()
    )


def _load_user_active_votes(session, user_login: str) -> list[RecommendedGameVote]:
    return (
        session.query(RecommendedGameVote)
        .join(RecommendedGame, RecommendedGame.id == RecommendedGameVote.recommended_game_id)
        .options(joinedload(RecommendedGameVote.recommended_game))
        .filter(
            RecommendedGameVote.user_login == _normalize_user_login(user_login),
            RecommendedGame.status.in_(ACTIVE_RECOMMENDATION_STATUSES),
        )
        .order_by(RecommendedGameVote.created_at.asc(), RecommendedGameVote.id.asc())
        .all()
    )


def _enforce_user_limit(session, user_login: str) -> str | None:
    if _user_is_privileged(user_login):
        return None

    active_votes = _load_user_active_votes(session, user_login)
    if len(active_votes) < RECOMMENDATIONS_LIMIT:
        return None

    oldest_vote = active_votes[0]
    removed_title, _ = _remove_vote(session, oldest_vote)
    return removed_title


def _format_limit_suffix(removed_title: str | None) -> str:
    if not removed_title:
        return ""
    return f" Самая старая рекомендация «{removed_title}» убрана из твоего списка."


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
        f"Игра «{title}» уже есть в таблице, твоя рекомендация учтена."
        if already_existing
        else f"Игра «{title}» занесена в таблицу."
    )
    return prefix + _format_limit_suffix(removed_title) + _doc_suffix()


async def recommend_game(query: str, user_login: str, user_display_name: str) -> RecommendationActionResult:
    normalized_query = normalize_recommendation_name(query)
    if not normalized_query:
        return _make_result("invalid", "Напиши: !рек [название игры]")

    if _user_is_banned(user_login):
        return _make_fake_add_result(query)

    streamed_match = _find_streamed_game_match(query)
    streamer_mode = _is_streamer_recommendation(user_login)
    if streamed_match is not None and not streamer_mode:
        return _make_result("already_streamed", build_game_response(query))

    session = SessionLocal()
    try:
        existing = _find_existing_recommendation(session, query)
        if existing:
            if _is_streamer_recommendation(user_login):
                changed = _set_streamer_interested(existing, True)
                changed = _remove_streamer_vote_if_present(session, existing) or changed
                sync_recommendation_matches(session)
                session.commit()
                summary = build_recommendation_summary(existing)
                if changed:
                    return _make_result(
                        "voted",
                        _format_add_message(summary.title, already_existing=True),
                        recommendation=summary,
                        accepted=True,
                    )
                return _make_result("duplicate_vote", f"Игра «{existing.title}» уже отмечена как интересная для Tabula.")

            existing_vote = _find_user_vote_for_recommendation(session, existing.id, user_login)
            if existing_vote:
                return _make_result("duplicate_vote", f"Ты уже рекомендовал «{existing.title}».")

            removed_title = _enforce_user_limit(session, user_login)
            add_vote(session, existing, user_login, user_display_name)
            sync_recommendation_matches(session)
            session.commit()
            summary = build_recommendation_summary(existing)
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
                f"Не нашел игру «{query}». Уточни название и напиши его максимально точно.",
            )

        metadata_streamed_match = _find_streamed_game_match(metadata.title)
        if metadata_streamed_match is not None and not streamer_mode:
            return _make_result("already_streamed", build_game_response(metadata.title))

        existing = _find_existing_recommendation(
            session,
            query=query,
            metadata_title=metadata.title,
            source_name=metadata.source_name,
            source_game_id=metadata.source_game_id,
        )
        if existing:
            if _is_streamer_recommendation(user_login):
                changed = _set_streamer_interested(existing, True)
                changed = _remove_streamer_vote_if_present(session, existing) or changed
                sync_recommendation_matches(session)
                session.commit()
                summary = build_recommendation_summary(existing)
                if changed:
                    return _make_result(
                        "voted",
                        _format_add_message(summary.title, already_existing=True),
                        recommendation=summary,
                        accepted=True,
                    )
                return _make_result("duplicate_vote", f"Игра «{existing.title}» уже отмечена как интересная для Tabula.")

            existing_vote = _find_user_vote_for_recommendation(session, existing.id, user_login)
            if existing_vote:
                return _make_result("duplicate_vote", f"Ты уже рекомендовал «{existing.title}».")

            removed_title = _enforce_user_limit(session, user_login)
            add_vote(session, existing, user_login, user_display_name)
            sync_recommendation_matches(session)
            session.commit()
            summary = build_recommendation_summary(existing)
            return _make_result(
                "voted",
                _format_add_message(summary.title, removed_title=removed_title, already_existing=True),
                recommendation=summary,
                accepted=True,
            )

        removed_title = _enforce_user_limit(session, user_login)
        recommendation = create_recommendation(
            session,
            query_name=query,
            title=metadata.title,
            release_date=metadata.release_date,
            release_precision=metadata.release_precision,
            description_short=metadata.description_short,
            steam_url=metadata.steam_url,
            rating_text=metadata.rating_text,
            platforms_text=metadata.platforms_text,
            genres_text=metadata.genres_text,
            cover_url=metadata.cover_url,
            source_name=metadata.source_name,
            source_game_id=metadata.source_game_id,
            source_payload=metadata.source_payload,
        )
        if _is_streamer_recommendation(user_login):
            _set_streamer_interested(recommendation, True)
            _remove_streamer_vote_if_present(session, recommendation)
        else:
            add_vote(session, recommendation, user_login, user_display_name)

        if metadata_streamed_match is not None:
            matched_game = session.query(Game).filter_by(name=metadata_streamed_match.name).first()
            recommendation.matched_game = matched_game

        sync_recommendation_matches(session)
        session.commit()
        summary = build_recommendation_summary(recommendation)
        return _make_result(
            "created",
            _format_add_message(summary.title, removed_title=removed_title),
            recommendation=summary,
            accepted=True,
        )
    finally:
        session.close()


async def delete_own_last_recommendation(user_login: str) -> RecommendationActionResult:
    if _user_is_banned(user_login):
        return _make_fake_delete_result()

    session = SessionLocal()
    try:
        vote = (
            session.query(RecommendedGameVote)
            .join(RecommendedGame, RecommendedGame.id == RecommendedGameVote.recommended_game_id)
            .options(joinedload(RecommendedGameVote.recommended_game))
            .filter(
                RecommendedGameVote.user_login == _normalize_user_login(user_login),
                RecommendedGame.status.in_(ACTIVE_RECOMMENDATION_STATUSES),
            )
            .order_by(RecommendedGameVote.created_at.desc(), RecommendedGameVote.id.desc())
            .first()
        )
        if not vote:
            return _make_result("not_found", "У тебя нет активных рекомендаций.")

        title, deleted_recommendation = _remove_vote(session, vote)
        sync_recommendation_matches(session)
        session.commit()
        suffix = " Игра целиком убрана из таблицы." if deleted_recommendation else ""
        return _make_result("deleted", f"Последняя рекомендация по игре «{title}» удалена.{suffix}")
    finally:
        session.close()


async def delete_own_recommendation_by_title(query: str, user_login: str) -> RecommendationActionResult:
    if not normalize_recommendation_name(query):
        return _make_result("invalid", "Напиши: !рек - [название игры]")

    if _user_is_banned(user_login):
        return _make_fake_delete_result(query)

    session = SessionLocal()
    try:
        recommendation = find_recommendation_by_query(session, query)
        if not recommendation:
            return _make_result("not_found", f"Не нашел твою рекомендацию для «{query}».")

        vote = _find_user_vote_for_recommendation(session, recommendation.id, user_login)
        if not vote:
            return _make_result("not_found", f"Не нашел твою рекомендацию для «{recommendation.title}».")

        title, deleted_recommendation = _remove_vote(session, vote)
        sync_recommendation_matches(session)
        session.commit()
        suffix = " Игра целиком убрана из таблицы." if deleted_recommendation else ""
        return _make_result("deleted", f"Рекомендация по игре «{title}» удалена.{suffix}")
    finally:
        session.close()


async def admin_delete_recommendations(target_user: str, query: str | None, actor_login: str) -> RecommendationActionResult:
    if not _user_is_admin(actor_login):
        return _make_result("forbidden", "Команда доступна только ADMINS.")

    if _user_is_banned(actor_login):
        return _make_fake_delete_result(query)

    normalized_target = _normalize_user_login(target_user)
    if not normalized_target:
        return _make_result("invalid", "Напиши: !рек -- [ник] [название игры] или !рек -- [ник]")

    session = SessionLocal()
    try:
        if normalized_target in ADMIN_DELETE_ALL_MARKERS:
            if not query:
                return _make_result("invalid", "Для удаления игры целиком напиши: !рек -- * [название игры]")

            recommendation = find_recommendation_by_query(session, query)
            if not recommendation:
                return _make_result("not_found", f"Игра «{query}» в рекомендациях не найдена.")

            title = recommendation.title
            votes_count = len(recommendation.votes)
            session.delete(recommendation)
            session.flush()
            session.commit()
            return _make_result("deleted", f"Игра «{title}» удалена из рекомендаций целиком. Удалено голосов: {votes_count}.")

        if not query:
            votes = _load_user_active_votes(session, normalized_target)
            if not votes:
                return _make_result("not_found", f"У пользователя {target_user} нет активных рекомендаций.")

            removed_count = 0
            removed_titles = []
            for vote in votes:
                title, _ = _remove_vote(session, vote)
                removed_titles.append(title)
                removed_count += 1

            sync_recommendation_matches(session)
            session.commit()
            return _make_result(
                "deleted",
                f"У пользователя {target_user} удалено рекомендаций: {removed_count}. Игры: {', '.join(removed_titles[:5])}",
            )

        recommendation = find_recommendation_by_query(session, query)
        if not recommendation:
            return _make_result("not_found", f"Игра «{query}» в рекомендациях не найдена.")

        vote = _find_user_vote_for_recommendation(session, recommendation.id, normalized_target)
        if not vote:
            return _make_result("not_found", f"У пользователя {target_user} нет рекомендации для «{recommendation.title}».")

        title, deleted_recommendation = _remove_vote(session, vote)
        sync_recommendation_matches(session)
        session.commit()
        suffix = " Игра целиком убрана из таблицы." if deleted_recommendation else ""
        return _make_result("deleted", f"У пользователя {target_user} удалена рекомендация по игре «{title}».{suffix}")
    finally:
        session.close()
