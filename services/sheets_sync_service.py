from datetime import datetime
from pathlib import Path
import re

from database.db import SessionLocal
from database.models import Game, GameMeta, GameStats, RecommendedGame, Stream
from config.settings import RECOMMENDATIONS_STREAMER_LOGIN
from services.google_sheets_service import format_dt, get_client
from services.recommendations_service import STATUS_RELEASED, STATUS_UPCOMING, refresh_recommendation_lifecycle


SPREADSHEET_NAME = "Tabula Streams"
STREAMS_SHEET_NAME = "СТРИМЫ"
GAMES_SHEET_NAME = "ИГРЫ"
BOT_INFO_SHEET_NAME = "БОТ"

RELEASES_SHEET_NAME = "РЕЛИЗЫ"
RECOMMENDATIONS_SHEET_NAME = "СОВЕТЫ"
CHAT_COMMANDS_PATH = Path(__file__).resolve().parent.parent / "CHAT_COMMANDS.txt"


def _stream_display_date(stream):
    return stream.date.strftime("%d.%m.%Y\n%H:%M")


def _build_stream_row(stream):
    games = " -> ".join(stream_game.game.name for stream_game in stream.stream_games)
    participants = " ".join(participant.display_name for participant in stream.participants)
    vod = _build_hyperlink_formula(stream.vod_url, "Twitch")
    clips = _build_hyperlink_formula(stream.clips_url, "Клипы")

    return [
        _stream_display_date(stream),
        stream.duration,
        stream.title,
        games,
        vod,
        clips,
        "",
        "",
        "",
        "",
        "",
        participants,
    ]


def _normalize_row(row, width):
    return row[:width] + [""] * max(0, width - len(row))


def _get_or_create_worksheet(client, sheet_name, rows="1000", cols="20"):
    spreadsheet = client.open(SPREADSHEET_NAME)
    try:
        return spreadsheet.worksheet(sheet_name)
    except Exception:
        return spreadsheet.add_worksheet(title=sheet_name, rows=rows, cols=cols)


def _parse_sheet_bool(value):
    normalized = str(value).strip().upper()
    if normalized in {"TRUE", "ИСТИНА"}:
        return True
    if normalized in {"FALSE", "ЛОЖЬ"}:
        return False
    return None


def _build_hyperlink_formula(url, label="Steam"):
    normalized_url = str(url or "").strip()
    if not normalized_url:
        return ""

    normalized_url = re.sub(r"[\u200B-\u200D\uFEFF]", "", normalized_url)
    normalized_url = re.sub(r"[\r\n\t]+", "", normalized_url)
    normalized_url = re.sub(r"\s+", " ", normalized_url).strip()
    normalized_url = normalized_url.replace('"', "")

    safe_label = str(label).replace('"', "")
    return f'=HYPERLINK("{normalized_url}"; "{safe_label}")'


def _format_streams_sheet(sheet, row_count):
    if row_count <= 0:
        return

    start_row = 9
    end_row = start_row + row_count - 1

    requests = [{
        "unmergeCells": {
            "range": {
                "sheetId": sheet.id,
                "startRowIndex": start_row - 1,
                "endRowIndex": end_row,
                "startColumnIndex": 5,
                "endColumnIndex": 10,
            }
        }
    }]

    for row in range(start_row, end_row + 1):
        requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": sheet.id,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": 5,
                    "endColumnIndex": 10,
                },
                "mergeType": "MERGE_ALL",
            }
        })

    sheet.spreadsheet.batch_update({"requests": requests})

    sheet.format(f"A{start_row}:L{end_row}", {
        "wrapStrategy": "WRAP",
        "verticalAlignment": "MIDDLE",
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 14,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255},
        },
    })

    sheet.format(f"A{start_row}:A{end_row}", {
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })

    sheet.format(f"B{start_row}:B{end_row}", {
        "numberFormat": {
            "type": "NUMBER",
            "pattern": "0.0",
        },
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })

    sheet.format(f"F{start_row}:J{end_row}", {
        "horizontalAlignment": "LEFT",
    })


