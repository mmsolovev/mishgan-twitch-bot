import asyncio
import datetime
import importlib
import json
import re
import subprocess
import sys
import time

from howlongtobeatpy import HowLongToBeat, HowLongToBeatEntry
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.db import AsyncSessionLocal
from database.models import Game, GameAlias, GameMetadataHLTB, GameMetadataIGDB
from pipeline.ingest.igdb_api import fetch_igdb_metadata
from pipeline.load.load_game_meta import apply_hltb_patch, apply_igdb_patch
from pipeline.transform.recommendations_transform import normalize_recommendation_name
from services.twitch_service import get_current_game
from utils.logger import get_logger
from utils.time_format import format_hours_minutes

logger = get_logger("services.hltb")

HLTB_COMMAND_DESCRIPTION = (
    "Команда: !hltb [название] — время прохождения игры с сайта HLTB, "
    "если пусто берется текущая игра стрима"
)

NON_GAME_CATEGORIES = {
    "just chatting",
    "irl",
    "special events",
    "games + demos",
    "retro",
    "variety",
    "talk shows & podcasts",
    "art",
    "music",
    "science & technology",
}

MIN_SIMILARITY = 0.60
HLTB_REQUEST_TIMEOUT_SECONDS = 20
HLTB_UPGRADE_COOLDOWN_SECONDS = 3600

_HLTB_LAST_UPGRADE_AT = 0.0

_HLTB_SUBMODULES = (
    "HTMLRequests",
    "JSONResultParser",
    "HowLongToBeatEntry",
    "HowLongToBeat",
)

_CATEGORY_LABELS = [
    ("Сюжет:", "hltb_main_story"),
    ("Сюжет+Доп:", "hltb_main_extra"),
    ("Полное:", "hltb_completionist"),
    ("Кооп:", "hltb_coop"),
    ("Мультиплеер:", "hltb_multiplayer"),
]

_ROMAN_TO_ARABIC = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}


def is_non_game_category(name: str | None) -> bool:
    if not name:
        return True
    return " ".join(name.casefold().split()) in NON_GAME_CATEGORIES


def _sanitize(name: str) -> str:
    sanitized = re.sub(r"[^\w\s:+'\-.]", " ", name, flags=re.UNICODE)
    return " ".join(sanitized.split())


def _strip_subtitle(name: str) -> str | None:
    for sep in (" -- ", " – ", " - ", ": ", " — "):
        if sep in name:
            return name.split(sep, 1)[0].strip()
    return None


def _zero_to_none(value):
    if value is None:
        return None
    if isinstance(value, float):
        return value if value != 0.0 else None
    if isinstance(value, int):
        return value if value != 0 else None
    return value


def _pick_best_match(results: list[HowLongToBeatEntry]) -> HowLongToBeatEntry | None:
    if not results:
        return None
    best = max(results, key=lambda item: item.similarity)
    return best if best.similarity >= MIN_SIMILARITY else None


def _search_hltb(query: str) -> HowLongToBeatEntry | None:
    client = HowLongToBeat()
    seen_ids: set[int] = set()
    candidates: list[HowLongToBeatEntry] = []

    queries = [query]
    sanitized = _sanitize(query)
    if sanitized and sanitized != query and sanitized not in queries:
        queries.append(sanitized)

    base = _strip_subtitle(query)
    if base and base not in queries:
        queries.append(base)

    for q in queries:
        results = client.search(q, similarity_case_sensitive=False)
        if results is None:
            raise ConnectionError(f"HLTB request failed for {q!r}")
        for r in results:
            if r.game_id not in seen_ids:
                seen_ids.add(r.game_id)
                candidates.append(r)

    return _pick_best_match(candidates)


def _build_hltb_patch(entry: HowLongToBeatEntry) -> dict:
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
    }


