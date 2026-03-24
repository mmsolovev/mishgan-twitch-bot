from difflib import get_close_matches
import json
from functools import lru_cache
from pathlib import Path


STORAGE_DIR = Path("storage")
INFO_PATH = STORAGE_DIR / "info.json"
INFO_EXAMPLE_PATH = STORAGE_DIR / "info.example.json"
ALIASES_PATH = STORAGE_DIR / "info_aliases.json"


@lru_cache
def load_info():
    path = INFO_PATH if INFO_PATH.exists() else INFO_EXAMPLE_PATH

    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def load_aliases():
    if not ALIASES_PATH.exists():
        return {}

    with ALIASES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def collect_keys(data):
    keys = []

    if isinstance(data, dict):
        for k, v in data.items():
            keys.append(k.lower())
            keys.extend(collect_keys(v))

    return keys


def find_value(data, query):
    if isinstance(data, dict):
        for k, v in data.items():
            if k.lower() == query:
                return k, v

            result = find_value(v, query)
            if result:
                return result

    return None


def fuzzy(query, keys):
    match = get_close_matches(query, keys, n=1, cutoff=0.5)
    return match[0] if match else None


def resolve_query(query: str):
    data = load_info()
    aliases = load_aliases()
    query = query.lower()
    query = aliases.get(query, query)

    keys = collect_keys(data)

    # 1. точный
    result = find_value(data, query)

    # 2. fuzzy
    if not result:
        match = fuzzy(query, keys)
        if match:
            result = find_value(data, match)

    return result

def format_for_chat(key, value):
    # 🔹 если есть summary — ВСЕГДА используем его
    if isinstance(value, dict) and "summary" in value:
        return value["summary"]

    # 🔹 если это конкретный девайс
    if isinstance(value, str):
        return f"{key}: {value}"

    # 🔹 fallback (на всякий случай)
    return "Нет информации 🤔"
