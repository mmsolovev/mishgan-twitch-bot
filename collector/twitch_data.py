import asyncio
import aiohttp
from datetime import datetime, timedelta
from collections import defaultdict

from sqlalchemy.orm import Session

from config.settings import CLIENT_ID, TWITCH_ACCESS_TOKEN, TWITCH_CHANNEL
from database.db import SessionLocal
from database.models import Stream


TWITCH_API = "https://api.twitch.tv/helix"


# ------------------------
# Twitch API
# ------------------------

async def fetch_user_id(session):
    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {TWITCH_ACCESS_TOKEN}",
    }

    params = {"login": TWITCH_CHANNEL}

    async with session.get(f"{TWITCH_API}/users", headers=headers, params=params) as resp:
        data = await resp.json()

    return data["data"][0]["id"]


async def fetch_vods(session, user_id):
    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {TWITCH_ACCESS_TOKEN}",
    }

    vods = []
    cursor = None

    while True:
        params = {
            "user_id": user_id,
            "type": "archive",
            "first": 100,
        }

        if cursor:
            params["after"] = cursor

        async with session.get(f"{TWITCH_API}/videos", headers=headers, params=params) as resp:
            data = await resp.json()

        vods.extend(data["data"])

        cursor = data.get("pagination", {}).get("cursor")
        if not cursor:
            break

    return vods


# ------------------------
# Matching
# ------------------------

def build_vods_index(vods):
    vods_by_date = defaultdict(list)

    for vod in vods:
        dt = datetime.fromisoformat(vod["created_at"].replace("Z", "+00:00"))
        vods_by_date[dt.date()].append(vod)

    return vods_by_date


def is_match(stream: Stream, vod) -> bool:
    vod_start = datetime.fromisoformat(vod["created_at"].replace("Z", "+00:00"))

    # 1. точное совпадение по дате
    if stream.date.date() == vod_start.date():
        return True

    # 2. fallback ±1 день + title
    delta_days = abs((stream.date.date() - vod_start.date()).days)

    if delta_days <= 1:
        if stream.title and vod["title"]:
            s1 = stream.title.lower()
            s2 = vod["title"].lower()

            if s1 in s2 or s2 in s1:
                return True

    return False


# ------------------------
# Main
# ------------------------

async def main():
    db: Session = SessionLocal()

    async with aiohttp.ClientSession() as session:
        user_id = await fetch_user_id(session)
        vods = await fetch_vods(session, user_id)

    print(f"Fetched VODs: {len(vods)}")

    # --- индекс ---
    vods_by_date = build_vods_index(vods)

    # --- множество URL ---
    vod_urls = set(v["url"] for v in vods)

    # ------------------------
    # 🧹 Удаление устаревших
    # ------------------------
    streams_with_vods = db.query(Stream).filter(Stream.vod_url.isnot(None)).all()

    removed = 0

    for stream in streams_with_vods:
        if stream.vod_url not in vod_urls:
            stream.vod_url = None
            removed += 1

    print(f"Removed outdated VODs: {removed}")

    # ------------------------
    # 🔗 Добавление новых
    # ------------------------
    streams = db.query(Stream).filter(Stream.vod_url.is_(None)).all()

    matched = 0

    for stream in streams:
        date = stream.date.date()

        candidates = vods_by_date.get(date, [])

        if not candidates:
            candidates = (
                vods_by_date.get(date - timedelta(days=1), []) +
                vods_by_date.get(date + timedelta(days=1), [])
            )

        for vod in candidates:
            if is_match(stream, vod):
                stream.vod_url = vod["url"]
                matched += 1
                print(f"Matched: {stream.id} -> {vod['url']}")
                break

    db.commit()
    db.close()

    print(f"Matched new VODs: {matched}")


if __name__ == "__main__":
    asyncio.run(main())
    