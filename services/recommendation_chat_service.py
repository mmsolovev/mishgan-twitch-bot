import asyncio
from dataclasses import dataclass


_BATCH_WINDOW_SECONDS = 1.2
_batches = {}


@dataclass
class _BatchEvent:
    user_display_name: str
    message: str
    accepted: bool


class _BatchState:
    def __init__(self, send_func):
        self.send_func = send_func
        self.events: list[_BatchEvent] = []
        self.task: asyncio.Task | None = None


async def queue_recommendation_chat_message(channel_key: str, user_display_name: str, message: str, accepted: bool) -> None:
    batch = _batches.get(channel_key)
    if batch is None:
        raise RuntimeError("Recommendation chat batch is not initialized")

    batch.events.append(_BatchEvent(user_display_name=user_display_name, message=message, accepted=accepted))
    await batch.task


def ensure_recommendation_batch(channel_key: str, send_func) -> None:
    batch = _batches.get(channel_key)
    if batch is not None:
        return

    batch = _BatchState(send_func=send_func)
    batch.task = asyncio.create_task(_flush_batch(channel_key, batch))
    _batches[channel_key] = batch


async def _flush_batch(channel_key: str, batch: _BatchState) -> None:
    await asyncio.sleep(_BATCH_WINDOW_SECONDS)

    events = batch.events[:]
    _batches.pop(channel_key, None)
    if not events:
        return

    if len(events) == 1:
        await batch.send_func(events[0].message)
        return

    accepted_events = [event for event in events if event.accepted]
    if len(accepted_events) >= 2:
        users = ", ".join(event.user_display_name for event in accepted_events)
        await batch.send_func(f"{users} | добавлено игр {len(accepted_events)}")
        return

    for event in events:
        await batch.send_func(event.message)
