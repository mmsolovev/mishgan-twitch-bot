from __future__ import annotations

"""
Google Sheets delivery: Releases worksheet sync (upcoming + unknown release date).
"""

from datetime import datetime

from database.db import AsyncSessionLocal
from database.models import (
    Game,
    GameMetadataIGDB,
    Genre,
    Platform,
    User,
    game_genres,
    game_platforms,
    game_recommendations,
    streamer_games,
)
from config.settings import RELEASES_SHEET_NAME
from pipeline.delivery.sheets_utils import (
    build_hyperlink_formula,
    build_recommenders_text,
    build_tags_text,
    comparable_row,
    get_client,
    get_or_create_worksheet as _get_or_create_worksheet,
)
from pipeline.transform.sheets_transform import normalize_row as _normalize_row, parse_sheet_bool
from services.recommendations_service import refresh_recommendation_lifecycle
from sqlalchemy import select, func


def _format_releases_sheet(sheet, row_count):
    if row_count <= 0:
        return

    start_row = 9
    end_row = start_row + row_count - 1

    requests = [
        {
            "unmergeCells": {
                "range": {
                    "sheetId": sheet.id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": end_row,
                    "startColumnIndex": 6,
                    "endColumnIndex": 10,
                }
            }
        }
    ]

    for row in range(start_row, end_row + 1):
        requests.append(
            {
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
            }
        )
        requests.append(
            {
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
            }
        )

    sheet.spreadsheet.batch_update({"requests": requests})

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

    sheet.format(
        f"A{start_row}:B{end_row}",
        {"horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"},
    )

    sheet.format(f"E{start_row}:E{end_row}", {
        "textFormat": {
            "fontFamily": "Orbitron",
            "fontSize": 14,
            "bold": True,
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

    sheet.format(f"D{start_row}:D{end_row}", {
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 12,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255}, },
    })

    sheet.format(f"K{start_row}:K{end_row}", {
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 10,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255}, },
    })

    sheet.format(f"L{start_row}:L{end_row}", {
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 12,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255}, },
    })


def _format_release_value(release_date):
    if not release_date:
        return ""
    return release_date.strftime("%d.%m.%Y\n%H:%M")


def _format_release_delta(release_date):
    if not release_date:
        return ""

    today = datetime.utcnow().date()
    release_day = release_date.date()
    days = (release_day - today).days

    if days < 0:
        return ""
    if days == 0:
        return "сегодня"
    return f"{days} д."


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


def _build_release_row(data, manual_columns=None):
    steam = build_hyperlink_formula(data.get("steam_url"))
    row = [
        _format_release_value(data.get("release_date")),
        _format_release_delta(data.get("release_date")),
        data["name"],
        data.get("description_ru") or "",
        steam,
        data.get("streamer_interested", False),
        "",
        "",
        "",
        "",
        build_tags_text(data.get("platforms_text"), data.get("genres_text")),
        build_recommenders_text(data.get("recommenders") or []),
    ]

    if manual_columns is not None:
        row[6:10] = manual_columns

    return row


async def _get_game_tags(session, game_id: int) -> tuple[str, str]:
    genres_result = await session.execute(
        select(Genre.name)
        .join(game_genres, game_genres.c.genre_id == Genre.id)
        .where(game_genres.c.game_id == game_id)
    )
    genres_text = ", ".join(row[0] for row in genres_result.all())

    platforms_result = await session.execute(
        select(Platform.name)
        .join(game_platforms, game_platforms.c.platform_id == Platform.id)
        .where(game_platforms.c.game_id == game_id)
    )
    platforms_text = ", ".join(row[0] for row in platforms_result.all())

    return platforms_text, genres_text


async def _get_game_recommenders(session, game_id: int) -> list[dict]:
    result = await session.execute(
        select(User.login, User.display_name)
        .join(game_recommendations, game_recommendations.c.user_id == User.id)
        .where(game_recommendations.c.game_id == game_id)
    )
    return [{"user_login": row[0], "display_name": row[1]} for row in result.all()]


