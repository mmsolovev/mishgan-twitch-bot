"""
Orchestrates the import of upcoming game releases from IGDB into recommendations.
"""

import asyncio
from datetime import datetime, timezone

from database.db import AsyncSessionLocal
from pipeline.ingest.igdb_api import ingest_top_upcoming_games
from pipeline.load.load_recommendations import (
    create_game_with_igdb,
    add_igdb_note,
    find_game_by_normalized_name,
)
from pipeline.transform.recommendations_transform import normalize_recommendation_name
from utils.logger import get_logger


async def _run():
    logger = get_logger("orchestrator.import_igdb_releases")

    logger.info("Starting IGDB upcoming games import...")

    games_meta = await ingest_top_upcoming_games(limit=15)
    if not games_meta:
        logger.info("No upcoming games returned from IGDB.")
        return

    async with AsyncSessionLocal() as session:
        added_count = 0

        for meta in games_meta:
            normalized = normalize_recommendation_name(meta.title)
            if not meta.title or not normalized:
                continue

            existing = await find_game_by_normalized_name(session, normalized)
            if existing:
                continue

            game = await create_game_with_igdb(
                session,
                name=meta.title,
                igdb_id=meta.source_game_id,
                release_date=meta.release_date,
                steam_url=meta.steam_url,
                igdb_score=_parse_igdb_score(meta.rating_text),
                description_en=meta.description_short,
                cover_url=meta.cover_url,
                raw_payload=meta.source_payload,
            )

            await add_igdb_note(session, game.id, user_login="igdb")
            added_count += 1

        if added_count > 0:
            await session.commit()
            logger.info(f"Successfully imported {added_count} new upcoming games from IGDB.")
        else:
            logger.info("No new upcoming games to import from IGDB.")


def _parse_igdb_score(rating_text: str | None) -> float | None:
    if not rating_text:
        return None
    import re
    match = re.search(r"(\d+(?:\.\d+)?)", rating_text)
    if match:
        return float(match.group(1))
    return None


def import_igdb_releases():
    asyncio.run(_run())


if __name__ == "__main__":
    import_igdb_releases()
