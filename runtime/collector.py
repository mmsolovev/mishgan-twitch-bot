import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import config.settings as settings
from runtime import sampler, storage
from runtime.metrics import recalculate_all_metrics
from runtime.session import (
    StreamSession,
    create_new_session,
    ensure_session_shape,
)
from runtime.utils import (
    calculate_duration_minutes,
    infer_session_end_time,
    now_iso,
    parse_iso,
)
from utils.logger import get_logger


class RuntimeStreamCollector:
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("runtime.collector")
        self.broadcaster_id: int | None = None
        self.active_session: StreamSession | None = None
        self.sample_interval_seconds = settings.STREAM_RUNTIME_SAMPLE_SECONDS
        self.sampling_task: asyncio.Task | None = None
        self.followers_sampling_enabled = True

        storage.prepare_storage_dir()

    async def bootstrap(self, broadcaster_id: int, token: str):
        self.broadcaster_id = int(broadcaster_id)
        self.active_session = storage.load_active_session()
        ensure_session_shape(self.active_session)

        live_stream = await sampler.fetch_live_stream_with_refresh(self.bot, settings.TWITCH_ACCESS_TOKEN)
        if self.active_session and live_stream and str(self.active_session.get("stream_id")) == str(live_stream.id):
            self.logger.info("Recovered active session for current live stream %s", live_stream.id)
            self._record_event("collector.bootstrap.recovered", {"stream_id": str(live_stream.id)})
            self._save_active_session()
            await self.ensure_sampling_started(initial_stream=live_stream)
            return

        if self.active_session and live_stream and str(self.active_session.get("stream_id")) != str(live_stream.id):
            self.logger.info(
                "Finalizing mismatched active session %s before recovering live stream %s",
                self.active_session.get("stream_id"),
                live_stream.id,
            )
            inferred_end = infer_session_end_time(self.active_session)
            await self.finalize_active_session(
                ended_at=inferred_end,
                reason="recovered_new_live_stream_on_bootstrap",
            )

        if self.active_session and not live_stream:
            self.logger.info("Finalizing stale active session without stream.offline event")
            inferred_end = infer_session_end_time(self.active_session)
            await self.finalize_active_session(
                ended_at=inferred_end,
                reason="recovered_offline_on_bootstrap",
            )

        if live_stream and not self.active_session:
            self.logger.info("Creating active session from current live stream snapshot %s", live_stream.id)
            self.start_session_from_stream(live_stream, source="bootstrap")
            await self.capture_runtime_sample(stream_snapshot=live_stream, reason="bootstrap")
            await self.ensure_sampling_started()

    async def handle_stream_online(self, data, stream_snapshot=None):
        stream_id = str(getattr(stream_snapshot, "id", None) or getattr(data, "id", None))
        if self.active_session and self.active_session.get("stream_id") == stream_id:
            self.logger.info("Ignoring duplicate stream.online for stream %s", stream_id)
            self._record_event("stream.online.duplicate", {"stream_id": stream_id})
            self._save_active_session()
            await self.ensure_sampling_started(initial_stream=stream_snapshot)
            return

        if self.active_session and self.active_session.get("stream_id") != stream_id:
            self.logger.info(
                "Finalizing previous active session %s before starting stream %s",
                self.active_session.get("stream_id"),
                stream_id,
            )
            ended_at = getattr(data, "started_at", None)
            await self.finalize_active_session(
                ended_at=ended_at.isoformat() if ended_at else infer_session_end_time(self.active_session),
                reason="superseded_by_new_stream_online",
            )

        if stream_snapshot is not None:
            self.start_session_from_stream(stream_snapshot, source="eventsub.stream_online")
            await self.capture_runtime_sample(stream_snapshot=stream_snapshot, reason="stream.online")
            await self.ensure_sampling_started()
            return

        self.start_session(
            stream_id=stream_id,
            started_at=data.started_at.isoformat(),
            title=None,
            category_name=None,
            category_id=None,
            source="eventsub.stream_online.partial",
        )
        await self.ensure_sampling_started()

    async def handle_stream_offline(self, ended_at: str | None = None):
        if not self.active_session:
            self.logger.info("Ignoring stream.offline without active session")
            return

        await self.finalize_active_session(
            ended_at=ended_at or now_iso(),
            reason="eventsub.stream_offline",
        )

    def handle_channel_update(self, data):
        if not self.active_session:
            self.logger.info("Ignoring channel.update for collector without active session")
            return

        changed_at = now_iso()
        if not self.active_session.get("title") and data.title:
            self.active_session["title"] = data.title
            self.active_session["title_start"] = data.title

        if self.active_session.get("title_current") != data.title:
            self.active_session["title_current"] = data.title
            self.active_session["title_history"].append(
                {
                    "title": data.title,
                    "changed_at": changed_at,
                    "source": "eventsub.channel_update",
                }
            )

        category_changed = self.active_session.get("category_name") != data.category_name
        if category_changed:
            self.active_session["category_name"] = data.category_name
            self.active_session["category_id"] = data.category_id
            self.active_session["category_history"].append(
                {
                    "category_id": data.category_id,
                    "category_name": data.category_name,
                    "changed_at": changed_at,
                    "source": "eventsub.channel_update",
                }
            )
            self._update_game_segments(
                category_id=data.category_id,
                category_name=data.category_name,
                changed_at=changed_at,
            )

        self._record_event(
            "channel.update",
            {
                "title": data.title,
                "category_id": data.category_id,
                "category_name": data.category_name,
                "changed_at": changed_at,
            },
        )
        self._save_active_session()

    def handle_follow(self, data):
        if not self.active_session:
            self.logger.info("Ignoring channel.follow.v2 without active session")
            return

        followed_at = data.followed_at.isoformat()
        user_id = str(data.user.id)
        follow_events = self.active_session["metrics"]["follow_events"]

        duplicate = any(
            event.get("user_id") == user_id and event.get("followed_at") == followed_at
            for event in follow_events
        )
        if duplicate:
            self.logger.info("Ignoring duplicate follow event for user %s at %s", user_id, followed_at)
            return

        follow_events.append(
            {
                "user_id": user_id,
                "user_login": data.user.name,
                "followed_at": followed_at,
                "source": "eventsub.channel_follow_v2",
            }
        )
        self._record_event(
            "channel.follow.v2",
            {
                "user_id": user_id,
                "user_login": data.user.name,
                "followed_at": followed_at,
            },
        )
        recalculate_all_metrics(self.active_session)
        self._save_active_session()

    def start_session_from_stream(self, stream, source: str):
        self.start_session(
            stream_id=str(stream.id),
            started_at=stream.started_at.isoformat(),
            title=stream.title,
            category_name=stream.game_name,
            category_id=str(stream.game_id),
            source=source,
        )

    def start_session(
        self,
        *,
        stream_id: str,
        started_at: str,
        title: str | None,
        category_name: str | None,
        category_id: str | None,
        source: str,
    ):
        self.active_session = create_new_session(
            stream_id=stream_id,
            started_at=started_at,
            title=title,
            category_name=category_name,
            category_id=category_id,
            source=source,
            broadcaster_id=str(self.broadcaster_id) if self.broadcaster_id else None,
        )
        self._record_event(
            "stream.session_started",
            {
                "stream_id": stream_id,
                "started_at": started_at,
                "title": title,
                "category_name": category_name,
                "source": source,
            },
        )
        self._save_active_session()
        self.logger.info("Started runtime stream session %s", stream_id)

    async def finalize_active_session(self, *, ended_at: str, reason: str):
        if not self.active_session:
            return

        if self.sampling_task and not self.sampling_task.done():
            self.sampling_task.cancel()
            try:
                await self.sampling_task
            except asyncio.CancelledError:
                pass
        self.sampling_task = None

        ended_dt = parse_iso(ended_at)
        now_dt = datetime.now(timezone.utc)
        if ended_dt is None or (now_dt - ended_dt).total_seconds() <= 30:
            await self.capture_runtime_sample(
                stream_snapshot=None,
                reason="stream.finalize",
                allow_offline_followers=True,
            )

        session = deepcopy(self.active_session)
        session["status"] = "completed"
        session["ended_at"] = ended_at
        session["duration_minutes"] = calculate_duration_minutes(
            session.get("started_at"),
            ended_at,
        )

        for segment in session.get("game_segments", []):
            if segment.get("ended_at") is None:
                segment["ended_at"] = ended_at

        recalculate_all_metrics(session)
        storage.append_completed_session(session, reason)
        self.logger.info(
            "Finalized runtime stream session %s with reason %s",
            session.get("stream_id"),
            reason,
        )

        self.active_session = None
        self._save_active_session()

    async def ensure_sampling_started(self, initial_stream=None):
        if not self.active_session:
            return

        if initial_stream is not None:
            await self.capture_runtime_sample(stream_snapshot=initial_stream, reason="sampling.initial")

        if self.sampling_task and not self.sampling_task.done():
            return

        self.sampling_task = self.bot.loop.create_task(self._sampling_loop())
        self.logger.info("Started runtime sampling loop with interval %ss", self.sample_interval_seconds)

    async def _sampling_loop(self):
        try:
            while self.active_session:
                await asyncio.sleep(self.sample_interval_seconds)
                await self.capture_runtime_sample(reason="sampling.loop")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("Runtime sampling loop failed: %s", exc)

    async def capture_runtime_sample(self, stream_snapshot=None, reason: str = "sample", allow_offline_followers: bool = False):
        if not self.active_session:
            return

        active_stream_id = self.active_session.get("stream_id")
        sample_timestamp = now_iso()

        current_stream = stream_snapshot
        if current_stream is None:
            try:
                current_stream = await sampler.fetch_live_stream_with_refresh(self.bot, settings.TWITCH_ACCESS_TOKEN)
            except Exception as exc:
                self.logger.warning("Failed to fetch live stream snapshot for %s: %s", reason, exc)
                current_stream = None

        if current_stream is not None and str(current_stream.id) == str(active_stream_id):
            self._sync_session_from_stream_snapshot(current_stream, reason)
            self._append_viewer_sample(current_stream.viewer_count, sample_timestamp, reason)
        elif current_stream is not None and str(current_stream.id) != str(active_stream_id):
            self.logger.info(
                "Skipping sample for mismatched live stream %s while active session is %s",
                current_stream.id,
                active_stream_id,
            )
        elif not allow_offline_followers:
            self.logger.info("Live stream snapshot unavailable for %s", reason)

        if self.followers_sampling_enabled:
            follower_count = await sampler.fetch_followers_count(self.bot, self.broadcaster_id, settings.TWITCH_ACCESS_TOKEN)
            if follower_count is not None:
                self._append_follower_sample(follower_count, sample_timestamp, reason)
            else:
                self.followers_sampling_enabled = False

        recalculate_all_metrics(self.active_session)
        self._save_active_session()

    def _sync_session_from_stream_snapshot(self, stream, reason: str):
        changed_at = now_iso()

        if not self.active_session.get("title") and stream.title:
            self.active_session["title"] = stream.title
            self.active_session["title_start"] = stream.title
            self.active_session["title_current"] = stream.title
            self.active_session["title_history"].append(
                {
                    "title": stream.title,
                    "changed_at": changed_at,
                    "source": reason,
                }
            )

        if not self.active_session.get("category_name") and stream.game_name:
            self.active_session["category_name"] = stream.game_name
            self.active_session["category_id"] = str(stream.game_id)
            self.active_session["category_history"].append(
                {
                    "category_id": str(stream.game_id),
                    "category_name": stream.game_name,
                    "changed_at": changed_at,
                    "source": reason,
                }
            )
            self.active_session["game_segments"].append(
                {
                    "category_id": str(stream.game_id),
                    "category_name": stream.game_name,
                    "started_at": self.active_session.get("started_at") or changed_at,
                    "ended_at": None,
                    "source": reason,
                }
            )

    def _append_viewer_sample(self, viewer_count: int, sampled_at: str, reason: str):
        samples = self.active_session["metrics"]["viewer_samples"]
        samples.append(
            {
                "viewer_count": int(viewer_count),
                "sampled_at": sampled_at,
                "source": reason,
            }
        )

    def _append_follower_sample(self, followers_total: int, sampled_at: str, reason: str):
        metrics = self.active_session["metrics"]
        metrics["follower_samples"].append(
            {
                "followers_total": int(followers_total),
                "sampled_at": sampled_at,
                "source": reason,
            }
        )
        if metrics["followers_start"] is None:
            metrics["followers_start"] = int(followers_total)
        metrics["followers_end"] = int(followers_total)

    def _update_game_segments(self, *, category_id: str | None, category_name: str | None, changed_at: str):
        game_segments = self.active_session["game_segments"]
        if game_segments and game_segments[-1].get("ended_at") is None:
            game_segments[-1]["ended_at"] = changed_at

        game_segments.append(
            {
                "category_id": category_id,
                "category_name": category_name,
                "started_at": changed_at,
                "ended_at": None,
                "source": "eventsub.channel_update",
            }
        )

    def _record_event(self, event_type: str, payload: dict):
        if not self.active_session:
            return

        self.active_session["events"].append(
            {
                "type": event_type,
                "timestamp": now_iso(),
                "payload": payload,
            }
        )
        self.active_session["collector"]["updated_at"] = now_iso()

    def _save_active_session(self):
        if self.active_session:
            ensure_session_shape(self.active_session)
            self.active_session["collector"]["updated_at"] = now_iso()
        storage.save_active_session(self.active_session)
