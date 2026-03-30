from database.db import SessionLocal
from database.models import Game, GameStats, Stream
from services.google_sheets_service import upload_table, format_dt, get_client
from services.sheets_header_builder import build_header



def setup_sheet(sheet_name: str):
    client = get_client()
    spreadsheet = client.open("Tabula Streams")

    try:
        sheet = spreadsheet.worksheet(sheet_name)
    except:
        sheet = spreadsheet.add_worksheet(title=sheet_name, rows="1000", cols="20")

    build_header(sheet)


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

def append_unique_rows(sheet, rows, key_indexes):
    """
    key_indexes — индексы колонок для уникальности
    """

    existing = sheet.get_all_values()

    if not existing:
        sheet.append_rows(rows)
        return

    existing_keys = set()

    for row in existing[1:]:
        key = tuple(row[i] for i in key_indexes if i < len(row))
        existing_keys.add(key)

    new_rows = []

    for row in rows:
        key = tuple(str(row[i]) for i in key_indexes)

        if key not in existing_keys:
            new_rows.append(row)

    if new_rows:
        sheet.append_rows(new_rows)
        print(f"Added {len(new_rows)} new rows")
    else:
        print("No new rows")
