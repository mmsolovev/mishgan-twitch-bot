import json
import os
import re
from datetime import datetime

from database.db import SessionLocal
from database.models import (
    Stream,
    Game,
    GameStats,
    GameMeta,
    Participant,
)


BASE_DIR = os.path.dirname(os.path.dirname(__file__))
streams_path = os.path.join(BASE_DIR, "storage", "streams.json")
games_path = os.path.join(BASE_DIR, "storage", "games.json")


# =========================
# 🧰 HELPERS
# =========================

def parse_int(value: str) -> int:
    return int(value.replace(",", "").strip())


def parse_float_hours(value: str) -> float:
    # "8.9hrs" → 8.9
    return float(value.replace("hrs", "").strip())


def parse_datetime(value: str) -> datetime:
    # "08/Mar/2026 12:21"
    return datetime.strptime(value, "%d/%b/%Y %H:%M")


def parse_date(value: str) -> datetime:
    # "03/Sep/2024"
    return datetime.strptime(value, "%d/%b/%Y")


def extract_participants(title: str):
    # @username
    return re.findall(r"@(\w+)", title)


# =========================
# 🌱 SEED
# =========================

def seed():
    session = SessionLocal()

    # ---------- STREAMS ----------
    with open(streams_path, "r", encoding="utf-8") as f:
        streams_data = json.load(f)

    for item in streams_data:
        date = parse_datetime(item["date"])

        # 🔑 простой external_id
        external_id = f"{date.isoformat()}"

        existing = session.query(Stream).filter_by(external_id=external_id).first()
        if existing:
            continue

        stream = Stream(
            external_id=external_id,
            date=date,
            duration=parse_float_hours(item["duration"]),
            avg_viewers=parse_int(item["avg_viewers"]),
            max_viewers=parse_int(item["max_viewers"]),
            followers=parse_int(item["followers"]),
            views=parse_int(item["views"]),
            title=item["title"],
        )

        session.add(stream)

        # 🎮 games
        for game_name in item["games"]:
            game = session.query(Game).filter_by(name=game_name).first()

            if not game:
                game = Game(name=game_name)
                session.add(game)
                session.flush()  # чтобы получить id

                # сразу создаём meta (пустую)
                meta = GameMeta(game_id=game.id)
                session.add(meta)

            stream.games.append(game)

        # 👥 participants
        participants = extract_participants(item["title"])

        session.add(stream)

        for name in participants:
            norm_name = name.lower()

            participant = session.query(Participant).filter_by(name=norm_name).first()

            if not participant:
                participant = Participant(
                    name=norm_name,
                    display_name=f"@{name}",
                )
                session.add(participant)

            stream.participants.append(participant)

        session.add(stream)

    session.commit()

    # ---------- GAME STATS ----------
    with open(games_path, "r", encoding="utf-8") as f:
        games_data = json.load(f)

    for item in games_data:
        game_name = item["game"]

        game = session.query(Game).filter_by(name=game_name).first()

        if not game:
            game = Game(name=game_name)
            session.add(game)
            session.flush()

            meta = GameMeta(game_id=game.id)
            session.add(meta)

        stats = GameStats(
            game_id=game.id,
            period="all",
            hours_streamed=item["hours_streamed"],
            avg_viewers=item["avg_viewers"],
            max_viewers=item["max_viewers"],
            followers_per_hour=item["followers_per_hour"],
            streams_count=None,  # можно посчитать позже
            last_stream=parse_date(item["last_stream"]),
        )

        session.merge(stats)

    session.commit()

    session.close()
    print("✅ Seed completed")


if __name__ == "__main__":
    seed()
