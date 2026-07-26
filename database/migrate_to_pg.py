"""
One-time migration script: SQLite (storage/streams.db) → PostgreSQL.

Usage:
    python database/migrate_to_pg.py

Requirements:
    - SQLite file storage/streams.db with data
    - PostgreSQL running and accessible (alembic upgrade head already applied)
    - DATABASE_URL configured in .env
"""

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from database.db import AsyncSessionLocal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = PROJECT_ROOT / "storage" / "streams.db"

_DT_FORMATS = [
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
]


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_dt(value):
    """Parse a datetime from SQLite (string or datetime) into a naive datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        for fmt in _DT_FORMATS:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def _parse_igdb_score(rating_text):
    """Extract IGDB score from rating_text like 'IGDB 86/100 | ...'."""
    if not rating_text:
        return None
    try:
        prefix = rating_text.split("IGDB ")[1]
        score_str = prefix.split("/100")[0]
        return float(score_str)
    except (IndexError, ValueError):
        return None


async def migrate_platforms(sqlite_conn, pg_session):
    """Extract unique platforms from games_meta.platforms_text → platforms + game_platforms."""
    rows = sqlite_conn.execute(
        "SELECT DISTINCT platforms_text FROM games_meta WHERE platforms_text IS NOT NULL"
    ).fetchall()

    platform_map = {}  # name → id

    for row in rows:
        raw = row["platforms_text"]
        if not raw:
            continue
        names = [p.strip() for p in raw.split(",") if p.strip()]
        for name in names:
            if name in platform_map:
                continue
            slug = name.lower().replace(" ", "-")
            result = await pg_session.execute(
                text("""
                    INSERT INTO platforms (name, slug, created_at)
                    VALUES (:name, :slug, :now)
                    ON CONFLICT (name) DO UPDATE SET slug = EXCLUDED.slug
                    RETURNING id
                """),
                {"name": name, "slug": slug, "now": utcnow()},
            )
            pid = result.scalar_one()
            platform_map[name] = pid

    return platform_map


async def migrate_genres(sqlite_conn, pg_session):
    """Extract unique genres from games_meta.genres_text → genres + game_genres."""
    rows = sqlite_conn.execute(
        "SELECT DISTINCT genres_text FROM games_meta WHERE genres_text IS NOT NULL"
    ).fetchall()

    genre_map = {}  # name → id

    for row in rows:
        raw = row["genres_text"]
        if not raw:
            continue
        names = [g.strip() for g in raw.split(",") if g.strip()]
        for name in names:
            if name in genre_map:
                continue
            slug = name.lower().replace(" ", "-")
            result = await pg_session.execute(
                text("""
                    INSERT INTO genres (name, slug, created_at)
                    VALUES (:name, :slug, :now)
                    ON CONFLICT (name) DO UPDATE SET slug = EXCLUDED.slug
                    RETURNING id
                """),
                {"name": name, "slug": slug, "now": utcnow()},
            )
            gid = result.scalar_one()
            genre_map[name] = gid

    return genre_map


async def migrate_games(sqlite_conn, pg_session):
    """Migrate games + create game_aliases (primary alias = name)."""
    rows = sqlite_conn.execute("SELECT * FROM games ORDER BY id").fetchall()
    id_mapping = {}  # old_id → new_id
    slug_counter = {}  # base_slug → count (for dedup)

    for row in rows:
        base_slug = row["name"].lower().replace(" ", "-")
        slug = base_slug
        if slug in slug_counter:
            slug_counter[slug] += 1
            slug = f"{base_slug}-{slug_counter[slug]}"
        else:
            slug_counter[slug] = 0
        result = await pg_session.execute(
            text("""
                INSERT INTO games (name, slug, created_at, updated_at)
                VALUES (:name, :slug, :now, :now)
                ON CONFLICT (name) DO UPDATE SET slug = EXCLUDED.slug
                RETURNING id
            """),
            {"name": row["name"], "slug": slug, "now": utcnow()},
        )
        game_id = result.scalar_one()
        id_mapping[row["id"]] = game_id

        # Primary alias
        normalized = row["name"].lower().strip()
        await pg_session.execute(
            text("""
                INSERT INTO game_aliases (game_id, alias, normalized_alias, is_primary, source, created_at)
                VALUES (:game_id, :alias, :normalized_alias, true, 'manual', :now)
                ON CONFLICT (game_id, normalized_alias) DO NOTHING
            """),
            {"game_id": game_id, "alias": row["name"], "normalized_alias": normalized, "now": utcnow()},
        )

    return id_mapping


async def migrate_users(sqlite_conn, pg_session):
    """Migrate participants → users (is_streamer=true) + vote users."""
    rows = sqlite_conn.execute("SELECT * FROM participants ORDER BY id").fetchall()
    id_mapping = {}  # old_id → new_id

    for row in rows:
        result = await pg_session.execute(
            text("""
                INSERT INTO users (login, display_name, twitch_url, is_streamer, created_at)
                VALUES (:login, :display_name, :twitch_url, true, :now)
                ON CONFLICT (login) DO UPDATE SET display_name = EXCLUDED.display_name
                RETURNING id
            """),
            {
                "login": row["name"],
                "display_name": row["display_name"] or f"@{row['name']}",
                "twitch_url": row["twitch_url"],
                "now": utcnow(),
            },
        )
        user_id = result.scalar_one()
        id_mapping[row["id"]] = user_id

    vote_users = sqlite_conn.execute(
        "SELECT DISTINCT user_login, user_display_name FROM recommended_game_votes"
    ).fetchall()
    for vu in vote_users:
        await pg_session.execute(
            text("""
                INSERT INTO users (login, display_name, is_streamer, created_at)
                VALUES (:login, :display_name, false, :now)
                ON CONFLICT (login) DO NOTHING
            """),
            {
                "login": vu["user_login"],
                "display_name": vu["user_display_name"] or vu["user_login"],
                "now": utcnow(),
            },
        )

    return id_mapping


async def migrate_streamers_on_stream(sqlite_conn, pg_session, user_id_mapping, stream_id_mapping):
    """Migrate stream_participants → streamers_on_stream."""
    rows = sqlite_conn.execute("SELECT * FROM stream_participants").fetchall()

    for row in rows:
        new_stream_id = stream_id_mapping.get(row["stream_id"])
        user_id = user_id_mapping.get(row["participant_id"])
        if user_id is None or new_stream_id is None:
            continue
        await pg_session.execute(
            text("""
                INSERT INTO streamers_on_stream (stream_id, streamer_id, role, created_at)
                VALUES (:stream_id, :streamer_id, 'guest', :now)
                ON CONFLICT (stream_id, streamer_id) DO NOTHING
            """),
            {"stream_id": new_stream_id, "streamer_id": user_id, "now": utcnow()},
        )


async def migrate_streams(sqlite_conn, pg_session):
    """Migrate streams + vod_url → stream_recordings (source='twitch')."""
    rows = sqlite_conn.execute("SELECT * FROM streams ORDER BY id").fetchall()
    id_mapping = {}  # old_id → new_id

    for row in rows:
        duration_min = int(row["duration"] * 60) if row["duration"] else None

        result = await pg_session.execute(
            text("""
                INSERT INTO streams (external_id, title, started_at, duration_minutes,
                                     avg_viewers, max_viewers, followers_gained, views_gained, created_at)
                VALUES (:external_id, :title, :started_at, :duration_minutes,
                        :avg_viewers, :max_viewers, :followers, :views, :now)
                ON CONFLICT (external_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    started_at = EXCLUDED.started_at
                RETURNING id
            """),
            {
                "external_id": row["external_id"],
                "title": row["title"],
                "started_at": parse_dt(row["date"]),
                "duration_minutes": duration_min,
                "avg_viewers": row["avg_viewers"],
                "max_viewers": row["max_viewers"],
                "followers": row["followers"],
                "views": row["views"],
                "now": utcnow(),
            },
        )
        stream_id = result.scalar_one()
        id_mapping[row["id"]] = stream_id

        # vod_url → stream_recordings
        if row["vod_url"]:
            await pg_session.execute(
                text("""
                    INSERT INTO stream_recordings (stream_id, source, url, created_at)
                    VALUES (:stream_id, 'twitch', :url, :now)
                """),
                {"stream_id": stream_id, "url": row["vod_url"], "now": utcnow()},
            )

    return id_mapping


async def migrate_stream_games(sqlite_conn, pg_session, stream_id_mapping, game_id_mapping):
    """Migrate stream_games (old composite PK → new autoincrement PK)."""
    rows = sqlite_conn.execute("SELECT * FROM stream_games ORDER BY stream_id, position").fetchall()

    for row in rows:
        new_stream_id = stream_id_mapping.get(row["stream_id"])
        new_game_id = game_id_mapping.get(row["game_id"])
        if new_stream_id is None or new_game_id is None:
            continue
        await pg_session.execute(
            text("""
                INSERT INTO stream_games (stream_id, game_id, position)
                VALUES (:stream_id, :game_id, :position)
                ON CONFLICT ON CONSTRAINT uq_stream_games_stream_position DO NOTHING
            """),
            {"stream_id": new_stream_id, "game_id": new_game_id, "position": row["position"]},
        )


async def migrate_game_stats(sqlite_conn, pg_session, game_id_mapping):
    """Migrate games_stats (period='all') → game_stats."""
    rows = sqlite_conn.execute(
        "SELECT * FROM games_stats WHERE period = 'all'"
    ).fetchall()

    for row in rows:
        new_game_id = game_id_mapping.get(row["game_id"])
        if new_game_id is None:
            continue
        await pg_session.execute(
            text("""
                INSERT INTO game_stats (game_id, streamed_hours, avg_viewers, max_viewers,
                                        followers_per_hour, streams_count, last_stream, synced_at)
                VALUES (:game_id, :streamed_hours, :avg_viewers, :max_viewers,
                        :followers_per_hour, :streams_count, :last_stream, :now)
                ON CONFLICT (game_id) DO UPDATE SET
                    streamed_hours = EXCLUDED.streamed_hours,
                    avg_viewers = EXCLUDED.avg_viewers,
                    max_viewers = EXCLUDED.max_viewers,
                    followers_per_hour = EXCLUDED.followers_per_hour,
                    streams_count = EXCLUDED.streams_count,
                    last_stream = EXCLUDED.last_stream,
                    synced_at = EXCLUDED.synced_at
            """),
            {
                "game_id": new_game_id,
                "streamed_hours": row["hours_streamed"],
                "avg_viewers": row["avg_viewers"],
                "max_viewers": row["max_viewers"],
                "followers_per_hour": row["followers_per_hour"],
                "streams_count": row["streams_count"],
                "last_stream": parse_dt(row["last_stream"]),
                "now": utcnow(),
            },
        )


async def migrate_game_metadata(sqlite_conn, pg_session, game_id_mapping):
    """Migrate games_meta → game_metadata_hltb + game_metadata_igdb + game_genres + game_platforms."""
    rows = sqlite_conn.execute("SELECT * FROM games_meta").fetchall()

    for row in rows:
        new_game_id = game_id_mapping.get(row["game_id"])
        if new_game_id is None:
            continue

        # HLTB
        if row["hltb_hours"] is not None:
            await pg_session.execute(
                text("""
                    INSERT INTO game_metadata_hltb (game_id, main_story_hours, synced_at)
                    VALUES (:game_id, :hours, :now)
                    ON CONFLICT (game_id) DO UPDATE SET main_story_hours = EXCLUDED.main_story_hours
                """),
                {"game_id": new_game_id, "hours": row["hltb_hours"], "now": utcnow()},
            )

        # IGDB (steam_url only)
        if row["steam_url"] is not None:
            await pg_session.execute(
                text("""
                    INSERT INTO game_metadata_igdb (game_id, igdb_id, steam_url, synced_at)
                    VALUES (:game_id, :igdb_id, :steam_url, :now)
                    ON CONFLICT (game_id) DO UPDATE SET steam_url = EXCLUDED.steam_url
                """),
                {"game_id": new_game_id, "igdb_id": f"pending-{new_game_id}", "steam_url": row["steam_url"], "now": utcnow()},
            )

        # Platforms
        if row["platforms_text"]:
            names = [p.strip() for p in row["platforms_text"].split(",") if p.strip()]
            for name in names:
                slug = name.lower().replace(" ", "-")
                result = await pg_session.execute(
                    text("""
                        INSERT INTO platforms (name, slug, created_at)
                        VALUES (:name, :slug, :now)
                        ON CONFLICT (name) DO UPDATE SET slug = EXCLUDED.slug
                        RETURNING id
                    """),
                    {"name": name, "slug": slug, "now": utcnow()},
                )
                platform_id = result.scalar_one()
                await pg_session.execute(
                    text("""
                        INSERT INTO game_platforms (game_id, platform_id)
                        VALUES (:game_id, :platform_id)
                        ON CONFLICT DO NOTHING
                    """),
                    {"game_id": new_game_id, "platform_id": platform_id},
                )

        # Genres
        if row["genres_text"]:
            names = [g.strip() for g in row["genres_text"].split(",") if g.strip()]
            for name in names:
                slug = name.lower().replace(" ", "-")
                result = await pg_session.execute(
                    text("""
                        INSERT INTO genres (name, slug, created_at)
                        VALUES (:name, :slug, :now)
                        ON CONFLICT (name) DO UPDATE SET slug = EXCLUDED.slug
                        RETURNING id
                    """),
                    {"name": name, "slug": slug, "now": utcnow()},
                )
                genre_id = result.scalar_one()
                await pg_session.execute(
                    text("""
                        INSERT INTO game_genres (game_id, genre_id)
                        VALUES (:game_id, :genre_id)
                        ON CONFLICT DO NOTHING
                    """),
                    {"game_id": new_game_id, "genre_id": genre_id},
                )


async def migrate_streamer_games(sqlite_conn, pg_session, game_id_mapping):
    """Migrate streamer_games with two data sources:
    - games_meta.liked/completed → liked, completed columns
    - game_recommendations from tabula → interested = True
    """
    result = await pg_session.execute(
        text("SELECT id FROM users WHERE login = 'tabula'")
    )
    tabula_row = result.first()
    if tabula_row is None:
        result = await pg_session.execute(
            text("SELECT id FROM users WHERE LOWER(login) = 'tabula'")
        )
        tabula_row = result.first()
    if tabula_row is None:
        print("  [WARN] User 'tabula' not found -- skipping streamer_games migration")
        return 0

    streamer_id = tabula_row[0]

    tabula_interested = set()
    tabula_recs = await pg_session.execute(text("""
        SELECT gr.game_id FROM game_recommendations gr
        JOIN users u ON u.id = gr.user_id
        WHERE u.login = 'tabula'
    """))
    for row in tabula_recs:
        tabula_interested.add(row[0])

    meta_rows = sqlite_conn.execute(
        "SELECT game_id, liked, completed FROM games_meta WHERE liked IS NOT NULL OR completed IS NOT NULL"
    ).fetchall()

    count = 0
    for row in meta_rows:
        new_game_id = game_id_mapping.get(row["game_id"])
        if new_game_id is None:
            continue
        interested = new_game_id in tabula_interested
        await pg_session.execute(
            text("""
                INSERT INTO streamer_games (streamer_id, game_id, interested, liked, completed, updated_at)
                VALUES (:streamer_id, :game_id, :interested, :liked, :completed, :now)
                ON CONFLICT (streamer_id, game_id) DO UPDATE SET
                    interested = EXCLUDED.interested,
                    liked = EXCLUDED.liked,
                    completed = EXCLUDED.completed,
                    updated_at = EXCLUDED.updated_at
            """),
            {
                "streamer_id": streamer_id,
                "game_id": new_game_id,
                "interested": interested,
                "liked": bool(row["liked"]) if row["liked"] is not None else None,
                "completed": bool(row["completed"]) if row["completed"] is not None else None,
                "now": utcnow(),
            },
        )
        count += 1

    for game_id in tabula_interested:
        exists = await pg_session.execute(text("""
            SELECT 1 FROM streamer_games WHERE streamer_id = :sid AND game_id = :gid
        """), {"sid": streamer_id, "gid": game_id})
        if exists.first() is None:
            await pg_session.execute(
                text("""
                    INSERT INTO streamer_games (streamer_id, game_id, interested, liked, completed, updated_at)
                    VALUES (:streamer_id, :game_id, true, NULL, NULL, :now)
                """),
                {"streamer_id": streamer_id, "game_id": game_id, "now": utcnow()},
            )
            count += 1

    return count


async def migrate_recommended_games_to_games(sqlite_conn, pg_session, game_id_mapping):
    """Migrate all recommended_games into games table + enrich game_metadata_igdb."""
    recs = sqlite_conn.execute("""
        SELECT id, title, normalized_name, matched_game_id, steam_url,
               release_date, description_short, rating_text, cover_url,
               source_game_id, source_payload
        FROM recommended_games
    """).fetchall()

    rec_to_game = {}  # rec_id -> pg_game_id
    slug_counter = {}

    existing = await pg_session.execute(text("SELECT slug FROM games"))
    for row in existing:
        slug_counter[row[0]] = 0

    for rec in recs:
        igdb_id = rec["source_game_id"] or f"pending-{rec['id']}"
        igdb_score = _parse_igdb_score(rec["rating_text"])
        release_dt = parse_dt(rec["release_date"])
        raw_payload = None
        if rec["source_payload"]:
            try:
                raw_payload = json.loads(rec["source_payload"])
            except (json.JSONDecodeError, TypeError):
                pass

        if rec["matched_game_id"] is not None:
            pg_game_id = game_id_mapping.get(rec["matched_game_id"])
            if pg_game_id is None:
                continue
            rec_to_game[rec["id"]] = pg_game_id
            await pg_session.execute(
                text("""
                    INSERT INTO game_metadata_igdb
                        (game_id, igdb_id, steam_url, release_date, description_ru,
                         igdb_score, cover_url, raw_payload, synced_at)
                    VALUES (:game_id, :igdb_id, :steam_url, :release_date, :description_ru,
                            :igdb_score, :cover_url, :raw_payload, :now)
                    ON CONFLICT (game_id) DO UPDATE SET
                        steam_url = COALESCE(EXCLUDED.steam_url, game_metadata_igdb.steam_url),
                        release_date = COALESCE(EXCLUDED.release_date, game_metadata_igdb.release_date),
                        description_ru = COALESCE(EXCLUDED.description_ru, game_metadata_igdb.description_ru),
                        igdb_score = COALESCE(EXCLUDED.igdb_score, game_metadata_igdb.igdb_score),
                        cover_url = COALESCE(EXCLUDED.cover_url, game_metadata_igdb.cover_url),
                        raw_payload = COALESCE(EXCLUDED.raw_payload, game_metadata_igdb.raw_payload)
                """),
                {"game_id": pg_game_id, "igdb_id": igdb_id,
                 "steam_url": rec["steam_url"], "release_date": release_dt,
                 "description_ru": rec["description_short"], "igdb_score": igdb_score,
                 "cover_url": rec["cover_url"], "raw_payload": json.dumps(raw_payload) if raw_payload else None,
                 "now": utcnow()},
            )
            continue

        slug_base = (rec["normalized_name"] or rec["title"]).lower().replace(" ", "-")
        slug = slug_base
        if slug in slug_counter:
            slug_counter[slug] += 1
            slug = f"{slug_base}-{slug_counter[slug]}"
            while slug in slug_counter:
                slug_counter[slug] += 1
                slug = f"{slug_base}-{slug_counter[slug]}"
            slug_counter[slug] = 0
        else:
            slug_counter[slug] = 0

        result = await pg_session.execute(
            text("""
                INSERT INTO games (name, slug, created_at, updated_at)
                VALUES (:name, :slug, :now, :now)
                ON CONFLICT (name) DO UPDATE SET slug = EXCLUDED.slug
                RETURNING id
            """),
            {"name": rec["title"], "slug": slug, "now": utcnow()},
        )
        pg_game_id = result.scalar_one()
        rec_to_game[rec["id"]] = pg_game_id

        normalized = rec["title"].lower().strip()
        await pg_session.execute(
            text("""
                INSERT INTO game_aliases (game_id, alias, normalized_alias, is_primary, source, created_at)
                VALUES (:game_id, :alias, :normalized_alias, true, 'igdb_recommendation', :now)
                ON CONFLICT (game_id, normalized_alias) DO NOTHING
            """),
            {"game_id": pg_game_id, "alias": rec["title"],
             "normalized_alias": normalized, "now": utcnow()},
        )

        if rec["steam_url"] or rec["description_short"] or rec["cover_url"] or raw_payload:
            await pg_session.execute(
                text("""
                    INSERT INTO game_metadata_igdb
                        (game_id, igdb_id, steam_url, release_date, description_ru,
                         igdb_score, cover_url, raw_payload, synced_at)
                    VALUES (:game_id, :igdb_id, :steam_url, :release_date, :description_ru,
                            :igdb_score, :cover_url, :raw_payload, :now)
                    ON CONFLICT (game_id) DO UPDATE SET
                        steam_url = COALESCE(EXCLUDED.steam_url, game_metadata_igdb.steam_url),
                        release_date = COALESCE(EXCLUDED.release_date, game_metadata_igdb.release_date),
                        description_ru = COALESCE(EXCLUDED.description_ru, game_metadata_igdb.description_ru),
                        igdb_score = COALESCE(EXCLUDED.igdb_score, game_metadata_igdb.igdb_score),
                        cover_url = COALESCE(EXCLUDED.cover_url, game_metadata_igdb.cover_url),
                        raw_payload = COALESCE(EXCLUDED.raw_payload, game_metadata_igdb.raw_payload)
                """),
                {"game_id": pg_game_id, "igdb_id": igdb_id,
                 "steam_url": rec["steam_url"], "release_date": release_dt,
                 "description_ru": rec["description_short"], "igdb_score": igdb_score,
                 "cover_url": rec["cover_url"], "raw_payload": json.dumps(raw_payload) if raw_payload else None,
                 "now": utcnow()},
            )

    return rec_to_game


async def migrate_game_recommendations(sqlite_conn, pg_session, rec_to_game):
    """Create game_recommendations from recommended_game_votes."""
    votes = sqlite_conn.execute("""
        SELECT rgv.user_login, rgv.recommended_game_id
        FROM recommended_game_votes rgv
    """).fetchall()

    inserted = set()
    count = 0

    for vote in votes:
        pg_game_id = rec_to_game.get(vote["recommended_game_id"])
        if pg_game_id is None:
            continue

        user_result = await pg_session.execute(
            text("SELECT id FROM users WHERE login = :login"),
            {"login": vote["user_login"]},
        )
        user_row = user_result.first()
        if user_row is None:
            continue

        key = (user_row[0], pg_game_id)
        if key in inserted:
            continue
        inserted.add(key)

        note = None
        if vote["user_login"] == "tabula":
            note = "В списке желаемого Steam"
        elif vote["user_login"] == "igdb":
            note = "Игра популярна"

        await pg_session.execute(
            text("""
                INSERT INTO game_recommendations (user_id, game_id, recommendation_note, created_at)
                VALUES (:user_id, :game_id, :note, :now)
                ON CONFLICT (user_id, game_id) DO NOTHING
            """),
            {
                "user_id": user_row[0],
                "game_id": pg_game_id,
                "note": note,
                "now": utcnow(),
            },
        )
        count += 1

    return count


async def reset_data(pg):
    """Truncate all data tables (preserve schema) for a clean re-run."""
    tables = [
        "streamer_games", "game_recommendations", "game_metadata_hltb",
        "game_metadata_igdb", "game_genres", "game_platforms",
        "game_stats", "stream_games", "streamers_on_stream", "stream_recordings",
        "streams", "game_aliases", "games", "genres", "platforms", "users",
    ]
    for t in tables:
        await pg.execute(text(f"TRUNCATE TABLE {t} CASCADE"))
    await pg.execute(text("ALTER SEQUENCE users_id_seq RESTART WITH 1"))
    await pg.execute(text("ALTER SEQUENCE games_id_seq RESTART WITH 1"))
    await pg.execute(text("ALTER SEQUENCE streams_id_seq RESTART WITH 1"))
    await pg.execute(text("ALTER SEQUENCE platforms_id_seq RESTART WITH 1"))
    await pg.execute(text("ALTER SEQUENCE genres_id_seq RESTART WITH 1"))
    await pg.commit()
    print("  All data tables truncated.")


async def migrate():
    reset = "--reset" in sys.argv
    print(f"Opening SQLite: {SQLITE_PATH}")
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    async with AsyncSessionLocal() as pg:
        if reset:
            print("\n[RESET] Truncating all data tables...")
            await reset_data(pg)
        print("\n[1/10] Migrating platforms...")
        await migrate_platforms(sqlite_conn, pg)

        print("[2/10] Migrating genres...")
        await migrate_genres(sqlite_conn, pg)

        print("[3/10] Migrating games + aliases...")
        game_id_mapping = await migrate_games(sqlite_conn, pg)

        print("[4/10] Migrating users (participants + vote users)...")
        user_id_mapping = await migrate_users(sqlite_conn, pg)

        print("[5/10] Migrating streams + stream_recordings...")
        stream_id_mapping = await migrate_streams(sqlite_conn, pg)

        print("[6/10] Migrating streamers_on_stream...")
        await migrate_streamers_on_stream(sqlite_conn, pg, user_id_mapping, stream_id_mapping)

        print("[7/10] Migrating stream_games...")
        await migrate_stream_games(sqlite_conn, pg, stream_id_mapping, game_id_mapping)

        print("[8/10] Migrating game_stats...")
        await migrate_game_stats(sqlite_conn, pg, game_id_mapping)

        print("[9/10] Migrating game_metadata (HLTB, IGDB, genres, platforms)...")
        await migrate_game_metadata(sqlite_conn, pg, game_id_mapping)

        print("[10/12] Migrating recommended_games -> games...")
        rec_to_game = await migrate_recommended_games_to_games(sqlite_conn, pg, game_id_mapping)
        print(f"  -> {len(rec_to_game)} recommended_games mapped to games")

        print("[11/12] Migrating game_recommendations...")
        rec_count = await migrate_game_recommendations(sqlite_conn, pg, rec_to_game)
        print(f"  -> {rec_count} game_recommendations records")

        print("[12/12] Migrating streamer_games (liked/completed + interested)...")
        sg_count = await migrate_streamer_games(sqlite_conn, pg, game_id_mapping)
        print(f"  -> {sg_count} streamer_games records")

        print("\nCommitting all changes...")
        await pg.commit()
        print("Done!")

    sqlite_conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
