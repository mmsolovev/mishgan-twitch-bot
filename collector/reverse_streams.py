import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
STREAMS_FILE = os.path.join(BASE_DIR, "storage", "streams.json")


with open(STREAMS_FILE, encoding="utf-8") as f:
    streams = json.load(f)

# разворачиваем список
streams.reverse()

with open(STREAMS_FILE, "w", encoding="utf-8") as f:
    json.dump(streams, f, ensure_ascii=False, indent=2)

print("streams.json reversed")
