import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


STORAGE_DIR = Path("storage") / "movies"
LOST_DATA_PATH = STORAGE_DIR / "lost.json"
LOST_STATE_PATH = STORAGE_DIR / "lost_state.json"

# Фиксированный MSK (UTC+03:00), чтобы время было стабильным независимо от хоста.
MSK_TZ = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class LostEpisodeRef:
    season: int
    episode: int


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now_msk_hhmm() -> str:
    return datetime.now(MSK_TZ).strftime("%H:%M")


def _normalize_hhmm(value: str) -> str | None:
    # Принимаем строго HH:MM (00-23):(00-59).
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) != 5 or value[2] != ":":
        return None
    hh, mm = value.split(":", 1)
    if not (hh.isdigit() and mm.isdigit()):
        return None
    h, m = int(hh), int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        return None
    return f"{h:02d}:{m:02d}"


def load_lost_data() -> dict[str, Any]:
    return _load_json(LOST_DATA_PATH)


def load_lost_state() -> dict[str, Any]:
    return _load_json(LOST_STATE_PATH)


def save_lost_state(state: dict[str, Any]) -> None:
    _save_json(LOST_STATE_PATH, state)


def get_current_ref() -> LostEpisodeRef | None:
    state = load_lost_state()
    season = state.get("season")
    episode = state.get("episode")
    if not isinstance(season, int) or not isinstance(episode, int):
        return None
    if season < 0 or episode < 0:
        return None
    return LostEpisodeRef(season=season, episode=episode)


def get_started_at_msk() -> str | None:
    state = load_lost_state()
    started_at = state.get("started_at_msk")
    if not started_at:
        return None
    return str(started_at)


def get_online_status() -> bool:
    state = load_lost_state()
    return state.get("online", False)


def _find_episode(data: dict[str, Any], ref: LostEpisodeRef) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    series = data.get("series") if isinstance(data, dict) else None
    if not isinstance(series, dict):
        return None

    seasons = series.get("seasons")
    if not isinstance(seasons, list):
        return None

    for season in seasons:
        if not isinstance(season, dict):
            continue
        if season.get("season_number") != ref.season:
            continue
        episodes = season.get("episodes")
        if not isinstance(episodes, list):
            return None
        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            if ep.get("episode_number") == ref.episode:
                return series, season, ep

    return None


def _next_ref(data: dict[str, Any], ref: LostEpisodeRef) -> LostEpisodeRef | None:
    series = data.get("series") if isinstance(data, dict) else None
    if not isinstance(series, dict):
        return None
    seasons = series.get("seasons")
    if not isinstance(seasons, list):
        return None

    # Ищем текущий сезон/эпизод в списке, затем берём следующий.
    for s_idx, season in enumerate(seasons):
        if not isinstance(season, dict):
            continue
        if season.get("season_number") != ref.season:
            continue

        episodes = season.get("episodes")
        if not isinstance(episodes, list):
            return None

        # ищем индекс текущего эпизода
        cur_idx = None
        for e_idx, ep in enumerate(episodes):
            if isinstance(ep, dict) and ep.get("episode_number") == ref.episode:
                cur_idx = e_idx
                break

        if cur_idx is None:
            return None

        # следующий эпизод в этом сезоне
        if cur_idx + 1 < len(episodes):
            next_ep = episodes[cur_idx + 1]
            if isinstance(next_ep, dict) and isinstance(next_ep.get("episode_number"), int):
                return LostEpisodeRef(season=ref.season, episode=int(next_ep["episode_number"]))
            return None

        # иначе первый эпизод следующего сезона
        for ns in seasons[s_idx + 1 :]:
            if not isinstance(ns, dict):
                continue
            neps = ns.get("episodes")
            if not isinstance(neps, list) or not neps:
                continue
            first = neps[0]
            if isinstance(first, dict) and isinstance(ns.get("season_number"), int) and isinstance(first.get("episode_number"), int):
                return LostEpisodeRef(season=int(ns["season_number"]), episode=int(first["episode_number"]))
            return None

        return None

    return None