def _run_pip_upgrade_hltb() -> bool:
    """Upgrade the howlongtobeatpy package in the current interpreter."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "howlongtobeatpy"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return result.returncode == 0
    except Exception:
        return False


def _reload_hltb_modules() -> bool:
    """Re-import howlongtobeatpy fresh from disk after an in-place upgrade."""
    try:
        import howlongtobeatpy

        for sub in _HLTB_SUBMODULES:
            name = f"howlongtobeatpy.{sub}"
            if name in sys.modules:
                importlib.reload(sys.modules[name])
        importlib.reload(howlongtobeatpy)
        globals()["HowLongToBeat"] = howlongtobeatpy.HowLongToBeat
        globals()["HowLongToBeatEntry"] = howlongtobeatpy.HowLongToBeatEntry
        return True
    except Exception as exc:
        logger.warning("howlongtobeatpy reload failed: %s", exc)
        return False


async def _maybe_upgrade_hltb_lib() -> bool:
    """Best-effort auto-upgrade on request failures, once per cooldown."""
    global _HLTB_LAST_UPGRADE_AT
    now = time.monotonic()
    if now - _HLTB_LAST_UPGRADE_AT < HLTB_UPGRADE_COOLDOWN_SECONDS:
        return False
    _HLTB_LAST_UPGRADE_AT = now

    logger.warning("howlongtobeatpy: request failures detected, attempting auto-upgrade")
    if not await asyncio.to_thread(_run_pip_upgrade_hltb):
        logger.warning("howlongtobeatpy: auto-upgrade failed")
        return False
    if not await asyncio.to_thread(_reload_hltb_modules):
        logger.warning("howlongtobeatpy: upgraded but reload failed; new version will apply on restart")
        return False
    logger.info("howlongtobeatpy: upgraded and reloaded")
    return True


async def _search_hltb_with_retries(query: str) -> tuple[HowLongToBeatEntry | None, bool]:
    """
    Search HLTB with retries on connection errors.

    Returns (entry, service_ok). service_ok=False means the request itself
    failed (network / outage); True means HLTB responded but without a match.
    """
    service_error = False
    upgraded = False
    for attempt in range(1, 4):
        try:
            entry = await asyncio.wait_for(
                asyncio.to_thread(_search_hltb, query),
                timeout=HLTB_REQUEST_TIMEOUT_SECONDS,
            )
            return entry, True
        except (ConnectionError, TimeoutError, asyncio.TimeoutError):
            service_error = True
            if not upgraded:
                upgraded = await _maybe_upgrade_hltb_lib()
            if attempt < 3:
                await asyncio.sleep(2.0 * attempt)
        except Exception:
            service_error = True
            break

    return None, not service_error


def _build_message(title: str, meta: GameMetadataHLTB) -> str | None:
    times = _format_times(meta)
    if not times:
        return None
    return f"Прохождение {title} по HowLongToBeat: {times}"


def _format_times(meta: GameMetadataHLTB) -> str:
    parts = []
    all_styles = format_hours_minutes(_zero_to_none(meta.hltb_all_styles))
    if all_styles:
        parts.append(all_styles)

    for label, field in _CATEGORY_LABELS:
        value = format_hours_minutes(_zero_to_none(getattr(meta, field)))
        if value:
            parts.append(f"{label} {value}")

    return " | ".join(parts)


def _canonicalize(value: str | None) -> str:
    """Normalize name like games lookup does: roman numerals to arabic."""
    if not value:
        return ""
    normalized = " ".join(value.casefold().split())
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    tokens = [_ROMAN_TO_ARABIC.get(token, token) for token in normalized.split()]
    return " ".join(tokens)


async def _find_game(query: str) -> Game | None:
    canonical_query = _canonicalize(query)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Game)
            .options(selectinload(Game.hltb_metadata))
            .outerjoin(GameAlias, GameAlias.game_id == Game.id)
            .where(
                (Game.name == query)
                | (GameAlias.normalized_alias == normalize_recommendation_name(query))
            )
            .limit(1)
        )
        row = result.scalars().first()
        if row is not None:
            return row

        if not canonical_query:
            return None

        all_games = (
            await session.execute(
                select(Game).options(selectinload(Game.hltb_metadata))
            )
        ).scalars().all()
        matches = [game for game in all_games if _canonicalize(game.name) == canonical_query]
        if not matches:
            return None
        matches.sort(key=lambda game: (game.hltb_metadata is None, game.id))
        return matches[0]


async def _resolve_game(
    session, query: str, entry: HowLongToBeatEntry, existing_game
) -> Game:
    """
    Pick the Game row to enrich: reuse the DB row that already owns the HLTB
    entry (avoids duplicates when the user typed another name for the same
    game, e.g. "Gothic Remake" vs "Gothic 1 Remake").
    """
    if existing_game is not None:
        return await session.get(Game, existing_game.id)

    holder_id = (
        await session.execute(
            select(GameMetadataHLTB.game_id)
            .where(GameMetadataHLTB.hltb_id == str(entry.game_id))
            .limit(1)
        )
    ).scalar_one_or_none()
    if holder_id is not None:
        held = await session.get(Game, holder_id)
        if held is not None:
            return held

    from pipeline.load.load_recommendations import create_game

    return await create_game(session, name=query)


async def _store_and_return(query: str, existing_game=None) -> tuple[str | None, str | None]:
    """
    Fetch HLTB (+IGDB enrichment) for a game, write everything to the DB,
    and return (success_message, error_message).

    existing_game - the Game row already present in our DB (may have empty
    HLTB metadata); otherwise the game is created in the DB first.
    """
    entry, service_ok = await _search_hltb_with_retries(query)
    if entry is None:
        if service_ok:
            return None, f"Не удалось найти информацию по игре «{query.strip()}»"
        return None, "Сервис HowLongToBeat сейчас недоступен"

    patch = _build_hltb_patch(entry)

    try:
        igdb_meta = await fetch_igdb_metadata(query)
    except Exception:
        igdb_meta = None

    async with AsyncSessionLocal() as session:
        game = await _resolve_game(session, query, entry, existing_game)
        game_id = int(game.id)
        await apply_hltb_patch(session, game_id=game_id, patch=patch)

        if igdb_meta is not None:
            igdb_id = str(igdb_meta.source_game_id)
            holder_game_id = (
                await session.execute(
                    select(GameMetadataIGDB.game_id).where(
                        GameMetadataIGDB.igdb_id == igdb_id,
                        GameMetadataIGDB.game_id != game_id,
                    )
                )
            ).scalar_one_or_none()

            igdb_score = None
            raw_payload_data = None
            if igdb_meta.source_payload:
                try:
                    raw_payload_data = json.loads(igdb_meta.source_payload)
                    payload_score = raw_payload_data.get("total_rating")
                    if payload_score is not None:
                        igdb_score = float(payload_score)
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw_payload_data = None
                    igdb_score = None

            fields = {
                "igdb_name": igdb_meta.title,
                "release_date": igdb_meta.release_date,
                "steam_url": igdb_meta.steam_url,
                "igdb_score": igdb_score,
                "description_en": igdb_meta.description_short,
                "cover_url": igdb_meta.cover_url,
                "raw_payload": raw_payload_data,
                "synced_at": datetime.datetime.utcnow(),
            }
            if holder_game_id is None:
                fields["igdb_id"] = igdb_id
            igdb_patch = {key: value for key, value in fields.items() if value not in (None, "")}
            if igdb_patch:
                await apply_igdb_patch(session, game_id=game_id, patch=igdb_patch)

        await session.commit()

        meta = await session.get(GameMetadataHLTB, game_id)

    if meta is None:
        return None, f"Не удалось найти информацию по игре «{query.strip()}»"

    message = _build_message(meta.hltb_name or query, meta)
    if message is None:
        return None, f"Не удалось найти информацию по игре «{query.strip()}»"

    return message, None


def clean_user_query(game: str) -> str:
    return game.strip().strip('"').strip("«»").strip()


async def get_hltb_info(game: str | None) -> str:
    """Full response message for the !hltb command."""
    if not game:
        game = await get_current_game()
        if not game:
            return "Не удалось определить текущую категорию стрима"

        if is_non_game_category(game):
            return HLTB_COMMAND_DESCRIPTION
    else:
        game = clean_user_query(game)

    row = await _find_game(game)
    if row is not None and row.hltb_metadata is not None:
        message = _build_message(row.hltb_metadata.hltb_name or row.name, row.hltb_metadata)
        if message is not None:
            return message

    message, error = await _store_and_return(game, existing_game=row)
    if message:
        return message
    return error or "Нет данных по игре"


async def get_hltb_summary(game: str | None) -> str | None:
    """Short summary (stream game-change): overall playtime only."""
    if not game:
        game = await get_current_game()

    if is_non_game_category(game):
        return None

    message = await get_hltb_info(game)

    summary = message.removeprefix("MrDestructoid ")
    first_block = summary.split(" | ", 1)[0]
    if not first_block.startswith("Прохождение "):
        return None
    return first_block