def _format_games_sheet(sheet, row_count):
    if row_count <= 0:
        return

    start_row = 9
    end_row = start_row + row_count - 1

    sheet.format(f"A{start_row}:L{end_row}", {
        "wrapStrategy": "WRAP",
        "verticalAlignment": "MIDDLE",
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 14,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255},
        },
    })

    sheet.format(f"A{start_row}:E{end_row}", {
        "horizontalAlignment": "CENTER",
    })

    requests = []
    for column_index in (7, 9):
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": sheet.id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": end_row,
                    "startColumnIndex": column_index,
                    "endColumnIndex": column_index + 1,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "showCustomUi": True,
                    "strict": True,
                },
            }
        })

    sheet.spreadsheet.batch_update({"requests": requests})


def _format_releases_sheet(sheet, row_count):
    if row_count <= 0:
        return

    start_row = 9
    end_row = start_row + row_count - 1

    requests = [{
        "unmergeCells": {
            "range": {
                "sheetId": sheet.id,
                "startRowIndex": start_row - 1,
                "endRowIndex": end_row,
                "startColumnIndex": 6,
                "endColumnIndex": 10,
            }
        }
    }]

    for row in range(start_row, end_row + 1):
        requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": sheet.id,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": 6,
                    "endColumnIndex": 10,
                },
                "mergeType": "MERGE_ALL",
            }
        })
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": sheet.id,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": 5,
                    "endColumnIndex": 6,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "showCustomUi": True,
                    "strict": True,
                },
            }
        })

    sheet.spreadsheet.batch_update({"requests": requests})

    sheet.format(f"A{start_row}:L{end_row}", {
        "wrapStrategy": "WRAP",
        "verticalAlignment": "MIDDLE",
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 14,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255},
        },
    })

    sheet.format(f"A{start_row}:B{end_row}", {
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })

    sheet.format(f"E{start_row}:E{end_row}", {
        "textFormat": {
            "fontFamily": "Orbitron",
            "fontSize": 14,
            "foregroundColor": {"red": 102 / 255, "green": 192 / 255, "blue": 244 / 255},
        },
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })

    sheet.format(f"F{start_row}:F{end_row}", {
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })

    sheet.format(f"G{start_row}:J{end_row}", {
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })


def _format_recommendations_sheet(sheet, row_count):
    if row_count <= 0:
        return

    start_row = 9
    end_row = start_row + row_count - 1

    sheet.format(f"A{start_row}:I{end_row}", {
        "wrapStrategy": "WRAP",
        "verticalAlignment": "MIDDLE",
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 14,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255},
        },
    })

    sheet.format(f"A{start_row}:B{end_row}", {
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })

    sheet.format(f"B{start_row}:B{end_row}", {
        "numberFormat": {
            "type": "NUMBER",
            "pattern": "0",
        },
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })

    sheet.format(f"D{start_row}:D{end_row}", {
        "textFormat": {
            "fontFamily": "Orbitron",
            "fontSize": 14,
            "foregroundColor": {"red": 102 / 255, "green": 192 / 255, "blue": 244 / 255},
        },
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })



def _stream_comparable_row(row):
    normalized_row = _normalize_row(row, 12)
    return [str(value) for value in normalized_row]


def _build_stream_comparable_row(stream, manual_columns=None):
    row = _build_stream_row(stream)
    comparable = [
        _stream_display_date(stream),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        str(row[4]),
        str(row[5]),
        "",
        "",
        "",
        "",
        "",
        str(row[11]),
    ]

    if manual_columns is not None:
        comparable[6:11] = [str(value) for value in manual_columns]

    return comparable


def _get_or_create_game_meta(game):
    if not game.meta:
        game.meta = GameMeta()
    return game.meta


