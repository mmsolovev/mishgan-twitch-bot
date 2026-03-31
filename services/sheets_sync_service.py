from database.db import SessionLocal
from database.models import Game, GameMeta, GameStats, Stream
from services.google_sheets_service import format_dt, get_client


SPREADSHEET_NAME = "Tabula Streams"


def _sheet_datetime_formula(dt):
    return f"=DATE({dt.year},{dt.month},{dt.day})+TIME({dt.hour},{dt.minute},{dt.second})"


def _stream_display_date(stream):
    return stream.date.strftime("%d.%m.%Y\n%H:%M")


def _build_stream_row(stream):
    games = " -> ".join(stream_game.game.name for stream_game in stream.stream_games)
    participants = " ".join(participant.display_name for participant in stream.participants)
    vod = f'=HYPERLINK("{stream.vod_url}"; "Twitch")' if stream.vod_url else ""
    clips = f'=HYPERLINK("{stream.clips_url}"; "Клипы")' if stream.clips_url else ""

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


def _parse_sheet_bool(value):
    normalized = str(value).strip().upper()
    if normalized in {"TRUE", "ИСТИНА"}:
        return True
    if normalized in {"FALSE", "ЛОЖЬ"}:
        return False
    return None


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
    steam = f'=HYPERLINK("{meta.steam_url}"; "Steam")' if meta.steam_url else ""

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


def sync_streams():
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet("Стримы")

    session = SessionLocal()
    streams = session.query(Stream).order_by(Stream.date.desc()).all()
    rows = [_build_stream_row(stream) for stream in streams]
    session.close()

    sheet.batch_clear(["A9:L1000"])
    sheet.update("A9", rows, value_input_option="USER_ENTERED")
    _format_streams_sheet(sheet, len(rows))

    print(f"Streams synced: {len(rows)}")


def sync_games():
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet("Игры")

    session = SessionLocal()
    rows = []
    for game, stats, rank in _build_games_dataset(session):
        rows.append(_build_game_row(game, stats, rank))
    _finalize_game_row_formulas(rows)

    session.close()

    sheet.batch_clear(["A9:L1000"])
    sheet.update("A9", rows, value_input_option="USER_ENTERED")
    _format_games_sheet(sheet, len(rows))

    print(f"Games synced: {len(rows)}")


def sync_streams_safe():
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet("Стримы")

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
    sheet = client.open(SPREADSHEET_NAME).worksheet("Игры")

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
