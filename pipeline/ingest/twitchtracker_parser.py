from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup


@dataclass(frozen=True, slots=True)
class TwitchTrackerStreamRow:
    date: datetime
    duration_hours: float
    avg_viewers: int
    max_viewers: int
    followers: int
    views: int
    title: str
    games: list[str]


@dataclass(frozen=True, slots=True)
class TwitchTrackerGameRow:
    name: str
    rank: int
    hours_streamed: float
    avg_viewers: int
    max_viewers: int
    followers_per_hour: float
    last_stream: datetime


def parse_stream_date(value: str) -> datetime:
    return datetime.strptime(value, "%d/%b/%Y %H:%M")


def parse_game_last_stream(value: str) -> datetime:
    return datetime.strptime(value, "%d/%b/%Y")


def clean_int(value: Any) -> int:
    return int(str(value).replace(",", "").strip())


def clean_float(value: Any) -> float:
    return float(str(value).replace(",", "").strip())


def clean_duration_hours(value: Any) -> float:
    s = str(value).strip().replace(" ", "")
    if s.lower().endswith("hrs"):
        s = s[: -3]
    return float(s)


def _unique_in_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _stream_date_cell_text(td: Any) -> str:
    span = td.find("span")
    if span:
        return span.get_text(strip=True)
    return td.get_text(strip=True)


def _stream_metric_cell_int(td: Any) -> int:
    span = td.find("span")
    raw = span.get_text(strip=True) if span else td.get_text(strip=True)
    return clean_int(raw)


def _stream_duration_cell_text(td: Any) -> str:
    span = td.find("span")
    raw = span.get_text(strip=True) if span else td.get_text(strip=True)
    return raw.replace(" ", "").replace(" hrs", "hrs")


def _parse_stream_row_cells(cols: list[Any]) -> TwitchTrackerStreamRow | None:
    if len(cols) < 8:
        return None

    date = parse_stream_date(_stream_date_cell_text(cols[0]))
    games: list[str] = []
    for img in cols[7].find_all("img"):
        game_name = img.get("data-original-title")
        if game_name:
            games.append(str(game_name))
    games = _unique_in_order(games)

    return TwitchTrackerStreamRow(
        date=date,
        duration_hours=clean_duration_hours(_stream_duration_cell_text(cols[1])),
        avg_viewers=_stream_metric_cell_int(cols[2]),
        max_viewers=_stream_metric_cell_int(cols[3]),
        followers=_stream_metric_cell_int(cols[4]),
        views=_stream_metric_cell_int(cols[5]),
        title=cols[6].get_text(strip=True),
        games=games,
    )


def _iter_stream_rows_from_soup(soup: BeautifulSoup) -> list[TwitchTrackerStreamRow]:
    table = soup.find("table", id="streams")
    row_els = table.find_all("tr") if table is not None else soup.find_all("tr")
    streams_by_date: dict[datetime, TwitchTrackerStreamRow] = {}
    for row in row_els:
        cols = row.find_all("td")
        parsed = _parse_stream_row_cells(cols)
        if parsed is not None:
            streams_by_date[parsed.date] = parsed
    return sorted(streams_by_date.values(), key=lambda x: x.date)


def _game_hours_from_td(td: Any) -> float:
    span = td.find("span")
    raw = span.get_text(strip=True) if span else td.get_text(strip=True)
    return clean_float(raw)


def _parse_game_row_cells(cols: list[Any]) -> TwitchTrackerGameRow | None:
    if len(cols) != 7:
        return None

    game_name = cols[1].get_text(strip=True)
    if not game_name:
        return None

    return TwitchTrackerGameRow(
        name=game_name,
        rank=clean_int(cols[0].get_text(strip=True)),
        hours_streamed=_game_hours_from_td(cols[2]),
        avg_viewers=clean_int(cols[3].get_text(strip=True)),
        max_viewers=clean_int(cols[4].get_text(strip=True)),
        followers_per_hour=clean_float(cols[5].get_text(strip=True)),
        last_stream=parse_game_last_stream(cols[6].get_text(strip=True)),
    )