def _sync_game_manual_fields_from_sheet(session, existing_rows):
    for game_name, row in existing_rows.items():
        game = session.query(Game).filter_by(name=game_name).first()
        if not game:
            continue

        meta = _get_or_create_game_meta(game)
        normalized_row = _normalize_row(row, 12)

        meta.liked = _parse_sheet_bool(normalized_row[7])
        meta.completed = _parse_sheet_bool(normalized_row[9])
        meta.review_url = normalized_row[11] or None

    session.flush()


def _build_games_dataset(session):
    ranked_games_data = []

    for game in session.query(Game).all():
        stats = session.query(GameStats).filter_by(
            game_id=game.id,
            period="all",
        ).first()

        if stats:
            ranked_games_data.append((game, stats))

    ranked_games_data.sort(key=lambda item: (-(item[1].hours_streamed or 0), item[0].name.casefold()))

    ranked_rows = []
    for rank, (game, stats) in enumerate(ranked_games_data, start=1):
        ranked_rows.append((game, stats, rank))

    ranked_rows.sort(
        key=lambda item: (
            item[1].last_stream is None,
            -(item[1].last_stream.timestamp()) if item[1].last_stream else 0,
            item[0].name.casefold(),
        )
    )

    return ranked_rows


def _build_game_row(game, stats, rank, manual_columns=None):
    meta = _get_or_create_game_meta(game)
    steam = _build_hyperlink_formula(meta.steam_url)

    row = [
        format_dt(stats.last_stream) if stats.last_stream else "",
        int(stats.streams_count or 0),
        game.name,
        rank,
        stats.hours_streamed or 0,
        meta.hltb_hours if meta.hltb_hours else "",
        steam,
        bool(meta.liked),
        '=IF(HROW()=TRUE;"❤️";"")',
        bool(meta.completed),
        '=IF(JROW()=TRUE;"✅";"")',
        meta.review_url or "",
    ]

    if manual_columns is not None:
        row[8] = manual_columns[0]
        row[10] = manual_columns[2]

    return row


def _finalize_game_row_formulas(rows, start_row=9):
    for offset, row in enumerate(rows):
        sheet_row = start_row + offset
        row[8] = f'=IF(H{sheet_row}=TRUE;"❤️";"")'
        row[10] = f'=IF(J{sheet_row}=TRUE;"✅";"")'
    return rows


def _game_comparable_row(row):
    normalized_row = _normalize_row(row, 12)
    comparable = []
    for value in normalized_row:
        if value is True:
            comparable.append("TRUE")
        elif value is False:
            comparable.append("FALSE")
        else:
            comparable.append(str(value))
    return comparable


def _format_release_value(recommendation):
    if not recommendation.release_date:
        return ""
    return recommendation.release_date.strftime("%d.%m.%Y\n%H:%M")


def _format_release_delta(recommendation):
    if not recommendation.release_date:
        return ""

    today = datetime.utcnow().date()
    release_day = recommendation.release_date.date()
    days = (release_day - today).days

    if days < 0:
        return ""
    if days == 0:
        return "сегодня"
    return f"{days} д."


def _build_tags_text(recommendation):
    parts = [recommendation.platforms_text, recommendation.genres_text]
    parts = [part for part in parts if part]
    return " | ".join(parts)


def _build_recommenders_text(recommendation):
    return ", ".join(
        vote.user_display_name
        for vote in recommendation.votes
        if vote.user_login != RECOMMENDATIONS_STREAMER_LOGIN.casefold()
    )


def _format_rating_value(recommendation):
    value = (recommendation.rating_text or "").strip()
    if not value:
        return ""

    return value.split("|", 1)[0].strip()


def _sync_release_manual_fields_from_sheet(session, existing_rows):
    for recommendation_name, row in existing_rows.items():
        recommendation = session.query(RecommendedGame).filter_by(title=recommendation_name).first()
        if not recommendation:
            continue

        normalized_row = _normalize_row(row, 12)
        sheet_value = _parse_sheet_bool(normalized_row[5])
        if sheet_value is True:
            recommendation.streamer_interested = True
        elif sheet_value is False and recommendation.streamer_interested is False:
            recommendation.streamer_interested = False

    session.flush()


