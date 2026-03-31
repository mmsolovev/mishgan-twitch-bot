import json
import os
import re
from datetime import datetime

from sqlalchemy import func

from database.db import SessionLocal
from database.models import Game, GameMeta, GameStats, Participant, Stream, StreamGame

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


def unique_in_order(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def extract_participants(title):
    return unique_in_order(name.lower() for name in re.findall(r"@(\w+)", title))


def get_or_create_game(session, game_name):
    game = session.query(Game).filter_by(name=game_name).first()

    if not game:
        game = Game(name=game_name)
        session.add(game)
        session.flush()

    if not session.query(GameMeta).filter_by(game_id=game.id).first():
        game.meta = GameMeta()

    return game


def get_or_create_participant(session, participant_name):
    participant = session.query(Participant).filter_by(name=participant_name).first()

    if not participant:
        participant = Participant(
            name=participant_name,
            display_name=f"@{participant_name}",
        )
        session.add(participant)

    return participant


def sync_stream_games(session, stream, game_names):
    desired_game_names = unique_in_order(game_names)
    desired_game_name_set = set(desired_game_names)
    existing_stream_games = {stream_game.game.name: stream_game for stream_game in stream.stream_games}

    for game_name, stream_game in list(existing_stream_games.items()):
        if game_name not in desired_game_name_set:
            stream.stream_games.remove(stream_game)

    existing_stream_games = {stream_game.game.name: stream_game for stream_game in stream.stream_games}

    for position, game_name in enumerate(desired_game_names):
        game = get_or_create_game(session, game_name)
        stream_game = existing_stream_games.get(game_name)

        if stream_game:
            stream_game.position = position
        else:
            stream.stream_games.append(StreamGame(game=game, position=position))


def sync_stream_participants(session, stream, title):
    desired_participant_names = extract_participants(title)
    desired_participant_name_set = set(desired_participant_names)
    existing_participants = {participant.name: participant for participant in stream.participants}

    for participant_name, participant in list(existing_participants.items()):
        if participant_name not in desired_participant_name_set:
            stream.participants.remove(participant)

    existing_participants = {participant.name for participant in stream.participants}

    for participant_name in desired_participant_names:
        participant = get_or_create_participant(session, participant_name)
        if participant.name not in existing_participants:
            stream.participants.append(participant)
            existing_participants.add(participant.name)


def import_streams(session):
    with open(streams_path, encoding="utf-8") as f:
        streams = json.load(f)

    desired_external_ids = set()

    for s in streams:
        date = parse_date(s["date"])
        external_id = date.isoformat()
        desired_external_ids.add(external_id)

        stream = session.query(Stream).filter_by(external_id=external_id).first()
        if not stream:
            stream = session.query(Stream).filter_by(date=date, title=s["title"]).first()
        if not stream:
            stream = Stream()
            session.add(stream)

        stream.external_id = external_id
        stream.date = date
        stream.duration = clean_float_duration(s["duration"])
        stream.avg_viewers = clean_int(s["avg_viewers"])
        stream.max_viewers = clean_int(s["max_viewers"])
        stream.followers = clean_int(s["followers"])
        stream.views = clean_int(s["views"])
        stream.title = s["title"]

        session.flush()

        sync_stream_games(session, stream, s["games"])
        sync_stream_participants(session, stream, s["title"])

    for stream in session.query(Stream).all():
        if stream.external_id not in desired_external_ids:
            stream.participants.clear()
            session.delete(stream)


def update_streams_count(session):
    counts_by_game_id = dict(
        session.query(
            StreamGame.game_id,
            func.count(StreamGame.stream_id)
        )
        .group_by(StreamGame.game_id)
        .all()
    )

    stats_rows = session.query(GameStats).filter_by(period="all").all()

    for stats in stats_rows:
        stats.streams_count = counts_by_game_id.get(stats.game_id, 0)


def import_games_stats(session):
    with open(games_path, encoding="utf-8") as f:
        games = json.load(f)

    desired_game_ids = set()

    for g in games:
        game = get_or_create_game(session, g["game"])
        desired_game_ids.add(game.id)

        stats = GameStats(
            game_id=game.id,
            period="all",
            hours_streamed=g["hours_streamed"],
            avg_viewers=g["avg_viewers"],
            max_viewers=g["max_viewers"],
            followers_per_hour=g["followers_per_hour"],
            last_stream=parse_last_stream(g["last_stream"]),
            streams_count=0
        )

        session.merge(stats)

    for stats in session.query(GameStats).filter_by(period="all").all():
        if stats.game_id not in desired_game_ids:
            session.delete(stats)


def run():
    session = SessionLocal()

    print("Importing streams...")
    import_streams(session)

    print("Importing game stats...")
    import_games_stats(session)

    print("Updating streams count...")
    update_streams_count(session)

    session.commit()
    session.close()

    print("Done!")


if __name__ == "__main__":
    run()