def collect_game_rows_from_games_html_file(path: Path) -> list[TwitchTrackerGameRow]:
    soup = _load_soup(Path(path))
    table = soup.find("table", id="games")
    if table is None:
        return []

    out: list[TwitchTrackerGameRow] = []
    for row in table.find_all("tr"):
        cols = row.find_all("td")
        parsed = _parse_game_row_cells(cols)
        if parsed is not None:
            out.append(parsed)
    return out


def collect_game_rows_from_pages_dir(*, pages_dir: Path) -> list[TwitchTrackerGameRow]:
    pages_dir = Path(pages_dir)
    out: list[TwitchTrackerGameRow] = []
    for file_name, path in iter_html_files(pages_dir):
        if not file_name.startswith("games_page"):
            continue
        out.extend(collect_game_rows_from_games_html_file(path))
    return out


def iter_html_files(pages_dir: Path) -> list[tuple[str, Path]]:
    pages_dir = Path(pages_dir)
    if not pages_dir.exists():
        return []
    return [
        (p.name, p)
        for p in sorted(pages_dir.iterdir(), key=lambda x: x.name)
        if p.is_file() and p.name.endswith(".html")
    ]


def _load_soup(path: Path) -> BeautifulSoup:
    return BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")


def parse_stream_pages(*, pages_dir: Path) -> list[TwitchTrackerStreamRow]:
    """
    Парсит HTML-страницы TwitchTracker и собирает информацию о стримах.

    Файлы без table#streams (например один <tr>) тоже обрабатываются.
    """
    streams_by_date: dict[datetime, TwitchTrackerStreamRow] = {}

    for file_name, path in iter_html_files(pages_dir):
        if file_name.startswith("games_page"):
            continue

        soup = _load_soup(path)
        for row in _iter_stream_rows_from_soup(soup):
            streams_by_date[row.date] = row

    return sorted(streams_by_date.values(), key=lambda x: x.date)


def parse_stream_file(*, path: Path) -> list[TwitchTrackerStreamRow]:
    """
    Parse a single TwitchTracker stream HTML file into normalized rows.

    Поддерживается полная страница с table id="streams" или фрагмент с одной/несколькими строками <tr>.
    Строки с одинаковой датой схлопываются (последняя побеждает).
    """
    soup = _load_soup(Path(path))
    return _iter_stream_rows_from_soup(soup)


def parse_game_pages(*, pages_dir: Path) -> list[TwitchTrackerGameRow]:
    """
    Парсит HTML-страницы с TwitchTracker и собирает информацию об играх.

    Несколько games_page*.html объединяются с правилом merge по имени игры
    (см. pipeline.transform.twitchtracker_transform.merge_twitchtracker_game_rows).
    """
    from pipeline.transform.twitchtracker_transform import merge_twitchtracker_game_rows

    raw = collect_game_rows_from_pages_dir(pages_dir=pages_dir)
    return merge_twitchtracker_game_rows(raw)


def parse_game_file(*, path: Path) -> list[TwitchTrackerGameRow]:
    """
    Parse a single TwitchTracker games HTML file into normalized rows.

    The file is expected to contain a table id="games".
    Дубликаты по имени игры в одном файле объединяются тем же правилом, что и между страницами.
    """
    from pipeline.transform.twitchtracker_transform import merge_twitchtracker_game_rows

    raw = collect_game_rows_from_games_html_file(Path(path))
    return merge_twitchtracker_game_rows(raw)


def load_streams_json(path: Path) -> list[TwitchTrackerStreamRow]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"Expected list in streams.json: {path}")

    out: list[TwitchTrackerStreamRow] = []
    for item in rows:
        if not isinstance(item, dict):
            continue

        out.append(
            TwitchTrackerStreamRow(
                date=parse_stream_date(str(item.get("date") or "")),
                duration_hours=clean_duration_hours(item.get("duration")),
                avg_viewers=clean_int(item.get("avg_viewers")),
                max_viewers=clean_int(item.get("max_viewers")),
                followers=clean_int(item.get("followers")),
                views=clean_int(item.get("views")),
                title=str(item.get("title") or ""),
                games=[str(x) for x in (item.get("games") or []) if str(x).strip()],
            )
        )

    out.sort(key=lambda x: x.date)
    return out


