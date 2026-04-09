import os
import json
from datetime import datetime
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

PAGES_DIR = os.path.join(BASE_DIR, "storage", "pages")
OUTPUT_FILE = os.path.join(BASE_DIR, "storage", "games.json")

all_games = []


def parse_last_stream(value):
    return datetime.strptime(value, "%d/%b/%Y")


def choose_preferred_game_stats(current, candidate):
    current_last_stream = parse_last_stream(current["last_stream"])
    candidate_last_stream = parse_last_stream(candidate["last_stream"])

    if candidate_last_stream != current_last_stream:
        return candidate if candidate_last_stream > current_last_stream else current

    if candidate["hours_streamed"] != current["hours_streamed"]:
        return candidate if candidate["hours_streamed"] > current["hours_streamed"] else current

    return candidate if candidate["rank"] < current["rank"] else current


def deduplicate_games(games):
    games_by_name = {}

    for game in games:
        existing = games_by_name.get(game["game"])
        if existing is None:
            games_by_name[game["game"]] = game
            continue

        games_by_name[game["game"]] = choose_preferred_game_stats(existing, game)

    return sorted(games_by_name.values(), key=lambda item: (item["rank"], item["game"].lower()))


def parse_file(path):

    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    table = soup.find("table", id="games")

    games = []

    for row in table.find_all("tr"):

        cols = row.find_all("td")

        if len(cols) != 7:
            continue

        # преобразуем часы из строки
        hours_text = cols[2].find("span").get_text(strip=True)
        hours = float(hours_text.replace(",", ""))

        games.append({
            "rank": int(cols[0].get_text(strip=True)),
            "game": cols[1].get_text(strip=True),
            "hours_streamed": hours,
            "avg_viewers": int(cols[3].get_text(strip=True).replace(",", "")),
            "max_viewers": int(cols[4].get_text(strip=True).replace(",", "")),
            "followers_per_hour": float(cols[5].get_text(strip=True)),
            "last_stream": cols[6].get_text(strip=True)
        })

    return games


for file in sorted(os.listdir(PAGES_DIR)):

    if not file.startswith("games_page"):
        continue

    path = os.path.join(PAGES_DIR, file)

    print("processing", file)

    games = parse_file(path)

    all_games.extend(games)

all_games = deduplicate_games(all_games)

print("total games:", len(all_games))


with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(all_games, f, ensure_ascii=False, indent=2)
