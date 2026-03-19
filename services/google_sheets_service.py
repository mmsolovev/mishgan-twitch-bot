import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials


BASE_DIR = os.path.dirname(os.path.dirname(__file__))
credentials_path = os.path.join(BASE_DIR, "config", "credentials.json")


def get_client():

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        credentials_path,
        scope
    )

    return gspread.authorize(creds)


def format_dt(dt, with_time=False):
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M") if with_time else dt.strftime("%Y-%m-%d")


def upload_table(
    sheet_name: str,
    headers: list,
    rows: list,
    spreadsheet_name: str = "Twitch Stats"
):
    """
    Универсальная загрузка таблицы

    sheet_name — имя листа (например: 'Games')
    headers — список заголовков
    rows — список списков
    """

    client = get_client()
    spreadsheet = client.open(spreadsheet_name)

    try:
        sheet = spreadsheet.worksheet(sheet_name)
    except:
        sheet = spreadsheet.add_worksheet(title=sheet_name, rows="1000", cols="20")

    # очищаем
    sheet.clear()

    # записываем ВСЁ одним запросом (без лимитов)
    sheet.update("A1", [headers] + rows)

    # оформление заголовка
    sheet.format("A1:Z1", {
        "textFormat": {"bold": True, "fontSize": 11},
        "horizontalAlignment": "CENTER"
    })

    print(f"Uploaded {len(rows)} rows to '{sheet_name}'")
