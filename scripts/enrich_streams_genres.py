from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Any


"""
Запуск:
python scripts\\enrich_streams_genres.py --limit 50
python scripts\\enrich_streams_genres.py --apply --backup
# пересчитать даже если уже заполнено:
python scripts\\enrich_streams_genres.py --force --apply --backup
# точечно:
python scripts\\enrich_streams_genres.py --only-stream-id 1230 --apply --backup
"""


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _db_path() -> Path:
    return _project_root() / "storage" / "streams.db"


def _normalize_key(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _normalize_genre_token(token: str) -> str:
    if _normalize_key(token) == "role-playing (rpg)":
        return "RPG"
    return token.strip()


def _dedup_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = _normalize_key(v)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _backup_db(db_path: Path) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak-{ts}")
    shutil.copyfile(db_path, backup_path)
    return backup_path


def _compute_stream_genres(
    *,
    title: str | None,
    has_participants: bool,
    game_names: list[str],
    game_genres_texts: list[str | None],
) -> str | None:
    title_raw = title or ""
    title_norm = _normalize_key(title_raw)
    token_list = [t for t in re.findall(r"[\w@]+", title_raw, flags=re.UNICODE) if t]
    title_tokens = {_normalize_key(t) for t in token_list}
    tags: list[str] = []

    # Rule: if the only game is Just Chatting -> "Общение"
    names_norm = [_normalize_key(n) for n in game_names if n]
    if len(names_norm) == 1 and names_norm[0] == "just chatting":
        tags.append("Общение")

    # Rule: streams with participants -> "Кооп"
    if has_participants:
        tags.append("Кооп")

    # Rule: keywords in title
    if {"ирл", "кирл", "irl"} & title_tokens:
        tags.append("IRL")
    if "игрокон" in title_tokens and "с" in title_tokens and "@evikey" in title_tokens:
        tags.append("IRL")
    if "кукинг" in title_tokens or "кукинг" in title_norm:
        tags.append("Кукинг")

    # Genres from games
    game_genres: list[str] = []
    for gt in game_genres_texts:
        for token in _parse_csv(gt):
            token = _normalize_genre_token(token)
            if token:
                game_genres.append(token)
    game_genres = _dedup_keep_order(game_genres)

    # Desired order:
    # "Общение" -> "Кооп" -> "Кукинг" -> "IRL" -> the rest (genres from games)
    fixed_order = ["Общение", "Кооп", "Кукинг", "IRL"]
    fixed = [t for t in fixed_order if any(_normalize_key(x) == _normalize_key(t) for x in tags)]
    rest = [g for g in game_genres if _normalize_key(g) not in {_normalize_key(t) for t in fixed}]

    # Keep deterministic output for easier diffs.
    rest = sorted(rest, key=lambda x: x.casefold())

    result = _dedup_keep_order(fixed + rest)
    return ", ".join(result) if result else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill streams.genres_text from games + rules (only if blank).")
    parser.add_argument("--db", default=str(_db_path()), help="Path to streams.db")
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry-run).")
    parser.add_argument("--backup", action="store_true", help="Create .bak-* copy of DB before applying.")
    parser.add_argument("--limit", type=int, default=0, help="Max streams to process (0 = no limit).")
    parser.add_argument("--only-stream-id", type=int, default=0, help="Process only this stream id.")
    parser.add_argument("--force", action="store_true", help="Recompute even if streams.genres_text is not blank.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        streams = cur.execute(
            "SELECT id, title, genres_text FROM streams ORDER BY id"
        ).fetchall()
        streams = [dict(r) for r in streams]
        if args.only_stream_id:
            streams = [s for s in streams if int(s["id"]) == int(args.only_stream_id)]
        if not args.force:
            streams = [s for s in streams if _is_blank(s.get("genres_text"))]
        if args.limit and args.limit > 0:
            streams = streams[: args.limit]

        if not streams:
            print("Nothing to do: no streams selected.")
            return

        print(f"Streams to process: {len(streams)}")

        if args.apply and args.backup:
            backup_path = _backup_db(db_path)
            print(f"Backup: {backup_path}")

        if args.apply:
            con.execute("BEGIN")

        updated = 0

        for idx, s in enumerate(streams, start=1):
            stream_id = int(s["id"])
            title = s.get("title")

            has_participants = (
                cur.execute(
                    "SELECT 1 FROM stream_participants WHERE stream_id = ? LIMIT 1",
                    (stream_id,),
                ).fetchone()
                is not None
            )

            game_rows = cur.execute(
                """
                SELECT g.id, g.name
                FROM stream_games sg
                JOIN games g ON g.id = sg.game_id
                WHERE sg.stream_id = ?
                ORDER BY sg.position
                """,
                (stream_id,),
            ).fetchall()
            game_rows = [dict(r) for r in game_rows]
            game_ids = [int(r["id"]) for r in game_rows]
            game_names = [str(r.get("name") or "") for r in game_rows]

            game_genres_texts: list[str | None] = []
            if game_ids:
                placeholders = ",".join("?" for _ in game_ids)
                meta_rows = cur.execute(
                    f"SELECT game_id, genres_text FROM games_meta WHERE game_id IN ({placeholders})",
                    game_ids,
                ).fetchall()
                by_id = {int(r[0]): r[1] for r in meta_rows}
                game_genres_texts = [by_id.get(gid) for gid in game_ids]

            new_value = _compute_stream_genres(
                title=title,
                has_participants=has_participants,
                game_names=game_names,
                game_genres_texts=game_genres_texts,
            )

            if _normalize_key(new_value or "") == _normalize_key(s.get("genres_text") or ""):
                continue

            updated += 1
            print(f"[{idx}/{len(streams)}] stream_id={stream_id}: {new_value!r}")

            if args.apply:
                cur.execute(
                    "UPDATE streams SET genres_text = ? WHERE id = ?",
                    (new_value, stream_id),
                )

        if args.apply:
            con.commit()
        else:
            con.rollback()

        mode = "APPLIED" if args.apply else "DRY-RUN"
        print(f"{mode}: updated_streams={updated}")
    except Exception:
        if args.apply:
            con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
