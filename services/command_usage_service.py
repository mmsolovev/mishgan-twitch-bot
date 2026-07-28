"""
Command usage service: sync bot_commands from the in-memory registry
and log every successful command invocation to command_usage_logs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BotCommand, BotCommandAlias, CommandUsageLog, User
from services.command_registry import COMMANDS_INFO

logger = logging.getLogger(__name__)


async def ensure_bot_commands(session: AsyncSession) -> None:
    """UPSERT every registered command and its aliases into bot_commands / bot_command_aliases."""
    for name, info in COMMANDS_INFO.items():
        result = await session.execute(select(BotCommand).where(BotCommand.name == name))
        cmd = result.scalar_one_or_none()

        if cmd is None:
            cmd = BotCommand(
                name=name,
                description=info["description"],
                created_at=_utcnow(),
            )
            session.add(cmd)
            await session.flush()

        for alias in info.get("aliases", []):
            result = await session.execute(
                select(BotCommandAlias).where(BotCommandAlias.alias == alias)
            )
            if result.scalar_one_or_none() is None:
                session.add(BotCommandAlias(command_id=cmd.id, alias=alias))

    await session.commit()


async def log_command_usage(
    session: AsyncSession,
    user: User,
    streamer: User,
    command_name: str,
) -> None:
    """Insert a row into command_usage_logs."""
    result = await session.execute(select(BotCommand).where(BotCommand.name == command_name))
    cmd = result.scalar_one_or_none()

    if cmd is None:
        logger.warning("Command %r not found in bot_commands, skipping log", command_name)
        return

    session.add(
        CommandUsageLog(
            user_id=user.id,
            streamer_id=streamer.id,
            command_id=cmd.id,
            created_at=_utcnow(),
        )
    )
    await session.flush()