def load_games_json(path: Path) -> list[TwitchTrackerGameRow]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"Expected list in games.json: {path}")

    out: list[TwitchTrackerGameRow] = []
    for item in rows:
        if not isinstance(item, dict):
            continue

        out.append(
            TwitchTrackerGameRow(
                name=str(item.get("game") or ""),
                rank=clean_int(item.get("rank")),
                hours_streamed=clean_float(item.get("hours_streamed")),
                avg_viewers=clean_int(item.get("avg_viewers")),
                max_viewers=clean_int(item.get("max_viewers")),
                followers_per_hour=clean_float(item.get("followers_per_hour")),
                last_stream=parse_game_last_stream(str(item.get("last_stream") or "")),
            )
        )

    out.sort(key=lambda x: (x.rank, x.name.casefold()))
    return out


# ---------------------------------------------------------------------------
# Stream page (single-stream detail page from TwitchTracker)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TitleChange:
    time: str
    offset: str | None
    title: str


@dataclass(frozen=True, slots=True)
class StreamGameEntry:
    name: str
    twitch_game_id: str | None
    avg_viewers: int
    peak_viewers: int
    duration_minutes: int
    followers_gained: int


@dataclass(frozen=True, slots=True)
class StreamPageData:
    date: datetime
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: int
    avg_viewers: int
    peak_viewers: int
    followers_gained: int
    title_changes: list[TitleChange]
    games: list[StreamGameEntry]


def _parse_duration_to_minutes(value: str) -> int:
    """Parse '7h29m' or '2h34m' or '1h' or '45m' to minutes."""
    s = value.replace(" ", "").replace("<small>", "").replace("</small>", "")
    hours = 0
    minutes = 0
    if "h" in s:
        parts = s.split("h")
        hours = int(parts[0]) if parts[0] else 0
        rest = parts[1] if len(parts) > 1 else ""
        rest = rest.replace("m", "")
        minutes = int(rest) if rest else 0
    elif "m" in s:
        minutes = int(s.replace("m", ""))
    return hours * 60 + minutes


def _parse_k_number(value: str) -> int:
    """Parse '2.8K' → 2800, '6,450' → 6450, '1,266' → 1266."""
    s = value.strip().replace(",", "")
    if s.upper().endswith("K"):
        return int(float(s[:-1]) * 1000)
    return int(float(s))


def _parse_stream_timestamp(value: str) -> datetime | None:
    """Parse 'Thu, Aug 13, 12:45' → datetime (year defaults to today)."""
    try:
        parts = value.split(", ", 1)
        date_str = parts[1] if len(parts) > 1 else value
        # Format without year: "Aug 13, 12:45"
        dt = datetime.strptime(date_str, "%b %d, %H:%M")
        # Assume current year since TwitchTracker omits it
        from datetime import date as _date
        return dt.replace(year=_date.today().year)
    except (ValueError, IndexError):
        return None


def _parse_stream_page_date(value: str) -> datetime | None:
    """Parse 'August 13, 2026' → datetime."""
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y")
    except ValueError:
        return None


def _parse_stream_page_summary(soup: BeautifulSoup) -> dict[str, Any]:
    """Extract stream summary metrics from the page."""
    result: dict[str, Any] = {}

    # Date from h3 headline
    date_el = soup.find("span", attrs={"data-date-format": True})
    if date_el:
        result["date"] = _parse_stream_page_date(date_el.get_text(strip=True))

    # Summary metrics — only blocks NOT inside a <section>
    blocks = soup.find_all("div", class_="g-x-s-block")
    for block in blocks:
        # Skip blocks inside sections (those belong to per-game tables)
        if block.find_parent("section"):
            continue

        label_el = block.find("div", class_="g-x-s-label")
        value_el = block.find("div", class_="g-x-s-value")
        if not label_el or not value_el:
            continue
        label = label_el.get_text(strip=True).lower()
        raw_value = value_el.get_text(strip=True)

        if "stream duration" in label:
            result["duration_minutes"] = _parse_duration_to_minutes(raw_value)
        elif "avg viewers" in label:
            result["avg_viewers"] = _parse_k_number(raw_value)
        elif "peak viewers" in label:
            result["peak_viewers"] = _parse_k_number(raw_value)
        elif "followers gained" in label:
            result["followers_gained"] = _parse_k_number(raw_value)

    # Timestamps (started/ended)
    timestamps_div = soup.find("div", class_="stream-timestamps")
    if timestamps_div:
        dt_els = timestamps_div.find_all("div", class_="stream-timestamp-dt")
        if len(dt_els) >= 1:
            result["started_at"] = _parse_stream_timestamp(dt_els[0].get_text(strip=True))
        if len(dt_els) >= 2:
            result["ended_at"] = _parse_stream_timestamp(dt_els[1].get_text(strip=True))

    return result