def format_current_episode_for_chat() -> str | None:
    if not get_online_status():
        return format_next_episode_for_chat()

    ref = get_current_ref()
    if not ref:
        return None

    data = load_lost_data()
    series = data.get("series") if isinstance(data, dict) else None
    if not isinstance(series, dict):
        return None

    series_title_ru = series.get("title_ru")
    series_title_en = series.get("title_en")

    if not series_title_ru or not series_title_en:
        return None

    if ref.season == 0 and ref.episode == 0:
        return f"MrDestructoid Сериал: {series_title_ru} ({series_title_en})"

    found = _find_episode(data, ref)
    if not found:
        return None

    _series, season, ep = found
    season_number = season.get("season_number")
    season_year = season.get("year")
    episode_number = ep.get("episode_number")
    episode_title_ru = ep.get("title_ru")
    episode_title_en = ep.get("title_en")
    imdb_rating = ep.get("imdb_rating")

    if not episode_title_ru or not episode_title_en:
        return None

    msg = (
        f"MrDestructoid Сериал: {series_title_ru} ({series_title_en}) | "
        f"{season_number} сезон ({season_year}) | "
        f"{episode_number} серия {episode_title_ru} ({episode_title_en}) | "
        f"IMDB {imdb_rating}"
    )

    started_at = get_started_at_msk()
    if started_at:
        msg += f" | Начали серию ~{started_at} МСК"

    return msg


def format_next_episode_for_chat() -> str | None:
    ref = get_current_ref()
    if not ref:
        return None

    return f"MrDestructoid Последняя просмотренная серия Lost: {ref.season} сезон, {ref.episode} серия."


def set_current_episode(ref: LostEpisodeRef, *, set_started_time_now: bool = True) -> None:
    state = load_lost_state()
    state["season"] = int(ref.season)
    state["episode"] = int(ref.episode)
    state["online"] = True
    if set_started_time_now:
        state["started_at_msk"] = _now_msk_hhmm()
    save_lost_state(state)


def set_generic_series_state() -> None:
    state = load_lost_state()
    state["season"] = 0
    state["episode"] = 0
    state["online"] = True
    state["started_at_msk"] = None
    save_lost_state(state)


def increment_episode() -> LostEpisodeRef | None:
    data = load_lost_data()
    cur = get_current_ref()

    if not cur or (cur.season == 0 and cur.episode == 0):
        series = data.get("series") if isinstance(data, dict) else None
        seasons = series.get("seasons") if isinstance(series, dict) else None
        if not isinstance(seasons, list) or not seasons:
            return None
        first_season = seasons[0] if isinstance(seasons[0], dict) else None
        if not isinstance(first_season, dict):
            return None
        eps = first_season.get("episodes")
        if not isinstance(eps, list) or not eps:
            return None
        first_ep = eps[0] if isinstance(eps[0], dict) else None
        if not isinstance(first_ep, dict):
            return None
        if not isinstance(first_season.get("season_number"), int) or not isinstance(first_ep.get("episode_number"), int):
            return None
        nxt = LostEpisodeRef(season=int(first_season["season_number"]), episode=int(first_ep["episode_number"]))
        set_current_episode(nxt, set_started_time_now=True)
        return nxt

    nxt = _next_ref(data, cur)
    if not nxt:
        return None
    set_current_episode(nxt, set_started_time_now=True)
    return nxt


def set_started_time(value: str | None) -> bool:
    state = load_lost_state()

    if value is None:
        state["started_at_msk"] = _now_msk_hhmm()
        save_lost_state(state)
        return True

    normalized = _normalize_hhmm(value)
    if not normalized:
        return False

    state["started_at_msk"] = normalized
    save_lost_state(state)
    return True


def set_online_status(online: bool) -> None:
    state = load_lost_state()
    state["online"] = online
    save_lost_state(state)


def clear_all() -> None:
    set_online_status(False)


def clear_time_only() -> None:
    state = load_lost_state()
    state["started_at_msk"] = None
    save_lost_state(state)