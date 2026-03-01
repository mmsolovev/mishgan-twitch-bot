import json
import os


CACHE_DIR = "storage/cache"


def load_cache(name: str) -> dict:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = f"{CACHE_DIR}/{name}.json"

    if not os.path.exists(path):
        return {}

    if os.path.getsize(path) == 0:
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_cache(name: str, data: dict):
    path = f"{CACHE_DIR}/{name}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
