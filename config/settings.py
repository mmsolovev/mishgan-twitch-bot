import os
from dotenv import load_dotenv


load_dotenv()

TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")
TWITCH_NICK = os.getenv("TWITCH_NICK")
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

BOT_PREFIX = "!"
SIGN="MrDestructoid"
COOLDOWN = 60
ALLOWED_USERS = ["mishgan_sol", "tabula", "orfeylefontu"]
