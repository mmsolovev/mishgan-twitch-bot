import asyncio
import random


async def human_delay(min_delay=2.3, max_delay=5.1):
    await asyncio.sleep(random.uniform(min_delay, max_delay))
    