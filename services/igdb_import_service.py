import asyncio
import re
from datetime import datetime, timezone

from database.db import SessionLocal
from database.models import RecommendedGame, RecommendedGameVote
from services.recommendation_metadata_service import fetch_top_upcoming_games, fetch_recommendation_metadata
from services.recommendations_service import STATUS_UPCOMING, STATUS_RELEASED


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def import_igdb_games():
    session = SessionLocal()

    games = asyncio.run(fetch_top_upcoming_games())

    existing_titles = {
        r.title for r in session.query(RecommendedGame.title).all()
    }

    added = 0

    for meta in games:
        normalized = normalize_name(meta.title)
        if not meta.title or meta.title in existing_titles:
            continue

        existing = session.query(RecommendedGame).filter_by(
            normalized_name=normalized
        ).first()

        if existing:
            continue

        now = datetime.now(timezone.utc)

        recommendation = RecommendedGame(
            query_name="igdb",
            normalized_name=normalized,
            title=meta.title,
            status=STATUS_UPCOMING,

            release_date=meta.release_date,
            release_precision="unknown",

            description_short=None,
            steam_url=meta.steam_url,
            rating_text=meta.rating_text,
            platforms_text=meta.platforms_text,
            genres_text=meta.genres_text,
            cover_url=meta.cover_url,

            source_name=meta.source_name,
            source_game_id=meta.source_game_id,
            source_payload=meta.source_payload,

            streamer_interested=False,

            created_at=now,
            updated_at=now,
        )

        session.add(recommendation)
        session.flush()

        vote = RecommendedGameVote(
            recommended_game_id=recommendation.id,
            user_login="igdb",
            user_display_name="IGDB",
            created_at=now,
        )

        session.add(vote)
        added += 1

    session.commit()
    session.close()

    print(f"IGDB import: added {added} games")


def cleanup_igdb_recommendations():
    session = SessionLocal()
    today = datetime.utcnow().date()

    recommendations = session.query(RecommendedGame).join(RecommendedGame.votes).filter(
        RecommendedGameVote.user_login == "igdb"
    ).all()

    removed = 0
    moved = 0

    for rec in recommendations:
        is_igdb = any(v.user_login == "igdb" for v in rec.votes)

        if not is_igdb:
            continue

        if not rec.release_date:
            continue

        if rec.release_date.date() <= today:
            if not rec.streamer_interested:
                session.delete(rec)
                removed += 1
            else:
                rec.status = STATUS_RELEASED
                moved += 1

                for vote in rec.votes:
                    if vote.user_login == "igdb":
                        vote.user_login = "tabula"
                        vote.user_display_name = "Tabula"

    session.commit()
    session.close()

    print(f"IGDB cleanup: removed {removed}, moved {moved}")


def cleanup_and_fix_igdb_games():
    session = SessionLocal()

    games = session.query(RecommendedGame).filter(
        RecommendedGame.source_name == "igdb"
    ).all()

    removed = 0
    updated = 0

    for game in games:

        # 🧹 1. удалить без платформ
        if not game.platforms_text:
            session.delete(game)
            removed += 1
            continue

        # 🔗 2. если нет steam_url — пробуем подтянуть
        if not game.steam_url:
            try:
                meta = asyncio.run(fetch_recommendation_metadata(game.title))

                if meta and meta.steam_url:
                    game.steam_url = meta.steam_url
                    game.updated_at = datetime.utcnow()
                    updated += 1

            except Exception as e:
                print(f"[IGDB FIX ERROR] {game.title}: {e}")

    session.commit()
    session.close()

    print(f"IGDB cleanup done: removed={removed}, updated={updated}")


if __name__ == "__main__":
    # import_igdb_games()
    # cleanup_igdb_recommendations()
    cleanup_and_fix_igdb_games()
