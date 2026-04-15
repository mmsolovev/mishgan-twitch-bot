import os
from dotenv import load_dotenv


load_dotenv()

TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")
TWITCH_ACCESS_TOKEN = TWITCH_TOKEN.removeprefix("oauth:") if TWITCH_TOKEN else None
TWITCH_NICK = os.getenv("TWITCH_NICK")
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
IGDB_CLIENT_ID = os.getenv("IGDB_CLIENT_ID") or CLIENT_ID
IGDB_CLIENT_SECRET = os.getenv("IGDB_CLIENT_SECRET") or CLIENT_SECRET
BOT_ID = os.getenv("BOT_ID")
RAWG_API_KEY = os.getenv("RAWG_API_KEY")
GAMES_SHEET_URL = os.getenv("GAMES_SHEET_URL")
STREAM_RUNTIME_SAMPLE_SECONDS = int(os.getenv("STREAM_RUNTIME_SAMPLE_SECONDS", "60"))

BOT_PREFIX = "!"
SIGN="MrDestructoid"
COOLDOWN = 60
ADMINS = ["mishgan_sol", "tabula", "orfeylefontu"]
ALLOWED_USERS = {"mishgan_sol", "tabula", "orfeylefontu", "eternalchilll", "wraith8", "kampacha", "angrys2l"}
RECOMMENDATIONS_LIMIT = int(os.getenv("RECOMMENDATIONS_LIMIT", "10"))
RECOMMENDATIONS_BANNED_USERS = {
    user.strip().casefold()
    for user in os.getenv("RECOMMENDATIONS_BANNED_USERS", "").split(",")
    if user.strip()
}
RECOMMENDATIONS_STREAMER_LOGIN = os.getenv("RECOMMENDATIONS_STREAMER_LOGIN", "tabula")
RECOMMENDATIONS_STREAMER_DISPLAY_NAME = os.getenv("RECOMMENDATIONS_STREAMER_DISPLAY_NAME", "Tabula")
RECOMMENDATION_SHEETS_SYNC_DEBOUNCE_SECONDS = int(os.getenv("RECOMMENDATION_SHEETS_SYNC_DEBOUNCE_SECONDS", "15"))
