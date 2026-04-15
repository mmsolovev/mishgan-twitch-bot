from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sqlite3
import time


"""
Ставит конкретное поле в NULL по game_id (в games_meta) или stream id (в streams).
python scripts\\db_clear_cell.py --entity game --id 123 --column genres_text
python scripts\\db_clear_cell.py --entity game --id 123 --column genres_text --apply --backup

python scripts\\db_clear_cell.py --entity stream --id 1230 --column genres_text --apply --backup
"""


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _db_path() -> Path:
    return _project_root() / "storage" / "streams.db"


def _backup_db(db_path: Path) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak-{ts}")
    shutil.copyfile(db_path, backup_path)
    return backup_path


def _table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}  # (cid, name, type, notnull, dflt, pk)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set a single DB cell to NULL for manual debugging.")
    parser.add_argument("--db", default=str(_db_path()), help="Path to streams.db")
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry-run).")
    parser.add_argument("--backup", action="store_true", help="Create .bak-* copy of DB before applying.")

    parser.add_argument("--entity", choices=["game", "stream"], required=True)
    parser.add_argument("--id", type=int, required=True, help="game_id (for games_meta) or stream id (for streams)")
    parser.add_argument("--column", required=True, help="Column name to clear (set to NULL)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    table = "games_meta" if args.entity == "game" else "streams"
    id_col = "game_id" if args.entity == "game" else "id"

    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cols = _table_columns(cur, table)
        if args.column not in cols:
            raise SystemExit(f"Unknown column for {table}: {args.column}. Available: {sorted(cols)}")

        before = cur.execute(
            f"SELECT {args.column} FROM {table} WHERE {id_col} = ?",
            (int(args.id),),
        ).fetchone()
        if before is None:
            raise SystemExit(f"Row not found: {table}.{id_col}={args.id}")

        print(f"Before: {table}.{id_col}={args.id} {args.column}={before[0]!r}")
        print(f"After:  {table}.{id_col}={args.id} {args.column}=NULL")

        if not args.apply:
            print("DRY-RUN (no changes written). Use --apply to write.")
            return

        if args.backup:
            backup_path = _backup_db(db_path)
            print(f"Backup: {backup_path}")

        con.execute("BEGIN")
        cur.execute(
            f"UPDATE {table} SET {args.column} = NULL WHERE {id_col} = ?",
            (int(args.id),),
        )
        con.commit()
        print("APPLIED.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()

