import json
import random
from datetime import datetime


with open("storage/holidays.json", "r", encoding="utf-8") as f:
    HOLIDAYS = json.load(f)


def get_today_key() -> str:
    return datetime.now().strftime("%d-%m")


def get_holidays_by_date(date: str):
    return HOLIDAYS.get(date, [])


def get_random_today():
    today = get_today_key()
    holidays = get_holidays_by_date(today)

    if not holidays:
        return None

    return random.choice(holidays)


def get_all_today_names():
    today = get_today_key()
    holidays = get_holidays_by_date(today)

    return [h["name"] for h in holidays]


def get_by_index(date: str, index: int):
    holidays = get_holidays_by_date(date)

    if 0 <= index < len(holidays):
        return holidays[index]

    return None


def search_holiday(query: str):
    results = []

    for date, holidays in HOLIDAYS.items():
        for h in holidays:
            if query.lower() in h["name"].lower():
                results.append({
                    "date": date,
                    "name": h["name"],
                    "desc": h["desc"]
                })

    return results[:5]  # ограничим
