import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parent.parent / "storage" / "logs"
LOG_FILE = LOG_DIR / "bot.log"
_LOGGING_CONFIGURED = False


def configure_logging() -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    _LOGGING_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def log_command(user: str, command: str, text: str = ""):
    timestamp = datetime.now().strftime("%H:%M:%S")
    message = f"[{timestamp}] {user}: !{command} {text}".rstrip()
    get_logger("chat.command").info(message)
