from twitchio.ext.commands import Context

from utils.censor import sanitize_outgoing_message


class SafeContext(Context):
    async def send(self, content: str):
        return await super().send(sanitize_outgoing_message(content))

    async def reply(self, content: str):
        return await super().reply(sanitize_outgoing_message(content))