async def sync_releases_safe() -> None:
    await refresh_recommendation_lifecycle()

    client = get_client()
    sheet = _get_or_create_worksheet(client, RELEASES_SHEET_NAME)

    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    existing = {}
    for row in data_rows:
        normalized_row = _normalize_row(row, 12)
        title = normalized_row[2]
        if title:
            existing[title] = normalized_row

    async with AsyncSessionLocal() as session:
        # Sync streamer_interested from sheet back to DB
        for game_name, row in existing.items():
            result = await session.execute(
                select(Game).where(Game.name == game_name).limit(1)
            )
            game = result.scalar_one_or_none()
            if not game:
                continue
            normalized_row = _normalize_row(row, 12)
            sheet_value = parse_sheet_bool(normalized_row[5])
            if sheet_value is True:
                await session.execute(
                    streamer_games.update()
                    .where(streamer_games.c.game_id == game.id, streamer_games.c.streamer_id == 1)
                    .values(interested=True)
                )
            elif sheet_value is False:
                sg_result = await session.execute(
                    select(streamer_games.c.interested)
                    .where(streamer_games.c.game_id == game.id, streamer_games.c.streamer_id == 1)
                    .limit(1)
                )
                current = sg_result.scalar_one_or_none()
                if current is False:
                    await session.execute(
                        streamer_games.update()
                        .where(streamer_games.c.game_id == game.id, streamer_games.c.streamer_id == 1)
                        .values(interested=False)
                    )
        await session.flush()

        # Query upcoming games: release_date in future OR unknown, with at least one recommender
        now = datetime.utcnow()
        subq = (
            select(game_recommendations.c.game_id)
            .group_by(game_recommendations.c.game_id)
            .having(func.count() > 0)
        )

        games_result = await session.execute(
            select(Game)
            .join(GameMetadataIGDB, GameMetadataIGDB.game_id == Game.id)
            .where(
                Game.id.in_(subq),
                (GameMetadataIGDB.release_date > now) | (GameMetadataIGDB.release_date.is_(None)),
            )
            .order_by(
                GameMetadataIGDB.release_date.is_(None),
                GameMetadataIGDB.release_date.asc(),
                Game.name.asc(),
            )
        )
        games = games_result.scalars().unique().all()

        dataset_rows = []
        for game in games:
            igdb_result = await session.execute(
                select(GameMetadataIGDB).where(GameMetadataIGDB.game_id == game.id).limit(1)
            )
            igdb = igdb_result.scalar_one_or_none()

            sg_result = await session.execute(
                select(streamer_games.c.interested)
                .where(streamer_games.c.game_id == game.id, streamer_games.c.streamer_id == 1)
                .limit(1)
            )
            interested = sg_result.scalar_one_or_none() or False

            platforms_text, genres_text = await _get_game_tags(session, game.id)
            recommenders = await _get_game_recommenders(session, game.id)

            dataset_rows.append({
                "name": game.name,
                "release_date": igdb.release_date if igdb else None,
                "steam_url": igdb.steam_url if igdb else None,
                "description_ru": igdb.description_ru if igdb else None,
                "streamer_interested": interested,
                "platforms_text": platforms_text,
                "genres_text": genres_text,
                "recommenders": recommenders,
            })

    rows = []
    for offset, data in enumerate(dataset_rows, start=9):
        row = _build_release_row(data)
        row[6] = f'=IF(F{offset}=TRUE;"👍";"")'
        rows.append(row)

    current_rows = [_release_comparable_row(row) for row in data_rows]
    comparable_final_rows = [_release_comparable_row(row) for row in rows]

    if current_rows != comparable_final_rows:
        sheet.batch_clear(["A9:L1000"])
        if rows:
            _format_releases_sheet(sheet, len(rows))
            sheet.update("A9", rows, value_input_option="USER_ENTERED")

    print(f"Releases synced: {len(rows)}")


__all__ = ["sync_releases_safe"]
