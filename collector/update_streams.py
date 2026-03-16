import os
import json
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

STREAMS_FILE = os.path.join(BASE_DIR, "storage", "streams.json")
HTML_FILE = os.path.join(BASE_DIR, "storage", "pages", "new_stream_page.html")


def parse_stream_row(row):

    cols = row.find_all("td")

    if len(cols) < 8:
        return None

    date = cols[0].find("span").get_text(strip=True)

    duration = cols[1].find("span").get_text(strip=True).replace(" ", "")
    duration = duration.replace(" hrs", "hrs")

    avg_viewers = cols[2].find("span").get_text(strip=True)

    max_viewers = cols[3].find("span").get_text(strip=True)

    followers = cols[4].find("span").get_text(strip=True)

    views = cols[5].find("span").get_text(strip=True)

    title = cols[6].get_text(strip=True)

    games = []
    for img in cols[7].find_all("img"):
        name = img.get("data-original-title")
        if name:
            games.append(name)

    return {
        "date": date,
        "duration": duration,
        "avg_viewers": avg_viewers,
        "max_viewers": max_viewers,
        "followers": followers,
        "views": views,
        "title": title,
        "games": games
    }


def load_existing():

    if not os.path.exists(STREAMS_FILE):
        return []

    with open(STREAMS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_streams(streams):

    with open(STREAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(streams, f, ensure_ascii=False, indent=2)


def update_streams():

    with open(HTML_FILE, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    rows = soup.find_all("tr")

    new_streams = []

    for row in rows:

        stream = parse_stream_row(row)

        if stream:
            new_streams.append(stream)

    existing = load_existing()

    existing_dates = {s["date"] for s in existing}

    added = 0

    for stream in new_streams:

        if stream["date"] not in existing_dates:
            existing.append(stream)
            added += 1

    save_streams(existing)

    print("new streams added:", added)


if __name__ == "__main__":
    update_streams()