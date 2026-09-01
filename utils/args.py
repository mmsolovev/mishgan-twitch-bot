"""Helpers for sanitizing user-provided command arguments.

Twitch/chat transports can attach invisible (non-printable) characters to messages,
e.g. zero-width space (U+200B), zero-width joiner (U+200D) or BOM (U+FEFF). These
turn into spurious tokens after `str.split()` and make commands fall into wrong
branches (search/not-found) instead of their intended no-argument behavior.
"""


def clean_text(value: str | None) -> str:
    """Strip non-printable chars and surrounding whitespace from a single argument.

    Returns an empty string if nothing meaningful remains (e.g. only invisible
    characters were provided).
    """
    if not value:
        return ""
    cleaned = "".join(c for c in value if c.isprintable()).strip()
    if not any(c.isalnum() for c in cleaned):
        return ""
    return cleaned


def split_command_args(content: str | None) -> list[str]:
    """Split raw message content into sanitized command argument tokens.

    The first token (the command name, e.g. ``!праздник``) is skipped. Tokens
    that consist only of invisible/non-printable characters are dropped, so
    phantom trailing arguments do not trigger unintended command branches.
    """
    if not content:
        return []
    cleaned = [
        "".join(c for c in token if c.isprintable()).strip()
        for token in content.split()
    ]
    cleaned = [token for token in cleaned if token]
    return cleaned[1:]
