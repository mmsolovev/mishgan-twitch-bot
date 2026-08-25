from __future__ import annotations

"""
Transform: stream-page domain.

Functions for matching TwitchTracker stream-page data to DB streams
and to Twitch VODs (for external_id extraction).
"""

from datetime import date, datetime
from typing import Any

from pipeline.ingest.twitchtracker_parser import StreamPageData
from pipeline.transform.streams_transform import (
    StreamForVodMatch,
    build_vods_index,
    extract_stream_id_from_vod,
    is_match,
    pick_vod_candidates,
)


def build_stream_pages_index(pages: list[StreamPageData]) -> dict[date, StreamPageData]:
    """Index parsed stream pages by date (date-only key)."""
    index: dict[date, StreamPageData] = {}
    for page in pages:
        key = page.date.date() if isinstance(page.date, datetime) else page.date
        index[key] = page
    return index


def resolve_external_id(
    page: StreamPageData,
    vods_by_date: dict[date, list[dict[str, Any]]],
) -> str | None:
    """Find Twitch stream_id from matching VOD, or None if no VOD match."""
    stream_date = page.date.date() if isinstance(page.date, datetime) else page.date

    stream_for_match = StreamForVodMatch(
        id=0,
        date=datetime.combine(stream_date, datetime.min.time()),
        title=page.title_changes[0].title if page.title_changes else "",
    )

    candidates = pick_vod_candidates(vods_by_date=vods_by_date, stream_date=stream_date)
    for vod in candidates:
        if is_match(stream_for_match, vod):
            return extract_stream_id_from_vod(vod)
    return None


__all__ = [
    "build_stream_pages_index",
    "resolve_external_id",
]
