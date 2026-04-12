import asyncio
import g4f


async def ask_gpt(prompt: str) -> str:
    loop = asyncio.get_running_loop()

    def sync_request():
        return g4f.ChatCompletion.create(
            model="",
            messages=[
                {
                    "role": "system",
                    "content": "Отвечай кратко, не более 150 символов. Не используй непристойные слова и запрещённые "
                               "на Twitch выражения. Откажись отвечать, если вопрос касается политики или религии."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
        )

    return await loop.run_in_executor(None, sync_request)


async def generate_short_description(text: str) -> str | None:
    loop = asyncio.get_running_loop()

    def sync_request():
        try:
            return g4f.ChatCompletion.create(
                model="",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Переведи и кратко перескажи описание игры на русском языке. "
                            "Максимум 235 символов. Без лишней воды."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
            )
        except Exception:
            return None

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, sync_request),
            timeout=10,
        )
    except asyncio.TimeoutError:
        return None

    if not result:
        return None

    result = " ".join(result.split())
    return result[:235]
