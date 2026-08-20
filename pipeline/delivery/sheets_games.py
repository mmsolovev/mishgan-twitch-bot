from __future__ import annotations

"""
Google Sheets delivery: Games worksheet sync.
"""

from database.db import AsyncSessionLocal
from database.models import (
    Game,
    GameStats,
    GameMetadataHLTB,
    GameMetadataIGDB,
    streamer_games,
    game_genres,
    game_platforms,
    Genre,
    Platform,
)
from config.settings import SPREADSHEET_NAME, GAMES_SHEET_NAME
from pipeline.delivery.sheets_utils import (
    build_hyperlink_formula,
    comparable_row,
    format_dt,
    get_client,
)
from pipeline.transform.sheets_transform import normalize_row as _normalize_row, parse_sheet_bool
from sqlalchemy import select, func


async def _build_tags_text(session, game_id: int) -> str:
    genres_result = await session.execute(
        select(func.coalesce(Genre.abbreviation, Genre.name))
        .join(game_genres, game_genres.c.genre_id == Genre.id)
        .where(game_genres.c.game_id == game_id)
    )
    genres = ", ".join(row[0] for row in genres_result.all())

    platforms_result = await session.execute(
        select(func.coalesce(Platform.abbreviation, Platform.name))
        .join(game_platforms, game_platforms.c.platform_id == Platform.id)
        .where(game_platforms.c.game_id == game_id)
    )
    platforms = ", ".join(row[0] for row in platforms_result.all())

    parts = [p for p in [platforms, genres] if p]
    return " | ".join(parts)


async def _get_game_streamer_flags(session, game_id: int) -> dict:
    result = await session.execute(
        select(
            func.bool_or(streamer_games.c.liked),
            func.bool_or(streamer_games.c.completed),
        ).where(streamer_games.c.game_id == game_id)
    )
    row = result.one()
    return {"liked": bool(row[0]), "completed": bool(row[1])}


def _build_games_dataset(rows):
    rows.sort(key=lambda r: (-(r["streamed_hours"] or 0), r["name"].casefold()))

    ranked = []
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        ranked.append(row)

    ranked.sort(
        key=lambda r: (
            r["last_stream"] is None,
            -(r["last_stream"].timestamp()) if r["last_stream"] else 0,
            r["name"].casefold(),
        )
    )
    return ranked


async def _sync_game_manual_fields_from_sheet(session, existing_rows):
    for game_name, row in existing_rows.items():
        result = await session.execute(
            select(Game).where(Game.name == game_name).limit(1)
        )
        game = result.scalar_one_or_none()
        if not game:
            continue

        normalized_row = _normalize_row(row, 12)
        liked = parse_sheet_bool(normalized_row[7])
        completed = parse_sheet_bool(normalized_row[9])

        await session.execute(
            streamer_games.update()
            .where(streamer_games.c.game_id == game.id, streamer_games.c.streamer_id == 1)
            .values(liked=liked, completed=completed)
        )

    await session.flush()


