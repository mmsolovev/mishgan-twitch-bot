from __future__ import annotations

"""
Google Sheets delivery: Recommendations worksheet sync (released games list).
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
)
from config.settings import RECOMMENDATIONS_SHEET_NAME
from pipeline.delivery.sheets_utils import (
    build_hyperlink_formula,
    build_recommenders_text,
    build_tags_text,
    comparable_row,
    format_rating_value,
    get_client,
    get_or_create_worksheet as _get_or_create_worksheet,
)
from services.recommendations_service import refresh_recommendation_lifecycle
from sqlalchemy import select, func


def _format_recommendations_sheet(sheet, row_count):
    if row_count <= 0:
        return

    start_row = 9
    end_row = start_row + row_count - 1

    sheet.format(
        f"A{start_row}:I{end_row}",
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

    sheet.format(
        f"B{start_row}:B{end_row}",
        {
            "numberFormat": {"type": "NUMBER", "pattern": "0"},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
        },
    )

    sheet.format(f"D{start_row}:D{end_row}", {
        "textFormat": {
            "fontFamily": "Orbitron",
            "fontSize": 14,
            "bold": True,
            "foregroundColor": {"red": 102 / 255, "green": 192 / 255, "blue": 244 / 255},
        },
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    })

    sheet.format(f"E{start_row}:E{end_row}", {
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 11,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255}, },
    })

    sheet.format(f"F{start_row}:F{end_row}", {
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 10,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255}, },
    })

    sheet.format(f"I{start_row}:I{end_row}", {
        "textFormat": {
            "fontFamily": "Montserrat",
            "fontSize": 12,
            "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255}, },
    })


async def _get_game_tags(session, game_id: int) -> tuple[str, str]:
    genres_result = await session.execute(
        select(func.coalesce(Genre.abbreviation, Genre.name))
        .join(game_genres, game_genres.c.genre_id == Genre.id)
        .where(game_genres.c.game_id == game_id)
    )
    genres_text = ", ".join(row[0] for row in genres_result.all())

    platforms_result = await session.execute(
        select(func.coalesce(Platform.abbreviation, Platform.name))
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


def _build_recommendation_row(data):
    steam = build_hyperlink_formula(data.get("steam_url"))
    platforms_text, genres_text = data.get("platforms_text", ""), data.get("genres_text", "")
    return [
        data["release_date"].strftime("%d.%m.%Y") if data.get("release_date") else "",
        data["votes_count"],
        data["name"],
        steam,
        format_rating_value(data.get("igdb_score")),
        build_tags_text(platforms_text, genres_text),
        "",
        "",
        build_recommenders_text(data.get("recommenders") or []),
    ]


async def sync_recommendations_safe() -> None:
    await refresh_recommendation_lifecycle()

    client = get_client()
    sheet = _get_or_create_worksheet(client, RECOMMENDATIONS_SHEET_NAME)

    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    now = datetime.utcnow()

    async with AsyncSessionLocal() as session:
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
                GameMetadataIGDB.release_date.is_not(None),
                GameMetadataIGDB.release_date <= now,
            )
            .order_by(GameMetadataIGDB.release_date.asc(), Game.name.asc())
        )
        games = games_result.scalars().unique().all()

        dataset_rows = []
        for game in games:
            igdb_result = await session.execute(
                select(GameMetadataIGDB).where(GameMetadataIGDB.game_id == game.id).limit(1)
            )
            igdb = igdb_result.scalar_one_or_none()

            votes_result = await session.execute(
                select(func.count()).where(game_recommendations.c.game_id == game.id)
            )
            votes_count = votes_result.scalar_one() or 0

            platforms_text, genres_text = await _get_game_tags(session, game.id)
            recommenders = await _get_game_recommenders(session, game.id)

            dataset_rows.append({
                "name": game.name,
                "release_date": igdb.release_date if igdb else None,
                "steam_url": igdb.steam_url if igdb else None,
                "igdb_score": igdb.igdb_score if igdb else None,
                "votes_count": votes_count,
                "platforms_text": platforms_text,
                "genres_text": genres_text,
                "recommenders": recommenders,
            })

    rows = [_build_recommendation_row(data) for data in dataset_rows]

    current_rows = [comparable_row(row, 9) for row in data_rows]
    comparable_final_rows = [comparable_row(row, 9) for row in rows]

    if current_rows != comparable_final_rows:
        sheet.batch_clear(["A9:I1000"])
        if rows:
            _format_recommendations_sheet(sheet, len(rows))
            sheet.update("A9", rows, value_input_option="USER_ENTERED")
        print(f"Recommendations synced: {len(rows)}")
    else:
        print("Recommendations already in sync")


__all__ = ["sync_recommendations_safe"]
