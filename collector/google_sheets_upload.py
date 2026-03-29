from services.google_sheets_service import get_client
from services.sheets_header_builder import build_header


def run():
    client = get_client()
    spreadsheet = client.open("Twitch Stats")

    # выбери нужный лист
    sheet = spreadsheet.worksheet("Стримы")  # или "Игры"

    build_header(sheet)


if __name__ == "__main__":
    run()
