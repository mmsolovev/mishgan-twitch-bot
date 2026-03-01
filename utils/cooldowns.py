import time


_cooldowns = {}


def check_cooldown(user: str, command: str, timeout: int) -> bool:
    """
    True — можно выполнять
    False — еще кулдаун
    """
    key = f"{user}:{command}"
    now = time.time()

    last_used = _cooldowns.get(key, 0)
    if now - last_used < timeout:
        return False

    _cooldowns[key] = now
    return True
