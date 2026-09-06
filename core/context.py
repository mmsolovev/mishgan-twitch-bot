from twitchio.ext.commands import Context
from twitchio.models.chat import SentMessage

import config.settings as settings
from services import chat_sender
from utils.censor import sanitize_outgoing_message


class SafeContext(Context):
    def _uses_bot_badge(self) -> bool:
        return (self.channel.name or "").casefold() in {
            channel.casefold() for channel in (settings.TWITCH_BOT_BADGE_CHANNELS or [])
        }

    def _track_sent(self, message_id) -> None:
        bot = getattr(self, "_bot", None)
        tracker = getattr(bot, "track_sent_message", None)
        if callable(tracker):
            tracker(message_id)

    async def send(self, content: str, *, me: bool = False) -> SentMessage | None:
        content = sanitize_outgoing_message(content)
        if self.channel.name and self._uses_bot_badge():
            sent = await chat_sender.send_message(self.channel.name, content)
            if sent:
                self._track_sent(sent)
                return None
        result = await super().send(content, me=me)
        if result is not None:
            self._track_sent(getattr(result, "id", None))
        return result

    async def reply(self, content: str, *, me: bool = False) -> SentMessage | None:
        result = await super().reply(sanitize_outgoing_message(content), me=me)
        if result is not None:
            self._track_sent(getattr(result, "id", None))
        return result