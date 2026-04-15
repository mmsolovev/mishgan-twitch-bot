from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def _db_path() -> Path:
    # Keep this script dependency-free (stdlib only), so it can run even when
    # SQLAlchemy isn't installed / venv isn't activated.
    base_dir = Path(__file__).resolve().parent.parent
    return base_dir / "storage" / "streams.db"


def _get_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}  # (cid, name, type, notnull, dflt_value, pk)


def main() -> None:
    db_path = _db_path()
    os.makedirs(db_path.parent, exist_ok=True)

    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()

        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "games_meta" not in tables:
            print("games_meta table not found; nothing to migrate")
            return

        cols = _get_columns(cur, "games_meta")

        stmts: list[str] = []
        if "platforms_text" not in cols:
            stmts.append("ALTER TABLE games_meta ADD COLUMN platforms_text VARCHAR")
        if "genres_text" not in cols:
            stmts.append("ALTER TABLE games_meta ADD COLUMN genres_text VARCHAR")

        for stmt in stmts:
            cur.execute(stmt)

        con.commit()

        if stmts:
            print("games_meta columns added:", ", ".join(
                s.split(" ADD COLUMN ", 1)[1].split()[0] for s in stmts
            ))
        else:
            print("games_meta columns already present")
    finally:
        con.close()


if __name__ == "__main__":
    main()

