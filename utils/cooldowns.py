import time
from twitchio.ext.commands import Context

_cooldowns = {}


def check_cooldown(ctx: Context, command: str, timeout: int) -> bool:
    """
    True — можно выполнять
    False — еще кулдаун
    """
    # Проверяем, что у сообщения есть автор
    if not ctx.author:
        return False

    # Ключ создается на основе ID пользователя, чтобы кулдаун был индивидуальным
    key = f"{ctx.author.id}:{command}"
    now = time.time()

    last_used = _cooldowns.get(key, 0)
    if now - last_used < timeout:
        return False

    _cooldowns[key] = now
    return True
