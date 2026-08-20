from __future__ import annotations

"""
Google Sheets delivery: Streams worksheet sync.
"""

from database.db import AsyncSessionLocal
from database.models import Stream, StreamGame, User, StreamRecording, streamers_on_stream
from config.settings import SPREADSHEET_NAME, STREAMS_SHEET_NAME
from pipeline.delivery.sheets_utils import build_hyperlink_formula, get_client
from pipeline.transform.sheets_transform import normalize_row as _normalize_row
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


def _stream_display_date(stream: Stream) -> str:
    return stream.started_at.strftime("%d.%m.%Y\n%H:%M")


async def _build_stream_row(session: AsyncSession, stream: Stream) -> list:
    games = " -> ".join(
        sg.game.name for sg in stream.stream_games if sg.game
    )

    result = await session.execute(
        select(User.display_name)
        .join(streamers_on_stream, streamers_on_stream.c.streamer_id == User.id)
        .where(streamers_on_stream.c.stream_id == stream.id)
    )
    participants = " ".join(row[0] for row in result.all() if row[0])

    vod_result = await session.execute(
        select(StreamRecording.url)
        .where(StreamRecording.stream_id == stream.id, StreamRecording.source == "twitch")
        .limit(1)
    )
    vod_url = vod_result.scalar_one_or_none()

    duration_str = ""
    if stream.duration_minutes is not None:
        hours = stream.duration_minutes / 60
        duration_str = f"{hours:.1f}".rstrip("0").rstrip(".")

    return [
        _stream_display_date(stream),
        duration_str,
        stream.title or "",
        games,
        build_hyperlink_formula(vod_url, "Twitch") if vod_url else "",
        "",
        "",
        "",
        "",
        "",
        "",
        participants,
    ]


def _format_streams_sheet(sheet, row_count: int) -> None:
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
                    "startColumnIndex": 5,
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
                        "startColumnIndex": 5,
                        "endColumnIndex": 10,
                    },
                    "mergeType": "MERGE_ALL",
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
        f"A{start_row}:A{end_row}",
        {"horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"},
    )

    sheet.format(
        f"B{start_row}:B{end_row}",
        {
            "numberFormat": {"type": "NUMBER", "pattern": "0.0"},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
        },
    )

    sheet.format(f"F{start_row}:J{end_row}", {"horizontalAlignment": "LEFT"})

    sheet.format(
        f"K{start_row}:K{end_row}",
        {
            "textFormat": {
                "fontFamily": "Montserrat",
                "fontSize": 10,
                "foregroundColor": {"red": 229 / 255, "green": 231 / 255, "blue": 235 / 255},
            }
        },
    )

    sheet.format(
        f"E{start_row}:E{end_row}",
        {
            "textFormat": {
                "fontFamily": "Orbitron",
                "fontSize": 14,
                "bold": True,
                "foregroundColor": {"red": 153 / 255, "green": 76 / 255, "blue": 255 / 255},
            },
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
        },
    )


def _stream_comparable_row(row):
    normalized_row = _normalize_row(row, 12)
    return [str(value) for value in normalized_row]


def _build_stream_comparable_row(row: list, manual_columns=None) -> list[str]:
    comparable = [str(value) for value in row]
    if manual_columns is not None:
        comparable[6:10] = [str(value) for value in manual_columns]
    return comparable


async def sync_streams() -> None:
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet(STREAMS_SHEET_NAME)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Stream)
            .options(selectinload(Stream.stream_games).selectinload(StreamGame.game))
            .order_by(Stream.started_at.desc())
        )
        streams = result.scalars().unique().all()
        rows = [await _build_stream_row(session, stream) for stream in streams]

    sheet.batch_clear(["A9:L1000"])
    if rows:
        sheet.update("A9", rows, value_input_option="USER_ENTERED")
    _format_streams_sheet(sheet, len(rows))

    print(f"Streams synced: {len(rows)}")


async def sync_streams_safe() -> None:
    client = get_client()
    sheet = client.open(SPREADSHEET_NAME).worksheet(STREAMS_SHEET_NAME)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Stream)
            .options(selectinload(Stream.stream_games).selectinload(StreamGame.game))
            .order_by(Stream.started_at.desc())
        )
        streams = result.scalars().unique().all()

    values = sheet.get_all_values()
    data_rows = values[8:] if len(values) > 8 else []

    existing = {}
    for row in data_rows:
        normalized_row = _normalize_row(row, 12)
        key = (normalized_row[0], normalized_row[2])
        if key[0] and key[1]:
            existing[key] = normalized_row

    final_rows = []
    comparable_final_rows = []

    async with AsyncSessionLocal() as session:
        for stream in streams:
            row = await _build_stream_row(session, stream)
            row_key = (_stream_display_date(stream), stream.title)
            if row_key in existing:
                old_row = existing[row_key]
                row[6:10] = old_row[6:10]
                comp_row = _build_stream_comparable_row(row, manual_columns=old_row[6:10])
            else:
                comp_row = _build_stream_comparable_row(row)
            final_rows.append(row)
            comparable_final_rows.append(comp_row)

    current_rows = [_stream_comparable_row(row) for row in data_rows]

    if current_rows != comparable_final_rows:
        sheet.batch_clear(["A9:L1000"])
        if final_rows:
            _format_streams_sheet(sheet, len(final_rows))
            sheet.update("A9", final_rows, value_input_option="USER_ENTERED")
        print(f"Reordered and synced {len(final_rows)} streams")
    else:
        print("Streams already in sync")


__all__ = ["sync_streams", "sync_streams_safe"]
