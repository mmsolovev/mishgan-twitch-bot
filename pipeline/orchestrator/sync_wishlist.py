"""
Orchestrates syncing of Steam wishlist entries into Tabula's game recommendations.

Parses storage/pages/steam_wishlist.html, looks up each game in IGDB,
verifies the Steam URL matches, and creates a recommendation + vote
as if the streamer had run the command "!рек + [game]".
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from bs4 import BeautifulSoup

from database.db import SessionLocal
from pipeline.ingest.igdb_api import fetch_recommendation_metadata
from pipeline.load.load_recommendations import (
    add_vote,
    create_recommendation,
    find_existing_recommendation,
    sync_recommendation_matches,
)
from pipeline.transform.recommendations_transform import normalize_recommendation_name
from utils.logger import get_logger

WISHLIST_HTML_PATH = Path(__file__).resolve().parent.parent.parent / "storage" / "pages" / "steam_wishlist.html"

TABULA_LOGIN = "tabula"
TABULA_DISPLAY_NAME = "Tabula"


def _extract_app_id(steam_url: str) -> str | None:
    match = re.search(r"/app/(\d+)", steam_url)
    return match.group(1) if match else None


def _parse_wishlist_items(html_content: str) -> list[dict]:
    soup = BeautifulSoup(html_content, "html.parser")
    items = []
    for link in soup.select("a.pOyXxbQoV38-"):
        name = link.get_text(strip=True)
        href = link.get("href", "").strip()
        if name and href:
            items.append({
                "name": name,
                "steam_url": href,
                "app_id": _extract_app_id(href),
            })
    return items


def _steam_urls_match(wishlist_steam_url: str | None, igdb_steam_url: str | None) -> bool:
    if not wishlist_steam_url or not igdb_steam_url:
        return False
    wl_app_id = _extract_app_id(wishlist_steam_url)
    igdb_app_id = _extract_app_id(igdb_steam_url)
    return wl_app_id is not None and wl_app_id == igdb_app_id


async def _process_single_game(
    session,
    item: dict,
    logger,
    index: int,
    total: int,
) -> str:
    name = item["name"]
    steam_url = item["steam_url"]
    app_id = item["app_id"]

    logger.info("[%d/%d] Processing: %s (app %s)", index, total, name, app_id or "?")

    metadata = await fetch_recommendation_metadata(name)
    if metadata is None:
        logger.warning("[%d/%d] Game not found in IGDB: %s", index, total, name)
        return "skipped (not in IGDB)"

    if not _steam_urls_match(steam_url, metadata.steam_url):
        logger.warning(
            "[%d/%d] Steam URL mismatch for '%s': wishlist app_id=%s, IGDB steam_url=%s",
            index, total, name, app_id, metadata.steam_url,
        )
        return "skipped (Steam URL mismatch)"

    existing = find_existing_recommendation(
        session,
        query=name,
        metadata_title=metadata.title,
        source_name=metadata.source_name,
        source_game_id=metadata.source_game_id,
    )
    if existing:
        logger.info(
            "[%d/%d] Already exists: '%s' (source=%s, source_game_id=%s)",
            index, total, existing.title, existing.source_name, existing.source_game_id,
        )
        return "skipped (already exists)"

    recommendation = create_recommendation(
        session,
        query_name=name,
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

    add_vote(
        session,
        recommendation,
        user_login=TABULA_LOGIN,
        user_display_name=TABULA_DISPLAY_NAME,
    )

    sync_recommendation_matches(session)
    session.commit()

    logger.info(
        "[%d/%d] Added: '%s' (IGDB id=%s, status=%s)",
        index, total, metadata.title, metadata.source_game_id, recommendation.status,
    )
    return "added"


async def _sync_all():
    logger = get_logger("orchestrator.sync_wishlist")

    if not WISHLIST_HTML_PATH.exists():
        logger.error("Wishlist HTML file not found: %s", WISHLIST_HTML_PATH)
        return

    logger.info("Reading wishlist: %s", WISHLIST_HTML_PATH)
    html = WISHLIST_HTML_PATH.read_text(encoding="utf-8")
    items = _parse_wishlist_items(html)
    logger.info("Found %d games in wishlist", len(items))

    if not items:
        return

    session = SessionLocal()
    try:
        stats = {"added": 0, "skipped": 0, "errors": 0}

        for idx, item in enumerate(items, start=1):
            try:
                result = await _process_single_game(session, item, logger, idx, len(items))
                if result == "added":
                    stats["added"] += 1
                else:
                    stats["skipped"] += 1
            except Exception as exc:
                session.rollback()
                logger.exception(
                    "[%d/%d] Error processing '%s': %s",
                    idx, len(items), item["name"], exc,
                )
                stats["errors"] += 1

        logger.info(
            "Sync complete: added=%d, skipped=%d, errors=%d",
            stats["added"], stats["skipped"], stats["errors"],
        )
    except Exception:
        logger.exception("Unexpected error during wishlist sync")
        session.rollback()
    finally:
        session.close()


def sync_wishlist():
    asyncio.run(_sync_all())


if __name__ == "__main__":
    sync_wishlist()
