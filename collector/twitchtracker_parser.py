import os
import json
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

PAGES_DIR = os.path.join(BASE_DIR, "storage", "pages")
OUTPUT_FILE = os.path.join(BASE_DIR, "storage", "streams.json")


all_streams = []


def unique_in_order(values):
    seen = set()
    result = []

    for value in values:
        if value in seen:
            continue

        seen.add(value)
        result.append(value)

    return result


def parse_file(path):

    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    table = soup.find("table", id="streams")
    if table is None:
        return []

    streams = []

    for row in table.find_all("tr"):

        cols = row.find_all("td")

        if len(cols) != 8:
            continue

        # игры
        game_imgs = cols[7].find_all("img")

        games = [
            img.get("data-original-title")
            for img in game_imgs
            if img.get("data-original-title")
        ]
        games = unique_in_order(games)

        streams.append({
            "date": cols[0].get_text(strip=True),
            "duration": cols[1].get_text(strip=True),
            "avg_viewers": cols[2].get_text(strip=True),
            "max_viewers": cols[3].get_text(strip=True),
            "followers": cols[4].get_text(strip=True),
            "views": cols[5].get_text(strip=True),
            "title": cols[6].get_text(strip=True),
            "games": games
        })

    return streams


# перебор всех файлов
for file in sorted(os.listdir(PAGES_DIR)):

    if not file.endswith(".html"):
        continue

    path = os.path.join(PAGES_DIR, file)

    print("processing", file)

    streams = parse_file(path)

    all_streams.extend(streams)


print("total streams:", len(all_streams))


# сохраняем
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(all_streams, f, ensure_ascii=False, indent=2)
