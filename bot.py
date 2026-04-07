import asyncio

from core.bot import Bot
from utils.logger import configure_logging

if __name__ == "__main__":
    configure_logging()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    bot = Bot()
    bot.run()
