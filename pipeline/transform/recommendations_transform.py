from __future__ import annotations

"""
Transform helpers for the recommendations domain.

Normalization and status computation here are intentionally pure (no DB access).

AI short description generation is provided via `services.gpt_service` and may perform external I/O.
"""

import re
from datetime import datetime, timedelta

from services.gpt_service import generate_short_description


STATUS_UPCOMING = "upcoming"
STATUS_RELEASED = "released"
STATUS_STREAMED = "streamed"
STATUS_DELETABLE_IGDB = "deletable_igdb" # New status for IGDB recommendations to be deleted


def normalize_user_login(value: str) -> str:
    return " ".join((value or "").casefold().split())


def normalize_recommendation_name(value: str) -> str:
    normalized = " ".join((value or "").casefold().split())
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return " ".join(normalized.split())


def determine_recommendation_status(
    *,
    release_date: datetime | None,
    matched_game: "Game | None" = None,
    streamer_interested: bool = False,
    source_name: str | None = None,
) -> str:
    # streamer_interested is currently not affecting status; keep the param for call-site compatibility.
    _ = streamer_interested

    if matched_game is not None:
        return STATUS_STREAMED

    now = datetime.utcnow()
    two_days_ago = now - timedelta(days=2)
    yesterday = now - timedelta(days=1)

    if source_name == "igdb":
        if release_date is None or release_date > now:
            return STATUS_UPCOMING # IGDB future release -> RELEASES
        elif release_date >= yesterday:
            return STATUS_RELEASED # IGDB today/yesterday release -> RECOMMENDATIONS
        else:
            return STATUS_DELETABLE_IGDB # IGDB older release -> mark for deletion
    elif source_name == "tabula":
        if release_date is None or release_date > now:
            return STATUS_UPCOMING # Tabula future/unknown release -> RELEASES
        else:
            return STATUS_RELEASED # Tabula past release -> RECOMMENDATIONS
    else: # Default logic for other sources or if source_name is not provided
        if release_date is None:
            return STATUS_RELEASED
        return STATUS_UPCOMING if release_date > now else STATUS_RELEASED


__all__ = [
    "STATUS_RELEASED",
    "STATUS_STREAMED",
    "STATUS_UPCOMING",
    "STATUS_DELETABLE_IGDB",
    "determine_recommendation_status",
    "generate_short_description",
    "normalize_recommendation_name",
    "normalize_user_login",
]
