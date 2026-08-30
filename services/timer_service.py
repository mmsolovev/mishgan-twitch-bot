import asyncio
import re
import time
from dataclasses import dataclass

MAX_SECONDS = 8 * 60 * 60
RETURN_TIMER_SECONDS = 115 * 60


@dataclass
class _Timer:
    end: float
    text: str
    task: asyncio.Task


# единственный глобальный таймер
_current: _Timer | None = None
# отдельный таймер возврата Steam (1ч 55м, предупреждения по остатку)
_return: _Timer | None = None


def parse_time(time_str: str) -> int | None:
    match = re.match(r"(\d+)([а-яa-z]*)", time_str.lower())
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)

    if unit in ("", "м", "мин", "m", "min"):
        return value * 60
    if unit in ("ч", "h"):
        return value * 3600

    return None


def _remaining_seconds(timer: _Timer) -> int:
    return max(0, int(timer.end - time.time()))


def format_time_left(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} м")
    if secs or not parts:
        parts.append(f"{secs} с")
    return " ".join(parts)


def _prune(timer: _Timer | None):
    if timer is not None and time.time() >= timer.end:
        return None
    return timer


def build_status_message() -> str:
    global _current, _return

    _current = _prune(_current)
    _return = _prune(_return)

    parts = []
    if _current is not None:
        parts.append(f"Таймер: {format_time_left(_remaining_seconds(_current))}")
    if _return is not None:
        parts.append(f"Возврат: {format_time_left(_remaining_seconds(_return))}")

    if not parts:
        return "HUH Активных таймеров нет"
    return "⏳ " + " | ".join(parts)


async def start_timer(ctx, seconds: int, text: str):
    global _current

    if _current is not None:
        _current.task.cancel()

    end_time = time.time() + seconds
    task = asyncio.create_task(_timer_task(ctx, seconds, text))
    _current = _Timer(end=end_time, text=text, task=task)

    print(f"[TIMER START] {seconds}s | {text}")
    await ctx.send(f"NOTED Таймер на {format_time_left(seconds)} запущен")


async def _timer_task(ctx, seconds: int, text: str):
    try:
        await asyncio.sleep(seconds)
        await ctx.send(text)
        await asyncio.sleep(1)
        await ctx.send(text)
    except asyncio.CancelledError:
        pass
    finally:
        global _current
        if _current is not None and _current.task is asyncio.current_task():
            _current = None
        print("[TIMER END]")


async def stop_timer(ctx):
    global _current, _return

    # сначала обычный таймер, потом таймер возврата
    if _current is not None:
        _current.task.cancel()
        _current = None
        await ctx.send("peepoLeave Таймер остановлен")
        return

    if _return is not None:
        _return.task.cancel()
        _return = None
        await ctx.send("peepoLeave Таймер возврата остановлен")
        return

    await ctx.send("HUH Активных таймеров нет")


async def start_return_timer(ctx):
    global _return

    if _return is not None:
        remaining = format_time_left(_remaining_seconds(_return))
        await ctx.send(f"MadgeTime Таймер возврата уже запущен, осталось {remaining}")
        return

    end_time = time.time() + RETURN_TIMER_SECONDS
    task = asyncio.create_task(_return_timer_task(ctx))
    _return = _Timer(end=end_time, text="STEAM RETURN", task=task)

    print("[TIMER RETURN START]")
    await ctx.send("NOTED Таймер возврата Steam запущен (1ч 55м)")


async def _return_timer_task(ctx):
    try:
        # остаток 115 мин - стартовое сообщение (отметка 1:55)
        await asyncio.sleep(55 * 60)
        await ctx.send("MrDestructoid ALERT ЧАС ДО ВОЗВРАТА ALERT")

        await asyncio.sleep(30 * 60)
        await ctx.send("MrDestructoid ALERT ПОЛЧАСА ДО ВОЗВРАТА ALERT")

        await asyncio.sleep(30 * 60)
        await ctx.send("MrDestructoid ALERT ВРЕМЯ ВОЗВРАТА ALERT")

    except asyncio.CancelledError:
        pass
    finally:
        global _return
        if _return is not None and _return.task is asyncio.current_task():
            _return = None
        print("[TIMER RETURN END]")


async def stop_return_timer(ctx):
    global _return

    if _return is not None:
        _return.task.cancel()
        _return = None
        await ctx.send("MrDestructoid Таймер возврата остановлен 1984")
        return

    await ctx.send("MrDestructoid Таймер возврата не запущен")