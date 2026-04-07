from sqlalchemy import inspect, text

from database.db import engine, Base
import database.models  # noqa: F401


def _ensure_streamer_interested_column():
    inspector = inspect(engine)
    if "recommended_games" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("recommended_games")}
    if "streamer_interested" in columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE recommended_games ADD COLUMN streamer_interested BOOLEAN NOT NULL DEFAULT 0")
        )


def main():
    Base.metadata.create_all(bind=engine)
    _ensure_streamer_interested_column()
    print("Recommendation tables ensured")


if __name__ == "__main__":
    main()
