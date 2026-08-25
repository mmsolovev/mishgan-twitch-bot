from __future__ import annotations

"""
Transform layer: streams domain.

Contains:
- computing Stream.genres_text from title/participants/games
- matching Stream rows to Twitch VODs (date + title overlap)
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Any

from pipeline.transform.utils_transform import dedup_keep_order, normalize_genre_token, normalize_key, parse_csv


def compute_stream_genres(
    *,
    title: str | None,
    has_participants: bool,
    game_names: list[str],
    game_genres_texts: list[str | None],
) -> str | None:
    title_raw = title or ""
    title_norm = normalize_key(title_raw)
    token_list = [t for t in re.findall(r"[\w@]+", title_raw, flags=re.UNICODE) if t]
    title_tokens = {normalize_key(t) for t in token_list}
    tags: list[str] = []

    # Rule: if the only game is Just Chatting -> "Общение"
    names_norm = [normalize_key(n) for n in game_names if n]
    if len(names_norm) == 1 and names_norm[0] == "just chatting":
        tags.append("Общение")

    # Rule: streams with participants -> "Кооп"
    if has_participants:
        tags.append("Кооп")

    # Rule: keywords in title
    if {"ирл", "кирл", "irl"} & title_tokens:
        tags.append("IRL")
    if "игрокон" in title_tokens and "с" in title_tokens and "@evikey" in title_tokens:
        tags.append("IRL")
    if "кукинг" in title_tokens or "кукинг" in title_norm:
        tags.append("Кукинг")

    # Genres from games
    game_genres: list[str] = []
    for gt in game_genres_texts:
        for token in parse_csv(gt):
            token = normalize_genre_token(token)
            if token:
                game_genres.append(token)
    game_genres = dedup_keep_order(game_genres)

    # Desired order:
    fixed_order = ["Общение", "Кооп", "Кукинг", "IRL"]
    fixed = [t for t in fixed_order if any(normalize_key(x) == normalize_key(t) for x in tags)]
    rest = [g for g in game_genres if normalize_key(g) not in {normalize_key(t) for t in fixed}]
    rest = sorted(rest, key=lambda x: x.casefold())

    result = dedup_keep_order(fixed + rest)
    return ", ".join(result) if result else None


@dataclass(frozen=True, slots=True)
class StreamForVodMatch:
    id: int
    date: datetime
    title: str | None


def _parse_vod_created_at(value: str) -> datetime:
    # Twitch returns ISO like "2026-01-01T12:34:56Z"
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def build_vods_index(vods: list[dict[str, Any]]) -> dict[date, list[dict[str, Any]]]:
    vods_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)

    for vod in vods:
        created_at = _parse_vod_created_at(vod.get("created_at"))
        vods_by_date[created_at.date()].append(vod)

    return dict(vods_by_date)


def extract_stream_id_from_vod(vod: dict[str, Any]) -> str | None:
    """Extract Twitch stream_id from a VOD dict. Returns None if empty."""
    sid = str(vod.get("stream_id") or "").strip()
    return sid if sid and sid != "0" else None


def is_match(stream: StreamForVodMatch, vod: dict[str, Any]) -> bool:
    vod_start = _parse_vod_created_at(vod.get("created_at"))

    # 1) Exact date match.
    if stream.date.date() == vod_start.date():
        return True

    # 2) Fallback: +-1 day + title overlap.
    delta_days = abs((stream.date.date() - vod_start.date()).days)
    if delta_days <= 1:
        s1 = (stream.title or "").strip().lower()
        s2 = str(vod.get("title") or "").strip().lower()
        if s1 and s2 and (s1 in s2 or s2 in s1):
            return True

    return False


def pick_vod_candidates(
    *,
    vods_by_date: dict[date, list[dict[str, Any]]],
    stream_date: date,
) -> list[dict[str, Any]]:
    candidates = vods_by_date.get(stream_date, [])
    if candidates:
        return candidates

    # +-1 day fallback if no exact bucket exists.
    return (vods_by_date.get(stream_date - timedelta(days=1), []) or []) + (
        vods_by_date.get(stream_date + timedelta(days=1), []) or []
    )


__all__ = [
    "StreamForVodMatch",
    "build_vods_index",
    "compute_stream_genres",
    "extract_stream_id_from_vod",
    "is_match",
    "normalize_key",
    "pick_vod_candidates",
]

