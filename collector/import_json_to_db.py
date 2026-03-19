import json
import os
from datetime import datetime

from database.db import SessionLocal
from database.models import Stream, Game, GameStats

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
streams_path = os.path.join(BASE_DIR, "storage", "streams.json")
games_path = os.path.join(BASE_DIR, "storage", "games.json")

os.makedirs(os.path.join(BASE_DIR, "storage"), exist_ok=True)

def parse_date(date_str):
    return datetime.strptime(date_str, "%d/%b/%Y %H:%M")


def parse_last_stream(date_str):
    return datetime.strptime(date_str, "%d/%b/%Y")


def clean_int(value):
    return int(str(value).replace(",", ""))


def clean_float_duration(value):
    return float(value.replace("hrs", ""))


def import_streams(session):

    with open(streams_path, encoding="utf-8") as f:
        streams = json.load(f)

    for s in streams:

        # --- дата ---
        date = parse_date(s["date"])

        # --- стрим ---
        stream = Stream(
            date=date,
            duration=clean_float_duration(s["duration"]),
            avg_viewers=clean_int(s["avg_viewers"]),
            max_viewers=clean_int(s["max_viewers"]),
            followers=clean_int(s["followers"]),
            views=clean_int(s["views"]),
            title=s["title"]
        )

        session.add(stream)
        session.flush()  # чтобы получить stream.id

        # --- игры ---
        for game_name in s["games"]:

            game = session.query(Game).filter_by(name=game_name).first()

            if not game:
                game = Game(name=game_name)
                session.add(game)
                session.flush()

            stream.games.append(game)


def import_games_stats(session):

    with open(games_path, encoding="utf-8") as f:
        games = json.load(f)

    for g in games:

        game_name = g["game"]

        game = session.query(Game).filter_by(name=game_name).first()

        if not game:
            game = Game(name=game_name)
            session.add(game)
            session.flush()

        stats = GameStats(
            game_id=game.id,
            rank=g["rank"],
            hours_streamed=g["hours_streamed"],
            avg_viewers=g["avg_viewers"],
            max_viewers=g["max_viewers"],
            followers_per_hour=g["followers_per_hour"],
            last_stream=parse_last_stream(g["last_stream"])
        )

        session.merge(stats)  # чтобы обновлялось если уже есть


def run():

    session = SessionLocal()

    print("Importing streams...")
    import_streams(session)

    print("Importing game stats...")
    import_games_stats(session)

    session.commit()
    session.close()

    print("Done!")


if __name__ == "__main__":
    run()
