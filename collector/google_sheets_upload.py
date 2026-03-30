from services.sheets_sync_service import sync_streams, sync_games


def run_all():
    sync_streams()
    sync_games()


if __name__ == "__main__":
    run_all()
