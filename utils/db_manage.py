from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sqlite3
import time


"""
Manage rows in the local SQLite DB:

  # Clear a specific cell (set to NULL)
  python utils/db_manage.py --entity game --id 123 --column genres_text
  python utils/db_manage.py --entity game --id 123 --column genres_text --apply --backup
  python utils/db_manage.py --entity stream --id 1230 --column genres_text --apply --backup
  python utils/db_manage.py --entity recommendation --id 42 --column description_short --apply
  python utils/db_manage.py --entity recommendation --title "Some Game" --column description_short --apply

  # Set a cell to a specific value
  python utils/db_manage.py --entity recommendation --id 42 --column description_short --set-value "Action RPG" --apply
  python utils/db_manage.py --entity recommendation --title "Some Game" --column description_short --set-value "Short desc" --apply

  # Delete an entire row (single table)
  python utils/db_manage.py --entity stream --id 1266 --delete --apply --backup
  python utils/db_manage.py --entity game --id 123 --delete --apply
  python utils/db_manage.py --entity recommendation --title "Some Game" --delete --apply

  # Delete from stream_games by stream_id
  python utils/db_manage.py --entity stream_game --id 1266 --delete --apply --backup

  # Cascade-delete a game + all related rows
  python utils/db_manage.py --entity game --id 5 --delete --cascade
  python utils/db_manage.py --entity game --id 5 --delete --cascade --apply
  python utils/db_manage.py --entity game --id 5 --delete --cascade --apply --backup
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
    return {r[1] for r in rows}


def _table_columns_ordered(cur: sqlite3.Cursor, table: str) -> list[str]:
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set a single DB cell to NULL or delete a row, for manual debugging."
    )
    parser.add_argument("--db", default=str(_db_path()), help="Path to streams.db")
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry-run).")
    parser.add_argument("--backup", action="store_true", help="Create .bak-* copy of DB before applying.")

    parser.add_argument("--entity", choices=["game", "stream", "recommendation", "stream_game"], required=True)
    parser.add_argument("--column", help="Column name to clear (set to NULL) or set with --set-value; not needed with --delete")
    parser.add_argument("--set-value", type=str, help="Set the column to this value instead of NULL (use with --column)")
    parser.add_argument("--delete", action="store_true", help="Delete the entire row instead of clearing a cell")
    parser.add_argument("--cascade", action="store_true", help="Also delete/clean related rows in other tables (only for --entity game --delete)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", type=int, help="ID of the entity (game_id, stream id, or recommendation id)")
    group.add_argument("--title", type=str, help="Title of the recommendation (only for --entity recommendation)")

    args = parser.parse_args()

    if args.title and args.entity != "recommendation":
        raise SystemExit("--title can only be used with --entity recommendation")

    if not args.delete and not args.column:
        raise SystemExit("Provide --column to clear a cell or --set-value to set a value, or --delete to delete the row")

    if args.delete and args.column:
        raise SystemExit("Use either --column (clear cell or --set-value to set) or --delete (delete row), not both")

    if args.set_value and not args.column:
        raise SystemExit("--set-value requires --column")

    if args.cascade:
        if args.entity != "game" or not args.delete:
            raise SystemExit("--cascade can only be used with --entity game --delete")

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    if args.entity == "game":
        if args.delete and not args.cascade:
            table = "games"
            id_col = "id"
        else:
            table = "games_meta"
            id_col = "game_id"
        id_val = args.id
    elif args.entity == "stream":
        table = "streams"
        id_col = "id"
        id_val = args.id
    elif args.entity == "stream_game":
        if not args.id:
            raise SystemExit("--id is required for --entity stream_game (use stream_id)")
        table = "stream_games"
        id_col = "stream_id"
        id_val = args.id
    else:  # recommendation
        table = "recommended_games"
        if args.id:
            id_col = "id"
            id_val = args.id
        else:
            id_col = "title"
            id_val = args.title

    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()

        if args.delete:
            if args.entity == "game" and args.cascade:
                # --- CASCADE DELETE for game ---
                game_id = args.id

                # 1. Verify game exists
                game_row = cur.execute("SELECT id, name FROM games WHERE id = ?", (game_id,)).fetchone()
                if game_row is None:
                    raise SystemExit(f"Game not found: games.id={game_id}")
                print(f"Game: id={game_row[0]}, name={game_row[1]!r}")

                # 2. Preview related rows
                stream_games = cur.execute("SELECT stream_id, position FROM stream_games WHERE game_id = ?", (game_id,)).fetchall()
                stats_rows = cur.execute("SELECT * FROM games_stats WHERE game_id = ?", (game_id,)).fetchall()
                meta_row = cur.execute("SELECT * FROM games_meta WHERE game_id = ?", (game_id,)).fetchall()
                rec_rows = cur.execute("SELECT id, title FROM recommended_games WHERE matched_game_id = ?", (game_id,)).fetchall()

                if stream_games:
                    print(f"  stream_games x{len(stream_games)}: {stream_games}")
                else:
                    print("  stream_games: (none)")
                if stats_rows:
                    stats_cols = [c[1] for c in cur.execute("PRAGMA table_info(games_stats)").fetchall()]
                    for r in stats_rows:
                        print(f"  games_stats: {dict(zip(stats_cols, r))}")
                else:
                    print("  games_stats: (none)")
                if meta_row:
                    meta_cols = [c[1] for c in cur.execute("PRAGMA table_info(games_meta)").fetchall()]
                    print(f"  games_meta: {dict(zip(meta_cols, meta_row[0]))}")
                else:
                    print("  games_meta: (none)")
                if rec_rows:
                    print(f"  recommended_games (will set matched_game_id=NULL): x{len(rec_rows)}")
                    for r in rec_rows:
                        print(f"    id={r[0]} title={r[1]!r}")
                else:
                    print("  recommended_games: (none)")

                if not args.apply:
                    print("DRY-RUN (no changes written). Use --apply to write.")
                    return

                if args.backup:
                    backup_path = _backup_db(db_path)
                    print(f"Backup: {backup_path}")

                con.execute("BEGIN")
                cur.execute("DELETE FROM stream_games WHERE game_id = ?", (game_id,))
                cur.execute("DELETE FROM games_stats WHERE game_id = ?", (game_id,))
                cur.execute("DELETE FROM games_meta WHERE game_id = ?", (game_id,))
                cur.execute("UPDATE recommended_games SET matched_game_id = NULL WHERE matched_game_id = ?", (game_id,))
                cur.execute("DELETE FROM games WHERE id = ?", (game_id,))
                con.commit()
                print("APPLIED (game cascade-deleted).")

            else:
                # --- SINGLE-TABLE DELETE path ---
                cols_list = _table_columns_ordered(cur, table)
                row = cur.execute(
                    f"SELECT * FROM {table} WHERE {id_col} = ?",
                    (id_val,),
                ).fetchone()

                if row is None:
                    raise SystemExit(f"Row not found: {table}.{id_col}={id_val}")

                print(f"Deleting row from {table} WHERE {id_col}={id_val}:")
                for name, value in zip(cols_list, row):
                    print(f"  {name}: {value!r}")

                if args.entity == "game":
                    print("WARNING: related rows in stream_games, games_stats, games_meta, "
                          "recommended_games will become orphans. Use --cascade to clean them too.")

                if not args.apply:
                    print("DRY-RUN (no changes written). Use --apply to write.")
                    return

                if args.backup:
                    backup_path = _backup_db(db_path)
                    print(f"Backup: {backup_path}")

                con.execute("BEGIN")
                cur.execute(f"DELETE FROM {table} WHERE {id_col} = ?", (id_val,))
                con.commit()
                print("APPLIED (row deleted).")

        else:
            # --- CLEAR-CELL / SET-CELL path ---
            cols = _table_columns(cur, table)
            if args.column not in cols:
                raise SystemExit(
                    f"Unknown column for {table}: {args.column}. Available: {sorted(cols)}"
                )

            query = f"SELECT {args.column} FROM {table} WHERE {id_col} = ?"
            params = (id_val,)

            before = cur.execute(query, params).fetchone()
            if before is None:
                raise SystemExit(f"Row not found: {table}.{id_col}={id_val}")

            new_value = args.set_value if args.set_value else None
            print(f"Before: {table}.{id_col}={id_val} {args.column}={before[0]!r}")
            print(f"After:  {table}.{id_col}={id_val} {args.column}={new_value!r}")

            if not args.apply:
                print("DRY-RUN (no changes written). Use --apply to write.")
                return

            if args.backup:
                backup_path = _backup_db(db_path)
                print(f"Backup: {backup_path}")

            con.execute("BEGIN")
            cur.execute(
                f"UPDATE {table} SET {args.column} = ? WHERE {id_col} = ?",
                (new_value, id_val),
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
