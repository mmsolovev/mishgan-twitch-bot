from config.settings import ALLOWED_USERS


def is_allowed(user: str) -> bool:
    return user.lower() in ALLOWED_USERS
