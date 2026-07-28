from __future__ import annotations

"""
Load layer: writes targeting `users` table (User) and `streamers_on_stream` M2M.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User, Stream, streamers_on_stream
from pipeline.load.load_stream_games import unique_in_order
from services.user_service import get_or_create_user_by_login


def extract_participants_from_title(title: str | None) -> list[str]:
    title = title or ""
    return unique_in_order([name.lower() for name in re.findall(r"@(\w+)", title)])


async def sync_stream_participants_from_title(session: AsyncSession, stream: Stream, title: str | None) -> bool:
    desired_names = extract_participants_from_title(title)
    desired_set = set(desired_names)
    changed = False

    result = await session.execute(
        select(User).join(streamers_on_stream, streamers_on_stream.c.streamer_id == User.id)
        .where(streamers_on_stream.c.stream_id == stream.id)
    )
    current_users = result.scalars().all()
    current_by_login = {u.login: u for u in current_users}

    for login, user in list(current_by_login.items()):
        if login not in desired_set:
            await session.execute(
                streamers_on_stream.delete().where(
                    streamers_on_stream.c.stream_id == stream.id,
                    streamers_on_stream.c.streamer_id == user.id,
                )
            )
            changed = True

    existing_logins = {u.login for u in current_users if u.login in desired_set}
    for name in desired_names:
        if name in existing_logins:
            continue
        user = await get_or_create_user_by_login(session, name)
        session.add(streamers_on_stream.insert().values(stream_id=stream.id, streamer_id=user.id))
        existing_logins.add(name)
        changed = True

    return changed


__all__ = [
    "extract_participants_from_title",
    "get_or_create_user_by_login",
    "sync_stream_participants_from_title",
]
