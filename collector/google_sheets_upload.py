from services.sheets_sync_service import (
    sync_bot_info,
    sync_games_safe,
    sync_recommendations_safe,
    sync_releases_safe,
    sync_streams_safe,
)


def run_all():
    # sync_bot_info()
    sync_streams_safe()
    sync_games_safe()
    sync_releases_safe()
    sync_recommendations_safe()


if __name__ == "__main__":
    run_all()
