from __future__ import annotations

"""
Google Sheets delivery utilities.

This module combines:
- transport-level helpers (open/create worksheet, upload table)
- small helpers shared by multiple Sheets exporters
"""

from datetime import datetime
import re

from services.sheets_service import get_client
from config.settings import SPREADSHEET_NAME


def format_dt(dt: datetime | None, with_time: bool = False) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M") if with_time else dt.strftime("%Y-%m-%d")


def upload_table(
    *,
    sheet_name: str,
    headers: list,
    rows: list,
    spreadsheet_name: str = "Tabula Streams",
):
    """
    Universal table uploader.
    - creates worksheet if missing
    - clears sheet
    - uploads headers+rows in one update
    """
    client = get_client()
    spreadsheet = client.open(spreadsheet_name)

    try:
        sheet = spreadsheet.worksheet(sheet_name)
    except Exception:
        sheet = spreadsheet.add_worksheet(title=sheet_name, rows="1000", cols="20")

    sheet.clear()
    sheet.update("A1", [headers] + rows)
    sheet.format(
        "A1:Z1",
        {"textFormat": {"bold": True, "fontSize": 11}, "horizontalAlignment": "CENTER"},
    )

    print(f"Uploaded {len(rows)} rows to '{sheet_name}'")


def get_or_create_worksheet(client, sheet_name: str, *, rows: str = "1000", cols: str = "20"):
    spreadsheet = client.open(SPREADSHEET_NAME)
    try:
        return spreadsheet.worksheet(sheet_name)
    except Exception:
        return spreadsheet.add_worksheet(title=sheet_name, rows=rows, cols=cols)


def build_hyperlink_formula(url, label: str = "Steam") -> str:
    normalized_url = str(url or "").strip()
    if not normalized_url:
        return ""

    normalized_url = re.sub(r"[\u200B-\u200D\uFEFF]", "", normalized_url)
    normalized_url = re.sub(r"[\r\n\t]+", "", normalized_url)
    normalized_url = re.sub(r"\s+", " ", normalized_url).strip()
    normalized_url = normalized_url.replace('\"', "")

    safe_label = str(label).replace('\"', "")
    return f'=HYPERLINK(\"{normalized_url}\"; \"{safe_label}\")'


def build_tags_text(platforms_text: str = None, genres_text: str = None) -> str:
    parts = [platforms_text, genres_text]
    parts = [part for part in parts if part]
    return " | ".join(parts)


def build_recommenders_text(recommender_data: list[dict]) -> str:
    """Build recommenders text from a list of dicts with 'user_login' and 'display_name' keys."""
    tabula_present = False
    igdb_present = False
    users = []

    for entry in recommender_data or []:
        login = (entry.get("user_login") or "").casefold()
        if login == "tabula":
            tabula_present = True
            continue
        if login == "igdb":
            igdb_present = True
            continue
        display_name = entry.get("display_name")
        if display_name:
            users.append(display_name)

    result = []
    if tabula_present:
        result.append("В желаемом")
    if igdb_present:
        result.append("Хайп")
    result.extend(users)
    return ", ".join(result)


def format_rating_value(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return value.split("|", 1)[0].strip()


def comparable_row(row: list, width: int) -> list[str]:
    """
    Normalize Sheets row values into a stable comparable vector.
    """
    out: list[str] = []
    row = list(row or [])
    row = row[:width] + [""] * max(0, width - len(row))
    for value in row:
        if value is True:
            out.append("TRUE")
        elif value is False:
            out.append("FALSE")
        else:
            out.append(str(value))
    return out


__all__ = [
    "build_hyperlink_formula",
    "build_recommenders_text",
    "build_tags_text",
    "comparable_row",
    "format_dt",
    "format_rating_value",
    "get_client",
    "get_or_create_worksheet",
    "upload_table",
]

