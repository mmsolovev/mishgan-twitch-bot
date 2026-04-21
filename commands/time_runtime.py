from __future__ import annotations

import json
from datetime import datetime, timezone

from twitchio.ext import commands

from services.command_registry import register_command
from services.runtime_stream_collector import ACTIVE_SESSION_FILE
from utils.cooldowns import check_cooldown
from utils.delays import human_delay


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    total_minutes = int(seconds // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours} ч {minutes} м"


def _load_active_session() -> dict | None:
    if not ACTIVE_SESSION_FILE.exists():
        return None
    try:
        return json.loads(ACTIVE_SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_time_message(session: dict) -> str | None:
    started_at = _parse_iso(session.get("started_at"))
    if started_at is None:
        return None

    now = datetime.now(timezone.utc)
    stream_seconds = (now - started_at).total_seconds()
    stream_part = _format_duration(stream_seconds)

    segments = session.get("game_segments") or []
    if not segments:
        # показываем текущую категорию, если сегментов нет
        category = session.get("category_name") or "?"
        return f"Стрим идет {stream_part} | [{category}] {stream_part}"

    parts: list[str] = []
    for seg in segments:
        name = seg.get("category_name") or "?"
        seg_start = _parse_iso(seg.get("started_at"))
        seg_end = _parse_iso(seg.get("ended_at")) or now
        if seg_start is None:
            continue
        dur = _format_duration((seg_end - seg_start).total_seconds())
        part = f"[{name}] {dur}"
        if parts and parts[-1].startswith(f"[{name}]"):
            # схлопываем дубликаты подряд
            parts[-1] = part
        else:
            parts.append(part)

    if not parts:
        return None

    max_segments = 6
    if len(parts) > max_segments:
        parts = ["..."] + parts[-max_segments:]

    return f"Стрим идет {stream_part} | " + " -> ".join(parts)


def setup(bot):
    register_command(
        "время",
        "Команда: !время — длительность текущего стрима и длительности категорий по ходу стрима",
        "all",
    )

    @commands.command(name="время")
    async def time_command(ctx):
        if not check_cooldown(ctx, "время", 5):
            return

        session = _load_active_session()
        if not session or session.get("status") != "active":
            await human_delay()
            await ctx.send("Сейчас стрим не идет")
            return

        message = _build_time_message(session)
        if not message:
            await human_delay()
            await ctx.send("Не смог посчитать время стрима")
            return

        await human_delay()
        await ctx.send(message)

    bot.add_command(time_command)
