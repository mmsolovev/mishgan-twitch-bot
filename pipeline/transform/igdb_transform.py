from __future__ import annotations

"""
Transform layer: parsing/normalization helpers for IGDB API payloads.
"""

from datetime import datetime, timezone

_STEAM_HOST_MARKERS = ("store.steampowered.com", "steamcommunity.com", "steam://")


def parse_release_date(value: int | str | None) -> tuple[datetime | None, str]:
    if value is None:
        return None, "unknown"

    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None, "unknown"

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None), "day"


def truncate_text(value: str | None, max_length: int = 280) -> str | None:
    if not value:
        return None

    compact = " ".join(value.split())
    if len(compact) <= max_length:
        return compact

    return compact[: max_length - 3].rstrip() + "..."


def build_rating_text(payload: dict) -> str | None:
    total_rating = payload.get("total_rating")
    total_rating_count = payload.get("total_rating_count")
    aggregated_rating = payload.get("aggregated_rating")
    aggregated_rating_count = payload.get("aggregated_rating_count")

    parts = []
    if total_rating:
        parts.append(f"IGDB {total_rating:.0f}/100")
    if total_rating_count:
        parts.append(f"оценок {int(total_rating_count)}")
    if aggregated_rating:
        parts.append(f"critic {aggregated_rating:.0f}/100")
    if aggregated_rating_count:
        parts.append(f"critic votes {int(aggregated_rating_count)}")

    return " | ".join(parts) if parts else None


def join_names(values: list[dict] | None) -> str | None:
    if not values:
        return None
    out = []
    for item in values:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            out.append(name)
    return ", ".join(out) if out else None


def normalize_genres_text(genres: list[dict] | None) -> str | None:
    if not genres:
        return None
    
    names = []
    for item in genres:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
            
    return ", ".join(names) if names else None


def build_platforms_text(platforms: list[dict] | None) -> str | None:
    if not platforms:
        return None

    names = [str(p.get("name") or "").strip() for p in platforms if isinstance(p, dict)]
    names = [n for n in names if n]

    return ", ".join(names) if names else None


def extract_steam_url(websites: list[dict] | None) -> str | None:
    for website in websites or []:
        url = str((website or {}).get("url") or "").strip()
        if any(marker in url.casefold() for marker in _STEAM_HOST_MARKERS):
            return url
    return None


def normalize_cover_url(cover_payload: dict | None) -> str | None:
    cover = cover_payload or {}
    cover_url = str(cover.get("url") or "").strip()
    if not cover_url:
        return None
    if cover_url.startswith("//"):
        return f"https:{cover_url}"
    if cover_url.startswith("/"):
        return f"https://images.igdb.com{cover_url}"
    return cover_url


def pick_best_match(results: list[dict], search_query: str) -> dict | None:
    if not results:
        return None

    best_match = results[0]
    query_cf = str(search_query or "").casefold()
    for item in results:
        if str(item.get("name") or "").casefold() == query_cf:
            return item
    return best_match


__all__ = [
    "build_platforms_text",
    "build_rating_text",
    "extract_steam_url",
    "join_names",
    "normalize_cover_url",
    "normalize_genres_text",
    "parse_release_date",
    "pick_best_match",
    "truncate_text",
]
