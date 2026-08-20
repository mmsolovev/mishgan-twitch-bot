from __future__ import annotations

"""
Transform layer: shared pure helpers (no DB access, no external I/O).

Keep this module small and focused: only cross-domain utilities that are
actively shared by multiple transform modules.
"""

from typing import Any


def normalize_key(value: str) -> str:
    return " ".join((value or "").casefold().split())


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def normalize_genre_token(token: str) -> str:
    return token.strip()


def dedup_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = normalize_key(v)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def normalize_genres_text(genres_text: str | None) -> str | None:
    tokens = [normalize_genre_token(t) for t in parse_csv(genres_text)]
    out = dedup_keep_order(tokens)
    return ", ".join(out) if out else None


__all__ = [
    "dedup_keep_order",
    "is_blank",
    "normalize_genre_token",
    "normalize_genres_text",
    "normalize_key",
    "parse_csv",
]