def _release_comparable_row(row):
    normalized_row = _normalize_row(row, 12)
    comparable = []
    for value in normalized_row:
        if value is True:
            comparable.append("TRUE")
        elif value is False:
            comparable.append("FALSE")
        else:
            comparable.append(str(value))
    return comparable


def _recommendation_comparable_row(row):
    normalized_row = _normalize_row(row, 9)
    comparable = []
    for value in normalized_row:
        if value is True:
            comparable.append("TRUE")
        elif value is False:
            comparable.append("FALSE")
        else:
            comparable.append(str(value))
    return comparable


def _build_release_row(recommendation):
    steam = _build_hyperlink_formula(recommendation.steam_url)
    return [
        _format_release_value(recommendation),
        _format_release_delta(recommendation),
        recommendation.title,
        recommendation.description_short or "",
        steam,
        bool(recommendation.streamer_interested),
        "",
        "",
        "",
        "",
        _build_tags_text(recommendation),
        _build_recommenders_text(recommendation),
    ]


def _build_recommendation_row(recommendation):
    steam = _build_hyperlink_formula(recommendation.steam_url)
    return [
        recommendation.release_date.strftime("%d.%m.%Y") if recommendation.release_date else "",
        len(recommendation.votes),
        recommendation.title,
        steam,
        _format_rating_value(recommendation),
        _build_tags_text(recommendation),
        "",
        "",
        _build_recommenders_text(recommendation),
    ]


def sync_streams():
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet(STREAMS_SHEET_NAME)

    session = SessionLocal()
    streams = session.query(Stream).order_by(Stream.date.desc()).all()
    rows = [_build_stream_row(stream) for stream in streams]
    session.close()

    sheet.batch_clear(["A9:L1000"])
    if rows:
        sheet.update("A9", rows, value_input_option="USER_ENTERED")
    _format_streams_sheet(sheet, len(rows))

    print(f"Streams synced: {len(rows)}")


def sync_games():
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet(GAMES_SHEET_NAME)

    session = SessionLocal()
    rows = []
    for game, stats, rank in _build_games_dataset(session):
        rows.append(_build_game_row(game, stats, rank))
    _finalize_game_row_formulas(rows)

    session.close()

    sheet.batch_clear(["A9:L1000"])
    if rows:
        sheet.update("A9", rows, value_input_option="USER_ENTERED")
        _format_games_sheet(sheet, len(rows))

    print(f"Games synced: {len(rows)}")


def sync_streams_safe():
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet(STREAMS_SHEET_NAME)

    session = SessionLocal()

    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    existing = {}
    for row in data_rows:
        normalized_row = _normalize_row(row, 12)
        key = (normalized_row[0], normalized_row[2])
        if key[0] and key[1]:
            existing[key] = normalized_row

    streams = session.query(Stream).order_by(Stream.date.desc()).all()
    final_rows = []
    comparable_final_rows = []

    for stream in streams:
        row = _build_stream_row(stream)
        row_key = (_stream_display_date(stream), stream.title)

        if row_key in existing:
            old_row = existing[row_key]
            row[6:11] = old_row[6:11]
            comparable_row = _build_stream_comparable_row(stream, manual_columns=old_row[6:11])
        else:
            comparable_row = _build_stream_comparable_row(stream)

        final_rows.append(row)
        comparable_final_rows.append(comparable_row)

    current_rows = [_stream_comparable_row(row) for row in data_rows]

    if current_rows != comparable_final_rows:
        sheet.batch_clear(["A9:L1000"])
        if final_rows:
            sheet.update("A9", final_rows, value_input_option="USER_ENTERED")
            _format_streams_sheet(sheet, len(final_rows))
        print(f"Reordered and synced {len(final_rows)} streams")
    else:
        print("Streams already in sync")

    session.close()


