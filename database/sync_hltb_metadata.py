"""
One-time script: fill/update game_metadata_hltb via howlongtobeatpy.

For each game in the DB that is missing or has stale HLTB metadata,
searches HowLongToBeat, validates the match via similarity, and upserts
the full set of fields (story, extra, completionist, all_styles, etc.).

Respects per-request delay (1.5 s) to avoid rate-limiting.

Usage:
    python database/sync_hltb_metadata.py [--force]

    --force   Re-process every game regardless of whether it already
              has HLTB metadata.
"""

import asyncio
import logging
import re
import time

from howlongtobeatpy import HowLongToBeat, HowLongToBeatEntry
from sqlalchemy import select

from database.db import AsyncSessionLocal
from database.models import Game, GameMetadataHLTB, GameAlias

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

HLTB_DELAY_SECONDS = 1.5
MIN_SIMILARITY = 0.6


def _now_ts() -> int:
    return int(time.time())


def _sanitize(name: str) -> str:
    sanitized = re.sub(r"[^\w\s:+'\-.]", " ", name, flags=re.UNICODE)
    return " ".join(sanitized.split())


def _strip_subtitle(name: str) -> str | None:
    for sep in (" -- ", " – ", " - ", ": ", " — "):
        if sep in name:
            return name.split(sep, 1)[0].strip()
    return None


def _pick_best_match(
    results: list[HowLongToBeatEntry],
    game_name: str,
    alias_names: list[str],
) -> HowLongToBeatEntry | None:
    """Pick the best-matching entry from HLTB results.

    Uses the library's built-in similarity score, with a boost when the
    name matches one of the game's known aliases.
    """
    if not results:
        return None

    def _score(entry: HowLongToBeatEntry) -> float:
        sim = entry.similarity
        lower_name = " ".join(game_name.casefold().split())
        for alias in alias_names:
            if sim < 1.0 and alias.casefold() == lower_name:
                sim = min(1.0, sim + 0.15)
        return sim

    best = max(results, key=_score)
    return best if _score(best) >= MIN_SIMILARITY else None


def _search_hltb(
    client: HowLongToBeat,
    game_name: str,
    alias_names: list[str],
) -> HowLongToBeatEntry | None:
    """Search HLTB with multiple query strategies and return best match."""
    seen_ids: set[int] = set()
    candidates: list[HowLongToBeatEntry] = []

    queries = [game_name]
    sanitized = _sanitize(game_name)
    if sanitized and sanitized != game_name and sanitized not in queries:
        queries.append(sanitized)

    base = _strip_subtitle(game_name)
    if base and base not in queries:
        queries.append(base)

    for q in queries:
        results = client.search(q, similarity_case_sensitive=False) or []
        for r in results:
            if r.game_id not in seen_ids:
                seen_ids.add(r.game_id)
                candidates.append(r)

    return _pick_best_match(candidates, game_name, alias_names)


def _build_patch(entry: HowLongToBeatEntry) -> dict:
    return {
        "hltb_id": str(entry.game_id),
        "hltb_name": entry.game_name,
        "hltb_main_story": entry.main_story,
        "hltb_main_extra": entry.main_extra,
        "hltb_completionist": entry.completionist,
        "hltb_all_styles": entry.all_styles,
        "hltb_coop": entry.coop_time,
        "hltb_multiplayer": entry.mp_time,
        "hltb_review_score": entry.review_score,
        "review_count": None,
    }


async def sync_hltb_metadata(*, force: bool = False) -> None:
    client = HowLongToBeat()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Game).order_by(Game.id)
        )
        games = list(result.scalars().all())

    if not games:
        log.info("No games in DB")
        return

    log.info("Processing %d games%s", len(games), " (force mode)" if force else "")

    processed = 0
    skipped = 0
    updated = 0
    inserted = 0
    errors = 0

    for game in games:
        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(GameMetadataHLTB).where(GameMetadataHLTB.game_id == game.id)
            )
            row = existing.scalar_one_or_none()

            if row is not None and not force:
                processed += 1
                skipped += 1
                if processed % 50 == 0:
                    log.info("Progress: %d/%d", processed, len(games))
                continue

            alias_result = await session.execute(
                select(GameAlias.alias).where(GameAlias.game_id == game.id)
            )
            alias_names = [r[0] for r in alias_result.all() if r[0]]

        try:
            entry = await asyncio.to_thread(
                _search_hltb, client, game.name, alias_names
            )
        except Exception as exc:
            log.warning("Error searching HLTB for %r: %s", game.name, exc)
            errors += 1
            processed += 1
            continue

        if entry is None:
            log.debug("No HLTB match for %r", game.name)
            skipped += 1
            processed += 1
            if processed % 50 == 0:
                log.info("Progress: %d/%d", processed, len(games))
            continue

        patch = _build_patch(entry)

        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(GameMetadataHLTB).where(GameMetadataHLTB.game_id == game.id)
            )
            row = existing.scalar_one_or_none()
            if row is not None:
                for k, v in patch.items():
                    setattr(row, k, v)
                row.synced_at = _utcnow()
                session.add(row)
                updated += 1
            else:
                meta = GameMetadataHLTB(
                    game_id=game.id,
                    **patch,
                    synced_at=_utcnow(),
                )
                session.add(meta)
                inserted += 1
            await session.commit()

        processed += 1
        if processed % 25 == 0:
            log.info(
                "Progress: %d/%d (inserted=%d, updated=%d, skipped=%d, errors=%d)",
                processed, len(games), inserted, updated, skipped, errors,
            )

        await asyncio.sleep(HLTB_DELAY_SECONDS)

    log.info(
        "Done: inserted=%d, updated=%d, skipped=%d, errors=%d (of %d)",
        inserted, updated, skipped, errors, len(games),
    )


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    asyncio.run(sync_hltb_metadata(force=force))
