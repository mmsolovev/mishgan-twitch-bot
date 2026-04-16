import asyncio
import random


async def custom_delay(multiplier=1, min_delay=2.3, max_delay=5.1):
    min_delay *= multiplier
    max_delay *= multiplier
    await asyncio.sleep(random.uniform(min_delay, max_delay))


async def human_delay():
    await custom_delay(1)
