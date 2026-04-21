import asyncio
import random


async def custom_delay(multiplier=1, min_delay=2.3, max_delay=4.1):
    """
    Задержка на случайный промежуток времени в диапазоне [min_delay, max_delay], масштабированный множителем.

    Возвращает фактическую задержку в секундах (полезно для логирования/тестирования).
    """
    min_s = float(min_delay) * float(multiplier)
    max_s = float(max_delay) * float(multiplier)

    # Be defensive: allow passing bounds in any order.
    if max_s < min_s:
        min_s, max_s = max_s, min_s

    delay_s = random.uniform(min_s, max_s)
    await asyncio.sleep(delay_s)
    return delay_s


async def human_delay():
    return await custom_delay(1)
