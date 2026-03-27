import argparse
import os
from datetime import datetime

from bs4 import BeautifulSoup

from database.db import Base, SessionLocal, engine
from database.models import Game, GameStats, Stream


BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PAGES_DIR = os.path.join(BASE_DIR, "storage", "pages")


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%d/%b/%Y %H:%M")


def parse_last_stream(value: str) -> datetime:
    return datetime.strptime(value, "%d/%b/%Y")


def parse_int(value: str) -> int:
    return int(str(value).replace(",", "").strip())


def parse_float(value: str) -> float:
    return float(str(value).replace(",", "").strip())


def parse_duration(value: str) -> float:
    normalized = str(value).replace(" ", "").replace(" hrs", "hrs").replace("hrs", "")
    return float(normalized)


def iter_html_files():
    for file_name in sorted(os.listdir(PAGES_DIR)):
        if not file_name.endswith(".html"):
            continue

        yield file_name, os.path.join(PAGES_DIR, file_name)


def load_soup(path: str) -> BeautifulSoup:
    with open(path, encoding="utf-8") as f:
        return BeautifulSoup(f, "html.parser")


def parse_stream_pages() -> list[dict]:
    streams_by_date = {}

    for file_name, path in iter_html_files():
        if file_name.startswith("games_page"):
            continue

        soup = load_soup(path)
        table = soup.find("table", id="streams")
        if table is None:
            continue

        for row in table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) != 8:
                continue

            date = parse_date(cols[0].get_text(strip=True))
            games = []

            for img in cols[7].find_all("img"):
                game_name = img.get("data-original-title")
                if game_name:
                    games.append(game_name)

            streams_by_date[date] = {
                "date": date,
                "duration": parse_duration(cols[1].get_text(strip=True)),
                "avg_viewers": parse_int(cols[2].get_text(strip=True)),
                "max_viewers": parse_int(cols[3].get_text(strip=True)),
                "followers": parse_int(cols[4].get_text(strip=True)),
                "views": parse_int(cols[5].get_text(strip=True)),
                "title": cols[6].get_text(strip=True),
                "games": games,
            }

    return list(streams_by_date.values())


def parse_game_pages() -> list[dict]:
    games_by_name = {}

    for file_name, path in iter_html_files():
        if not file_name.startswith("games_page"):
            continue

        soup = load_soup(path)
        table = soup.find("table", id="games")
        if table is None:
            continue

        for row in table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) != 7:
                continue

            game_name = cols[1].get_text(strip=True)
            games_by_name[game_name] = {
                "game": game_name,
                "rank": parse_int(cols[0].get_text(strip=True)),
                "hours_streamed": parse_float(cols[2].get_text(strip=True)),
                "avg_viewers": parse_int(cols[3].get_text(strip=True)),
                "max_viewers": parse_int(cols[4].get_text(strip=True)),
                "followers_per_hour": parse_float(cols[5].get_text(strip=True)),
                "last_stream": parse_last_stream(cols[6].get_text(strip=True)),
            }

    return list(games_by_name.values())


def get_or_create_game(session, game_cache: dict[str, Game], name: str) -> Game:
    game = game_cache.get(name)
    if game is not None:
        return game

    game = Game(name=name)
    session.add(game)
    session.flush()
    game_cache[name] = game
    return game


def sync_streams(session, streams_data: list[dict], game_cache: dict[str, Game]) -> dict[str, int]:
    existing_streams = {
        stream.date: stream
        for stream in session.query(Stream).all()
    }

    stats = {"added": 0, "updated": 0, "unchanged": 0}

    for data in streams_data:
        stream = existing_streams.get(data["date"])
        created = False

        if stream is None:
            stream = Stream(date=data["date"])
            session.add(stream)
            session.flush()
            existing_streams[data["date"]] = stream
            created = True

        changed = created

        for field in ("duration", "avg_viewers", "max_viewers", "followers", "views", "title"):
            if getattr(stream, field) != data[field]:
                setattr(stream, field, data[field])
                changed = True

        desired_games = [
            get_or_create_game(session, game_cache, game_name)
            for game_name in data["games"]
        ]
        current_game_names = {game.name for game in stream.games}
        desired_game_names = {game.name for game in desired_games}

        if current_game_names != desired_game_names:
            stream.games = desired_games
            changed = True

        if created:
            stats["added"] += 1
        elif changed:
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1

    return stats


def sync_game_stats(session, games_data: list[dict], game_cache: dict[str, Game]) -> dict[str, int]:
    stats = {"added": 0, "updated": 0, "unchanged": 0}

    for data in games_data:
        game = get_or_create_game(session, game_cache, data["game"])
        game_stats = session.get(GameStats, game.id)
        created = False

        if game_stats is None:
            game_stats = GameStats(game_id=game.id)
            session.add(game_stats)
            created = True

        changed = created

        for field in ("rank", "hours_streamed", "avg_viewers", "max_viewers", "followers_per_hour", "last_stream"):
            if getattr(game_stats, field) != data[field]:
                setattr(game_stats, field, data[field])
                changed = True

        if created:
            stats["added"] += 1
        elif changed:
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1

    return stats


def run(dry_run: bool = False):
    Base.metadata.create_all(bind=engine)

    session = SessionLocal()

    try:
        streams_data = parse_stream_pages()
        games_data = parse_game_pages()
        game_cache = {game.name: game for game in session.query(Game).all()}

        stream_stats = sync_streams(session, streams_data, game_cache)
        game_stats = sync_game_stats(session, games_data, game_cache)

        if dry_run:
            session.rollback()
        else:
            session.commit()

        print(
            f"Streams -> added: {stream_stats['added']}, "
            f"updated: {stream_stats['updated']}, "
            f"unchanged: {stream_stats['unchanged']}"
        )
        print(
            f"Game stats -> added: {game_stats['added']}, "
            f"updated: {game_stats['updated']}, "
            f"unchanged: {game_stats['unchanged']}"
        )
        print("Dry run: yes" if dry_run else "Dry run: no")
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Sync TwitchTracker HTML pages directly into the SQLite database."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and compare data without writing changes to the database.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
