from pipeline.delivery.sheets_bot_info import sync_bot_info
from pipeline.delivery.sheets_games import sync_games_safe
from pipeline.delivery.sheets_recommendations import sync_recommendations_safe
from pipeline.delivery.sheets_releases import sync_releases_safe
from pipeline.delivery.sheets_streams import sync_streams_safe


async def run_all_sheets_sync():
    """
    Orchestrates the synchronization of various data entities from the database
    to Google Sheets.
    """
    print("Starting Google Sheets synchronization...")

    await sync_streams_safe()
    await sync_games_safe()
    await sync_releases_safe()
    await sync_recommendations_safe()

    print("Google Sheets synchronization completed.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_all_sheets_sync())
