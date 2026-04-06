import asyncio
import time


MAX_REPLY_CACHE_SECONDS = 300
MAX_MESSAGE_LENGTH = 430

EN_LAYOUT = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./"
RU_LAYOUT = "ёйцукенгшщзхъфывапролджэячсмитьбю."
SHIFT_EN_LAYOUT = '~QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>?'
SHIFT_RU_LAYOUT = 'ËЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,'

EN_TO_RU = str.maketrans(EN_LAYOUT + SHIFT_EN_LAYOUT, RU_LAYOUT + SHIFT_RU_LAYOUT)
RU_TO_EN = str.maketrans(RU_LAYOUT + SHIFT_RU_LAYOUT, EN_LAYOUT + SHIFT_EN_LAYOUT)

_processed_reply_ids: dict[str, float] = {}
_processed_reply_lock = asyncio.Lock()


def _unescape_twitch_tag(value: str) -> str:
    if not value:
        return ""

    return (
        value.replace(r"\s", " ")
        .replace(r"\:", ";")
        .replace(r"\r", "\r")
        .replace(r"\n", "\n")
        .replace(r"\\", "\\")
    )


def _translate_en_to_ru(text: str) -> str:
    return text.translate(EN_TO_RU)


def _translate_ru_to_en(text: str) -> str:
    return text.translate(RU_TO_EN)


def _count_latin(text: str) -> int:
    return sum("a" <= char.casefold() <= "z" for char in text)


def _count_cyrillic(text: str) -> int:
    return sum("\u0430" <= char.casefold() <= "\u044f" or char.casefold() == "ё" for char in text)


def _score_natural_text(text: str) -> float:
    latin = _count_latin(text)
    cyrillic = _count_cyrillic(text)
    letters = latin + cyrillic
    if not letters:
        return 0.0

    vowels = set("aeiouyауоыиэяюёе")
    vowel_count = sum(char.casefold() in vowels for char in text)
    spaces = text.count(" ")
    return letters + vowel_count * 0.3 + spaces * 0.15


def _vowel_ratio(text: str) -> float:
    vowels = set("aeiouyауоыиэяюёе")
    letters = [char.casefold() for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(char in vowels for char in letters) / len(letters)


def translate_keyboard_layout(text: str) -> str:
    latin = _count_latin(text)
    cyrillic = _count_cyrillic(text)
    ratio = _vowel_ratio(text)

    if latin >= 4 and cyrillic == 0 and ratio >= 0.28:
        return text
    if cyrillic >= 4 and latin == 0 and ratio >= 0.28:
        return text

    variants = [
        text,
        _translate_en_to_ru(text),
        _translate_ru_to_en(text),
    ]
    variants.sort(key=_score_natural_text, reverse=True)
    return variants[0]


def _truncate_response(author_name: str, text: str) -> str:
    prefix = f"Написано: {author_name}: "
    available = MAX_MESSAGE_LENGTH - len(prefix)
    if available <= 0:
        return prefix.rstrip()

    if len(text) <= available:
        return prefix + text

    return prefix + text[: available - 3].rstrip() + "..."


def extract_reply_message_data(tags: dict) -> tuple[str | None, str | None, str | None]:
    reply_id = tags.get("reply-parent-msg-id")
    reply_author = (
        tags.get("reply-parent-display-name")
        or tags.get("reply-parent-user-login")
        or tags.get("reply-parent-user-name")
    )
    reply_body = tags.get("reply-parent-msg-body")

    if not reply_id or not reply_author or reply_body is None:
        return None, None, None

    return reply_id, _unescape_twitch_tag(reply_author), _unescape_twitch_tag(reply_body)


async def claim_reply_translation(reply_id: str) -> bool:
    async with _processed_reply_lock:
        now = time.time()
        expired = [item_id for item_id, seen_at in _processed_reply_ids.items() if now - seen_at > MAX_REPLY_CACHE_SECONDS]
        for item_id in expired:
            del _processed_reply_ids[item_id]

        if reply_id in _processed_reply_ids:
            return False

        _processed_reply_ids[reply_id] = now
        return True


def build_reply_translation_response(reply_author: str, reply_body: str) -> str:
    translated = translate_keyboard_layout(reply_body.strip())
    return _truncate_response(reply_author, translated)
