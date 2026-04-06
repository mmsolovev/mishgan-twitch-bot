import json
import re
from functools import lru_cache
from pathlib import Path


BANNED_START = ("!", "/", ".")
BANNED_WORDS_PATH = Path("storage/censorship/banned_words.json")
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9@$#]+", re.UNICODE)
SUBSTITUTION_MAP = {
    "@": "a",
    "$": "s",
    "0": "o",
    "1": "i",
    "3": "e",
    "4": "a",
    "5": "s",
    "6": "g",
    "7": "t",
    "8": "b",
    "9": "g",
}
HOMOGLYPH_MAP = {
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "у": "y",
    "х": "x",
    "ё": "е",
    "a": "a",
    "c": "c",
    "e": "e",
    "o": "o",
    "p": "p",
    "x": "x",
    "y": "y",
}


def _normalize_char(char: str) -> str:
    lowered = char.casefold()
    lowered = SUBSTITUTION_MAP.get(lowered, lowered)
    return HOMOGLYPH_MAP.get(lowered, lowered)


def _normalize_token(token: str) -> str:
    normalized = []
    for char in token:
        if not char.isalnum() and char not in SUBSTITUTION_MAP and char not in {"@", "$", "#"}:
            continue
        normalized_char = _normalize_char(char)
        if normalized_char.isalnum() or "\u0400" <= normalized_char <= "\u04ff":
            normalized.append(normalized_char)
    return "".join(normalized)


def _is_within_one_edit(left: str, right: str) -> bool:
    if left == right:
        return True

    left_len = len(left)
    right_len = len(right)
    if abs(left_len - right_len) > 1:
        return False

    if left_len > right_len:
        left, right = right, left
        left_len, right_len = right_len, left_len

    index_left = 0
    index_right = 0
    edits = 0

    while index_left < left_len and index_right < right_len:
        if left[index_left] == right[index_right]:
            index_left += 1
            index_right += 1
            continue

        edits += 1
        if edits > 1:
            return False

        if left_len == right_len:
            index_left += 1
            index_right += 1
        else:
            index_right += 1

    if index_left < left_len or index_right < right_len:
        edits += 1

    return edits <= 1


@lru_cache(maxsize=1)
def load_banned_words() -> tuple[str, ...]:
    if not BANNED_WORDS_PATH.exists():
        return tuple()

    try:
        raw_words = json.loads(BANNED_WORDS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return tuple()

    if not isinstance(raw_words, list):
        return tuple()

    cleaned_words = []
    for word in raw_words:
        if not isinstance(word, str):
            continue

        normalized = _normalize_token(word.strip())
        if normalized:
            cleaned_words.append(normalized)

    return tuple(sorted(set(cleaned_words)))


def censor_text(text: str) -> str:
    if not text:
        return text

    banned_words = load_banned_words()
    if not banned_words:
        return text

    chars = list(text)
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        normalized_token = _normalize_token(token)
        if not normalized_token:
            continue

        should_censor = any(_is_within_one_edit(normalized_token, banned_word) for banned_word in banned_words)
        if not should_censor:
            continue

        for index in range(match.start(), match.end()):
            if not chars[index].isspace():
                chars[index] = "*"

    return "".join(chars)


def sanitize_start(text: str) -> str:
    while text and text[0] in BANNED_START:
        text = text[1:]
    return text.strip()


def sanitize_outgoing_message(
    raw_text: str,
    *,
    max_length: int = 450,
    empty_fallback: str = "****",
) -> str:
    text = (raw_text or "").strip().replace("\n", " ")
    text = censor_text(text)
    text = sanitize_start(text)

    if len(text) > max_length:
        text = text[: max_length - 3].rstrip() + "..."

    return text or empty_fallback


def process_gpt_answer(raw_answer: str) -> str:
    if not raw_answer:
        return "MrDestructoid GPT временно недоступен"

    answer = sanitize_outgoing_message(
        raw_answer,
        max_length=150,
        empty_fallback="MrDestructoid GPT ответил пусто после фильтров",
    )
    return answer
