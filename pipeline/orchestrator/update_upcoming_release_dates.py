from __future__ import annotations

"""
Orchestrator to update release dates for upcoming games.
"""

import asyncio

from database.db import AsyncSessionLocal
from pipeline.ingest.igdb_api import fetch_games_by_ids
from pipeline.load.load_recommendations import (
    get_upcoming_games,
    update_release_dates,
)
from utils.logger import get_logger


async def _run():
    logger = get_logger("release_dates_updater")
    logger.info("Starting release dates update for upcoming games...")

    async with AsyncSessionLocal() as session:
        local_games = await get_upcoming_games(session)
        if not local_games:
            logger.info("No upcoming games found in the database.")
            return

        game_ids = [game.igdb_metadata.igdb_id for game in local_games if game.igdb_metadata and game.igdb_metadata.igdb_id]
        if not game_ids:
            logger.info("No IGDB IDs found for upcoming games.")
            return

        igdb_games = await fetch_games_by_ids(game_ids)
        if not igdb_games:
            logger.warning("Could not fetch games from IGDB.")
            return

        igdb_games_by_id = {str(g.get("id")): g for g in igdb_games}

        games_to_update = []
        from datetime import datetime, timezone
        for game in local_games:
            if not game.igdb_metadata or not game.igdb_metadata.igdb_id:
                continue
            igdb_game = igdb_games_by_id.get(game.igdb_metadata.igdb_id)
            if not igdb_game:
                continue
            from pipeline.transform.igdb_transform import parse_release_date
            igdb_release_date, _ = parse_release_date(igdb_game.get("first_release_date"))
            if igdb_release_date and game.igdb_metadata.release_date != igdb_release_date:
                game.igdb_metadata.release_date = igdb_release_date
                games_to_update.append(game)

        if games_to_update:
            updated_count = await update_release_dates(session, games_to_update)
            await session.commit()
            logger.info(f"Updated release dates for {updated_count} games.")
        else:
            logger.info("No release dates to update.")

    logger.info("Release dates update finished.")


def main():
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
