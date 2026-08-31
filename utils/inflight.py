import asyncio
from collections.abc import Awaitable, Callable

from utils.logger import get_logger

logger = get_logger("utils.inflight")

_inflight: dict[str, asyncio.Task] = {}
_last_done: dict[str, float] = {}


async def run_once(
    key: str,
    action: Callable[[], Awaitable[object]],
    *,
    cooldown: float = 0.0,
) -> bool:
    """
    Run `action` at most once per `key`.

    - Concurrent callers with the same key share a single run: only the first
      caller executes `action`, everyone else waits for the shared task and
      does nothing themselves.
    - If `cooldown` > 0, a `key` answered successfully within this many
      seconds is skipped entirely (no duplicate answer in chat). The window
      only counts successful actions and is per-key, so unrelated keys are
      never blocked.

    Returns True if THIS caller executed the action, False otherwise.
    """
    loop = asyncio.get_running_loop()
    now = loop.time()
    if cooldown > 0 and key in _last_done and now - _last_done[key] < cooldown:
        return False

    task = _inflight.get(key)
    if task is not None:
        try:
            await task
        except Exception:
            logger.exception("shared in-flight task for %r failed", key)
        return False

    task = asyncio.ensure_future(action())
    _inflight[key] = task
    ok = False
    try:
        result = await task
        ok = bool(result)
    except Exception:
        logger.exception("in-flight action for %r failed", key)
    finally:
        if _inflight.get(key) is task:
            _inflight.pop(key, None)
        if ok:
            _last_done[key] = loop.time()
    return True