from twitchio.ext.commands import Context

import config.settings as settings
from services import chat_sender
from utils.censor import sanitize_outgoing_message


class SafeContext(Context):
    def _uses_bot_badge(self) -> bool:
        return self.channel is not None and self.channel.name.casefold() in {
            channel.casefold() for channel in (settings.TWITCH_BOT_BADGE_CHANNELS or [])
        }

    async def send(self, content: str):
        content = sanitize_outgoing_message(content)
        if self._uses_bot_badge():
            sent = await chat_sender.send_message(self.channel.name, content)
            if sent:
                return None
        return await super().send(content)

    async def reply(self, content: str):
        return await super().reply(sanitize_outgoing_message(content))