def sync_games_safe():
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet(GAMES_SHEET_NAME)

    session = SessionLocal()

    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    existing = {}
    for row in data_rows:
        normalized_row = _normalize_row(row, 12)
        game_name = normalized_row[2]
        if game_name:
            existing[game_name] = normalized_row

    _sync_game_manual_fields_from_sheet(session, existing)

    final_rows = []
    for game, stats, rank in _build_games_dataset(session):
        manual_columns = None
        if game.name in existing:
            old_row = existing[game.name]
            manual_columns = [old_row[8], old_row[9], old_row[10]]
        final_rows.append(_build_game_row(game, stats, rank, manual_columns=manual_columns))
    _finalize_game_row_formulas(final_rows)

    current_rows = [_game_comparable_row(row) for row in data_rows]
    comparable_final_rows = [_game_comparable_row(row) for row in final_rows]

    if current_rows != comparable_final_rows:
        sheet.batch_clear(["A9:L1000"])
        if final_rows:
            sheet.update("A9", final_rows, value_input_option="USER_ENTERED")
            _format_games_sheet(sheet, len(final_rows))
        print(f"Reordered and synced {len(final_rows)} games")
    else:
        print("Games already in sync")

    session.commit()

    session.close()


def sync_releases_safe():
    refresh_recommendation_lifecycle()

    client = get_client()
    sheet = _get_or_create_worksheet(client, RELEASES_SHEET_NAME)

    session = SessionLocal()
    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    existing = {}
    for row in data_rows:
        normalized_row = _normalize_row(row, 12)
        title = normalized_row[2]
        if title:
            existing[title] = normalized_row

    _sync_release_manual_fields_from_sheet(session, existing)

    recommendations = (
        session.query(RecommendedGame)
        .filter(RecommendedGame.status == STATUS_UPCOMING)
        .order_by(RecommendedGame.release_date.asc(), RecommendedGame.title.asc())
        .all()
    )
    rows = []
    for offset, recommendation in enumerate(recommendations, start=9):
        row = _build_release_row(recommendation)
        row[6] = f'=IF(F{offset}=TRUE;"👍";"")'
        rows.append(row)

    current_rows = [_release_comparable_row(row) for row in data_rows]
    comparable_final_rows = [_release_comparable_row(row) for row in rows]

    if current_rows != comparable_final_rows:
        sheet.batch_clear(["A9:L1000"])
        if rows:
            sheet.update("A9", rows, value_input_option="USER_ENTERED")
            _format_releases_sheet(sheet, len(rows))

    print(f"Releases synced: {len(rows)}")
    session.commit()
    session.close()


def sync_recommendations_safe():
    refresh_recommendation_lifecycle()

    client = get_client()
    sheet = _get_or_create_worksheet(client, RECOMMENDATIONS_SHEET_NAME)

    session = SessionLocal()
    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    recommendations = (
        session.query(RecommendedGame)
        .filter(RecommendedGame.status == STATUS_RELEASED)
        .order_by(RecommendedGame.release_date.asc(), RecommendedGame.title.asc())
        .all()
    )
    rows = [_build_recommendation_row(recommendation) for recommendation in recommendations]

    current_rows = [_recommendation_comparable_row(row) for row in data_rows]
    comparable_final_rows = [_recommendation_comparable_row(row) for row in rows]

    if current_rows != comparable_final_rows:
        sheet.batch_clear(["A9:I1000"])
        if rows:
            sheet.update("A9", rows, value_input_option="USER_ENTERED")
            _format_recommendations_sheet(sheet, len(rows))
        print(f"Recommendations synced: {len(rows)}")
    else:
        print("Recommendations already in sync")

    session.close()


def sync_bot_info():
    sheet = _get_or_create_worksheet(get_client(), BOT_INFO_SHEET_NAME, rows="1000", cols="12")
    text = CHAT_COMMANDS_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    current_text = (sheet.acell("A1").value or "").replace("\r\n", "\n")

    if current_text != text:
        sheet.update("A1", [[text]], value_input_option="RAW")
        print("Bot info synced")
    else:
        print("Bot info already in sync")
