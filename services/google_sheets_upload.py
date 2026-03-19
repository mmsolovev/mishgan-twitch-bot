from database.db import SessionLocal
from database.models import Game, GameStats, Stream
from services.google_sheets_service import upload_table, format_dt


def upload_games():

    session = SessionLocal()

    rows = []

    games = session.query(Game).all()

    for game in games:
        stats = session.query(GameStats).filter_by(game_id=game.id).first()

        if stats:
            rows.append([
                stats.rank,
                game.name,
                stats.hours_streamed,
                stats.avg_viewers,
                stats.max_viewers,
                stats.followers_per_hour,
                format_dt(stats.last_stream)
            ])

    session.close()

    upload_table(
        sheet_name="Games",
        headers=[
            "Rank", "Game", "Hours",
            "Avg viewers", "Max viewers",
            "Follows/hour", "Last stream"
        ],
        rows=rows
    )


def upload_streams():

    session = SessionLocal()

    rows = []

    streams = session.query(Stream).order_by(Stream.date.desc()).limit(100).all()

    for s in streams:
        rows.append([
            format_dt(s.date, True),
            s.duration,
            s.avg_viewers,
            s.max_viewers,
            s.followers,
            s.views,
            s.title,
            ", ".join(g.name for g in s.games)
        ])

    session.close()

    upload_table(
        sheet_name="Streams",
        headers=[
            "Date", "Duration", "Avg viewers",
            "Max viewers", "Followers", "Views",
            "Title", "Games"
        ],
        rows=rows
    )
