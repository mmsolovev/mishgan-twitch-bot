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

HLTB_DELAY_SECONDS = 2.0
HLTB_RETRY_DELAY = 10.0
HLTB_MAX_RETRIES = 3
MIN_SIMILARITY = 0.4

_IGNORED_CATEGORIES = {
    "just chatting", "special events", "games + demos", "retro", "variety",
}


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


def _zero_to_none(value):
    if value is None:
        return None
    if isinstance(value, float):
        return value if value != 0.0 else None
    if isinstance(value, int):
        return value if value != 0 else None
    return value


def _build_patch(entry: HowLongToBeatEntry) -> dict:
    return {
        "hltb_id": str(entry.game_id),
        "hltb_name": entry.game_name,
        "hltb_main_story": _zero_to_none(entry.main_story),
        "hltb_main_extra": _zero_to_none(entry.main_extra),
        "hltb_completionist": _zero_to_none(entry.completionist),
        "hltb_all_styles": _zero_to_none(entry.all_styles),
        "hltb_coop": _zero_to_none(entry.coop_time),
        "hltb_multiplayer": _zero_to_none(entry.mp_time),
        "hltb_review_score": _zero_to_none(entry.review_score),
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
        normalized_name = " ".join(game.name.casefold().split())
        if normalized_name in _IGNORED_CATEGORIES:
            log.info("[%d/%d] Skip (non-game) %r", processed + 1, len(games), game.name)
            skipped += 1
            processed += 1
            continue

        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(GameMetadataHLTB).where(GameMetadataHLTB.game_id == game.id)
            )
            row = existing.scalar_one_or_none()

            if row is not None and row.hltb_id is not None and not force:
                log.info(
                    "[%d/%d] Skip (already has HLTB id=%s) %r",
                    processed + 1, len(games), row.hltb_id, game.name,
                )
                skipped += 1
                processed += 1
                continue

            alias_result = await session.execute(
                select(GameAlias.alias).where(GameAlias.game_id == game.id)
            )
            alias_names = [r[0] for r in alias_result.all() if r[0]]

        entry = None
        for attempt in range(1, HLTB_MAX_RETRIES + 1):
            try:
                entry = await asyncio.to_thread(
                    _search_hltb, client, game.name, alias_names
                )
                break
            except (ConnectionResetError, ConnectionAbortedError, TimeoutError) as exc:
                log.warning(
                    "[%d/%d] Connection error on attempt %d/%d for %r: %s",
                    processed + 1, len(games), attempt, HLTB_MAX_RETRIES, game.name, exc,
                )
                if attempt < HLTB_MAX_RETRIES:
                    await asyncio.sleep(HLTB_RETRY_DELAY * attempt)
            except Exception as exc:
                log.warning("[%d/%d] Error for %r: %s", processed + 1, len(games), game.name, exc)
                break

        if entry is None:
            log.info("[%d/%d] No match %r", processed + 1, len(games), game.name)
            skipped += 1
            processed += 1
            await asyncio.sleep(HLTB_DELAY_SECONDS)
            continue

        patch = _build_patch(entry)
        match_info = (
            f"sim={entry.similarity:.2f} hltb_id={entry.game_id} "
            f"'{patch.get('hltb_main_story') or '?'}/{patch.get('hltb_main_extra') or '?'}"
            f"/{patch.get('hltb_completionist') or '?'}h"
        )

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

        log.info(
            "[%d/%d] %s %r -> %r (%s)",
            processed + 1, len(games),
            "Updated" if row is not None else "Inserted",
            game.name,
            entry.game_name,
            match_info,
        )

        processed += 1
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
