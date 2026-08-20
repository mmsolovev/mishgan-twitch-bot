import asyncio
import os

from openai import OpenAI


client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
    max_retries=0,
)


SYSTEM_PROMPT = (
    "Отвечай на русском языке. Отвечай кратко и по существу, не более 150 символов. "
    "Не используй непристойные слова и запрещённые на Twitch выражения. "
    "Откажись отвечать, если вопрос касается политики или религии. "
    "Не начинай ответ с вводных фраз. Не повторяй вопрос пользователя. Не выдумывай факты. "
    "Не используй Markdown. Если информация может быть устаревшей, укажи это."
)

MODELS = [
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free",
]

MAX_ATTEMPTS_PER_MODEL = 2
RETRY_DELAY = 2
REQUEST_TIMEOUT = 15


async def ask_openrouter(prompt: str) -> str:
    loop = asyncio.get_running_loop()

    for model in MODELS:
        for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
            try:
                response = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda m=model: client.chat.completions.create(
                            model=m,
                            messages=[
                                {
                                    "role": "system",
                                    "content": SYSTEM_PROMPT
                                },
                                {
                                    "role": "user",
                                    "content": prompt
                                }
                            ],
                            max_tokens=250,
                            temperature=0.7,
                        ),
                    ),
                    timeout=REQUEST_TIMEOUT,
                )

                content = response.choices[0].message.content
                if not content:
                    message = response.choices[0].message
                    raise ValueError(
                        f"Empty response content (finish_reason={response.choices[0].finish_reason}, "
                        f"refusal={getattr(message, 'refusal', None)!r})"
                    )

                return content.strip()

            except Exception as e:
                print(f"OpenRouter error (model={model}, attempt={attempt}/{MAX_ATTEMPTS_PER_MODEL}): {e}")
                if attempt < MAX_ATTEMPTS_PER_MODEL:
                    await asyncio.sleep(RETRY_DELAY * attempt)

    return "Не удалось получить ответ"