def _parse_title_changes(soup: BeautifulSoup) -> list[TitleChange]:
    """Extract title change history.

    Supports two HTML layouts:
    1. section#stream-titles  — full history with timestamps (div.line > span×3)
    2. section#stream-single-title — single current title in <p>
    """
    # --- layout 1: full history ---
    section = soup.find("section", id="stream-titles")
    if section:
        changes: list[TitleChange] = []
        for line_div in section.find_all("div", class_="line"):
            spans = line_div.find_all("span")
            if len(spans) >= 3:
                time_str = spans[0].get_text(strip=True)
                offset_raw = spans[1].get_text(strip=True)
                title = spans[2].get_text(strip=True)
                offset = offset_raw if offset_raw and offset_raw != "Start" else None
                changes.append(TitleChange(time=time_str, offset=offset, title=title))
        if changes:
            return changes

    # --- layout 2: single current title ---
    single = soup.find("section", id="stream-single-title")
    if single:
        p = single.find("p")
        if p:
            title = p.get_text(strip=True)
            if title:
                return [TitleChange(time="0:00", offset=None, title=title)]

    return []


def _parse_stream_games(soup: BeautifulSoup) -> list[StreamGameEntry]:
    """Extract per-game metrics from section#stream-games."""
    section = soup.find("section", id="stream-games")
    if not section:
        return []

    games: list[StreamGameEntry] = []
    for wrapper in section.find_all("div", class_="g-x-wrapper"):
        # Game name and twitch ID from link
        title_link = wrapper.find("a", class_="g-x-s-title")
        if not title_link:
            continue
        game_name = title_link.get_text(strip=True)
        href = title_link.get("href", "")
        twitch_game_id = None
        if "/tabula/games/" in href:
            twitch_game_id = href.split("/tabula/games/")[-1].strip("/")

        # Metrics
        metrics: dict[str, str] = {}
        for block in wrapper.find_all("div", class_="g-x-s-block"):
            label_el = block.find("div", class_="g-x-s-label")
            value_el = block.find("div", class_="g-x-s-value")
            if label_el and value_el:
                metrics[label_el.get_text(strip=True).lower()] = value_el.get_text(strip=True)

        games.append(StreamGameEntry(
            name=game_name,
            twitch_game_id=twitch_game_id,
            avg_viewers=_parse_k_number(metrics.get("avg viewers", "0")),
            peak_viewers=_parse_k_number(metrics.get("peak viewers", "0")),
            duration_minutes=_parse_duration_to_minutes(metrics.get("duration", "0m")),
            followers_gained=_parse_k_number(metrics.get("followers gained", "0")),
        ))

    return games


def parse_stream_page_html(path: Path) -> StreamPageData | None:
    """Parse a single TwitchTracker stream detail page into StreamPageData."""
    soup = _load_soup(path)
    summary = _parse_stream_page_summary(soup)

    date = summary.get("date")
    started_at = summary.get("started_at")
    if not date and not started_at:
        return None

    return StreamPageData(
        date=date or started_at.replace(hour=0, minute=0, second=0, microsecond=0),
        started_at=started_at or date,
        ended_at=summary.get("ended_at"),
        duration_minutes=summary.get("duration_minutes", 0),
        avg_viewers=summary.get("avg_viewers", 0),
        peak_viewers=summary.get("peak_viewers", 0),
        followers_gained=summary.get("followers_gained", 0),
        title_changes=_parse_title_changes(soup),
        games=_parse_stream_games(soup),
    )
