import asyncio

from config.settings import RECOMMENDATION_SHEETS_SYNC_DEBOUNCE_SECONDS
from services.sheets_sync_service import sync_recommendations_safe, sync_releases_safe
from utils.logger import get_logger


class RecommendationSheetsSyncScheduler:
    def __init__(self, debounce_seconds: int | float = RECOMMENDATION_SHEETS_SYNC_DEBOUNCE_SECONDS):
        self.debounce_seconds = max(float(debounce_seconds), 0.0)
        self.logger = get_logger("sheets.recommendations.scheduler")
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._pending = False

    async def schedule_sync(self, reason: str = "recommendation_changed"):
        self._pending = True

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(reason), name="recommendation-sheets-sync")
            self.logger.info("Scheduled recommendations sheets sync (%s)", reason)

    async def _run(self, initial_reason: str):
        reason = initial_reason

        while True:
            self._pending = False

            if self.debounce_seconds > 0:
                await asyncio.sleep(self.debounce_seconds)

            if self._pending:
                reason = "recommendation_changed_batched"
                continue

            async with self._lock:
                self.logger.info("Starting recommendations sheets sync (%s)", reason)
                try:
                    await asyncio.to_thread(sync_releases_safe)
                    await asyncio.to_thread(sync_recommendations_safe)
                except Exception:
                    self.logger.exception("Recommendations sheets sync failed")
                else:
                    self.logger.info("Recommendations sheets sync finished")

            if not self._pending:
                break

            reason = "recommendation_changed_batched"

        self._task = None

