import asyncio
import random


async def human_delay(min_delay=0.7, max_delay=1.8):
    await asyncio.sleep(random.uniform(min_delay, max_delay))
    