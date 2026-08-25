import asyncio
import os
import re

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

DESCRIPTION_SYSTEM_PROMPT = (
    "Ты — опытный копирайтер, специализирующийся на кратких и интересных описаниях игр для русскоязычной аудитории. "
    "Твоя задача — пересказать англоязычное описание игры на русском языке, сохранив ключевые особенности и жанр. "
    "Описание должно быть лаконичным (не более 170 символов), увлекательным и написано живым языком. "
    "Не используй клише и канцеляризмы. Ответ должен содержать только сгенерированное описание, без лишних фраз. "
    "Вот примеры хороших описаний:\n"
    "Пример 1: 'Хардкорный лутер-шутер с элементами RPG. Исследуй аномальную зону, сражайся с мутантами и другими игроками, ищи артефакты и выживай.'\n"
    "Пример 2: 'Уютный симулятор жизни, где ты управляешь фермой, заводишь друзей и раскрываешь тайны маленького городка. Идеально для отдыха.'\n"
    "Пример 3: 'Динамичный рогалик, в котором ты — бог, сбежавший из подземного мира. Прорубайся через орды врагов, используя мифическое оружие и дары Олимпа.'"
)

MODELS = [
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free",
]

# Free tier limits: ~20 req/min, slower inference, occasional 429s
FREE_MODEL_RATE_LIMIT_DELAY = 3.5
MAX_ATTEMPTS_PER_MODEL = 2
RETRY_DELAY = 2
REQUEST_TIMEOUT = 30

_DESCRIPTION_MAX_LENGTH = 170
_RE_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")


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


def _is_likely_russian(text: str) -> bool:
    return bool(_RE_CYRILLIC.search(text))


async def generate_short_description(english_summary: str) -> str | None:
    """Generate a short Russian game description from English IGDB summary.

    Uses OpenRouter free models. Returns None on failure or if validation fails.
    """
    if not english_summary or not isinstance(english_summary, str):
        return None

    summary = english_summary.strip()
    if len(summary) < 10:
        return None

    loop = asyncio.get_running_loop()
    last_error = None

    for model in MODELS:
        for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
            try:
                response = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda m=model: client.chat.completions.create(
                            model=m,
                            messages=[
                                {"role": "system", "content": DESCRIPTION_SYSTEM_PROMPT},
                                {"role": "user", "content": summary},
                            ],
                            max_tokens=250,
                            temperature=0.7,
                        ),
                    ),
                    timeout=REQUEST_TIMEOUT,
                )

                content = response.choices[0].message.content
                if not content:
                    raise ValueError("Empty response content")

                raw = " ".join(content.split()).strip()

                # Check for refusal
                if any(phrase in raw.lower() for phrase in ("отказ", "не могу", "не могу помочь")):
                    last_error = f"Refusal from {model}"
                    continue

                # Validate Russian
                if not _is_likely_russian(raw):
                    last_error = f"Not Russian (model={model})"
                    continue

                # Too long - treat as failure, try next model
                if len(raw) > _DESCRIPTION_MAX_LENGTH:
                    last_error = f"Too long ({len(raw)} chars, model={model})"
                    continue

                return raw

            except asyncio.TimeoutError:
                last_error = f"Timeout (model={model})"
            except Exception as e:
                last_error = f"Error (model={model}): {e}"

            # Delay between attempts to respect free tier rate limits
            if attempt < MAX_ATTEMPTS_PER_MODEL:
                await asyncio.sleep(RETRY_DELAY * attempt)

        # Extra delay between different models (free tier rate limiting)
        await asyncio.sleep(FREE_MODEL_RATE_LIMIT_DELAY)

    print(f"generate_short_description failed: {last_error}")
    return None
