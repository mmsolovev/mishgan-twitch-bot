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