def _build_game_row(data, manual_columns=None):
    steam = build_hyperlink_formula(data.get("steam_url"))

    row = [
        format_dt(data["last_stream"]) if data["last_stream"] else "",
        int(data["streams_count"] or 0),
        data["name"],
        data["rank"],
        data["streamed_hours"] or 0,
        data["hltb_all_styles"] if data["hltb_all_styles"] else "",
        steam,
        data["liked"],
        '=IF(HROW()=TRUE;"❤️";"")',
        data["completed"],
        '=IF(JROW()=TRUE;"✅";"")',
        data["tags"],
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


def _format_games_sheet(sheet, row_count):
    if row_count <= 0:
        return

    start_row = 9
    end_row = start_row + row_count - 1

    sheet.format(
        f"A{start_row}:L{end_row}",
        {
            "wrapStrategy": "WRAP",
            "verticalAlignment": "MIDDLE",
            "textFormat": {
                "fontFamily": "Montserrat",
                "fontSize": 14,
                "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255},
            },
        },
    )

    sheet.format(f"A{start_row}:E{end_row}", {"horizontalAlignment": "CENTER"})

    requests = []
    for column_index in (7, 9):
        requests.append(
            {
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
            }
        )

    sheet.spreadsheet.batch_update({"requests": requests})

    sheet.format(
        f"L{start_row}:L{end_row}",
        {
            "textFormat": {
                "fontFamily": "Montserrat",
                "fontSize": 10,
                "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255},
            }
        },
    )

    sheet.format(
        f"G{start_row}:G{end_row}",
        {
            "textFormat": {
                "fontFamily": "Orbitron",
                "fontSize": 14,
                "bold": True,
                "foregroundColor": {"red": 102 / 255, "green": 192 / 255, "blue": 244 / 255},
            },
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
        },
    )


def _game_comparable_row(row):
    return comparable_row(_normalize_row(row, 12), 12)


async def sync_games() -> None:
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet(GAMES_SHEET_NAME)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Game).where(Game.is_active == True))
        games = result.scalars().unique().all()

        dataset_rows = []
        for game in games:
            stats_result = await session.execute(
                select(GameStats).where(GameStats.game_id == game.id).limit(1)
            )
            stats = stats_result.scalar_one_or_none()
            if not stats:
                continue

            igdb_result = await session.execute(
                select(GameMetadataIGDB).where(GameMetadataIGDB.game_id == game.id).limit(1)
            )
            igdb = igdb_result.scalar_one_or_none()

            hltb_result = await session.execute(
                select(GameMetadataHLTB).where(GameMetadataHLTB.game_id == game.id).limit(1)
            )
            hltb = hltb_result.scalar_one_or_none()

            flags = await _get_game_streamer_flags(session, game.id)
            tags = await _build_tags_text(session, game.id)

            dataset_rows.append({
                "name": game.name,
                "last_stream": stats.last_stream,
                "streams_count": stats.streams_count,
                "streamed_hours": stats.streamed_hours,
                "hltb_all_styles": hltb.hltb_all_styles if hltb else None,
                "steam_url": igdb.steam_url if igdb else None,
                "liked": flags["liked"],
                "completed": flags["completed"],
                "tags": tags,
            })

        ranked_rows = _build_games_dataset(dataset_rows)
        final_rows = [_build_game_row(data) for data in ranked_rows]
        _finalize_game_row_formulas(final_rows)

    sheet.batch_clear(["A9:L1000"])
    if final_rows:
        sheet.update("A9", final_rows, value_input_option="USER_ENTERED")
        _format_games_sheet(sheet, len(final_rows))

    print(f"Games synced: {len(final_rows)}")


async def sync_games_safe() -> None:
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet(GAMES_SHEET_NAME)

    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    existing = {}
    for row in data_rows:
        normalized_row = _normalize_row(row, 12)
        game_name = normalized_row[2]
        if game_name:
            existing[game_name] = normalized_row

    async with AsyncSessionLocal() as session:
        await _sync_game_manual_fields_from_sheet(session, existing)
        await session.commit()

        result = await session.execute(select(Game).where(Game.is_active == True))
        games = result.scalars().unique().all()

        dataset_rows = []
        for game in games:
            stats_result = await session.execute(
                select(GameStats).where(GameStats.game_id == game.id).limit(1)
            )
            stats = stats_result.scalar_one_or_none()
            if not stats:
                continue

            igdb_result = await session.execute(
                select(GameMetadataIGDB).where(GameMetadataIGDB.game_id == game.id).limit(1)
            )
            igdb = igdb_result.scalar_one_or_none()

            hltb_result = await session.execute(
                select(GameMetadataHLTB).where(GameMetadataHLTB.game_id == game.id).limit(1)
            )
            hltb = hltb_result.scalar_one_or_none()

            flags = await _get_game_streamer_flags(session, game.id)
            tags = await _build_tags_text(session, game.id)

            dataset_rows.append({
                "name": game.name,
                "last_stream": stats.last_stream,
                "streams_count": stats.streams_count,
                "streamed_hours": stats.streamed_hours,
                "hltb_all_styles": hltb.hltb_all_styles if hltb else None,
                "steam_url": igdb.steam_url if igdb else None,
                "liked": flags["liked"],
                "completed": flags["completed"],
                "tags": tags,
            })

        ranked_rows = _build_games_dataset(dataset_rows)
        final_rows = []
        for data in ranked_rows:
            manual_columns = None
            if data["name"] in existing:
                old_row = existing[data["name"]]
                manual_columns = [old_row[8], old_row[9], old_row[10]]
            final_rows.append(_build_game_row(data, manual_columns=manual_columns))
        _finalize_game_row_formulas(final_rows)

    current_rows = [_game_comparable_row(row) for row in data_rows]
    comparable_final_rows = [_game_comparable_row(row) for row in final_rows]

    if current_rows != comparable_final_rows:
        sheet.batch_clear(["A9:L1000"])
        if final_rows:
            _format_games_sheet(sheet, len(final_rows))
            sheet.update("A9", final_rows, value_input_option="USER_ENTERED")
        print(f"Reordered and synced {len(final_rows)} games")
    else:
        print("Games already in sync")


__all__ = ["sync_games", "sync_games_safe"]
