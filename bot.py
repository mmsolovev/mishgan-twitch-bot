import asyncio

from core.bot import Bot
from services.token_service import ensure_valid_token
from utils.logger import configure_logging

if __name__ == "__main__":
    configure_logging()

    new_token = asyncio.run(ensure_valid_token())
    import config.settings as settings
    settings.TWITCH_ACCESS_TOKEN = new_token
    settings.TWITCH_TOKEN = f"oauth:{new_token}"

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    bot = Bot()
    bot.run()