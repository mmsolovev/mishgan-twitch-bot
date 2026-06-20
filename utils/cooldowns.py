import time
from twitchio.ext.commands import Context

_cooldowns = {}
_channel_cooldowns = {}


def check_cooldown(ctx: Context, command: str, timeout: int) -> bool:
    """
    True — можно выполнять
    False — еще кулдаун

    Глобальный (канальный) кулдаун: один таймер на весь канал для команды.
    """
    now = time.monotonic()

    last_used = _channel_cooldowns.get(command, 0)
    if now - last_used < timeout:
        return False

    _channel_cooldowns[command] = now
    return True


def check_user_cooldown(ctx: Context, command: str, timeout: int) -> bool:
    """
    True — можно выполнять
    False — еще кулдаун

    Per-user cooldown: каждый пользователь имеет свой таймер для команды.
    """
    if not ctx.author:
        return False

    key = f"{ctx.author.id}:{command}"
    now = time.monotonic()

    last_used = _cooldowns.get(key, 0)
    if now - last_used < timeout:
        return False

    _cooldowns[key] = now
    return True
