import os
from dotenv import load_dotenv


load_dotenv()

TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")
TWITCH_NICK = os.getenv("TWITCH_NICK")
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
RAWG_API_KEY = os.getenv("RAWG_API_KEY")

BOT_PREFIX = "!"
SIGN="MrDestructoid"
COOLDOWN = 60
ADMINS = ["mishgan_sol", "tabula", "orfeylefontu"]
ALLOWED_USERS = {"mishgan_sol", "tabula", "orfeylefontu", "wraith8", "kampacha", "angrys2l"}
