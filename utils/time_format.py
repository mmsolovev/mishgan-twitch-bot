def _render_hours_minutes(hours: int, minutes: int) -> str:
    if minutes == 0:
        return f"{hours} ч"
    return f"{hours} ч {minutes} м"


def format_hours_minutes(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        total_minutes = round(float(value) * 60)
    except (TypeError, ValueError):
        return None
    if total_minutes < 0:
        total_minutes = 0
    return _render_hours_minutes(total_minutes // 60, total_minutes % 60)


def format_duration_compact(seconds: float | int) -> str:
    if seconds < 0:
        seconds = 0
    total_minutes = int(seconds // 60)
    return _render_hours_minutes(total_minutes // 60, total_minutes % 60)