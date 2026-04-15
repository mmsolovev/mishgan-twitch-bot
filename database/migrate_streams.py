from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def _db_path() -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    return base_dir / "storage" / "streams.db"


def _get_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def main() -> None:
    db_path = _db_path()
    os.makedirs(db_path.parent, exist_ok=True)

    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()

        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "streams" not in tables:
            print("streams table not found; nothing to migrate")
            return

        cols = _get_columns(cur, "streams")
        if "genres_text" in cols:
            print("streams.genres_text already present")
            return

        cur.execute("ALTER TABLE streams ADD COLUMN genres_text VARCHAR")
        con.commit()
        print("streams column added: genres_text")
    finally:
        con.close()


if __name__ == "__main__":
    main()

