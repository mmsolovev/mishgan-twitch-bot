import asyncio
import json
import time

from database.db import SessionLocal
from database.models import RecommendedGame
import g4f


def generate_short_description_sync(text: str) -> str | None:
    try:
        result = g4f.ChatCompletion.create(
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

    if not result:
        return None

    result = " ".join(result.split())
    return result[:235]


async def process():
    session = SessionLocal()

    try:
        games = (
            session.query(RecommendedGame)
            .filter(RecommendedGame.description_short.is_(None))
            .all()
        )

        print(f"Найдено игр: {len(games)}")

        for i, game in enumerate(games, 1):
            try:
                if not game.source_payload:
                    continue

                payload = json.loads(game.source_payload)
                summary = payload.get("summary")

                if not summary:
                    continue

                # 🔥 вызываем g4f в отдельном потоке
                short = await asyncio.to_thread(
                    generate_short_description_sync, summary
                )

                if not short:
                    print(f"[{i}] skip: {game.title}")
                    continue

                game.description_short = short
                session.commit()

                print(f"[{i}] OK: {game.title}")

                # небольшая задержка
                await asyncio.sleep(1.5)

            except Exception as e:
                print(f"[{i}] ERROR: {game.title} -> {e}")
                continue

    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(process())
    