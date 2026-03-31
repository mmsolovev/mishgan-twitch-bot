from services.sheets_sync_service import sync_streams_safe, sync_games_safe


def run_all():
    sync_streams_safe()
    sync_games_safe()


if __name__ == "__main__":
    run_all()
