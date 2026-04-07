import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from twitchio.http import Route

from config.settings import STREAM_RUNTIME_SAMPLE_SECONDS, TWITCH_ACCESS_TOKEN, TWITCH_CHANNEL
from utils.logger import get_logger


RUNTIME_DIR = Path(__file__).resolve().parent.parent / "storage" / "runtime"
ACTIVE_SESSION_FILE = RUNTIME_DIR / "active_stream_session.json"
COMPLETED_SESSIONS_FILE = RUNTIME_DIR / "completed_stream_sessions.json"
COLLECTOR_STATE_VERSION = 2


class RuntimeStreamCollector:
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("runtime.collector")
        self.broadcaster_id = None
        self.active_session = None
        self.token = TWITCH_ACCESS_TOKEN
        self.sample_interval_seconds = STREAM_RUNTIME_SAMPLE_SECONDS
        self.sampling_task = None
        self.followers_sampling_enabled = True

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    async def bootstrap(self, broadcaster_id: int, token: str):
        self.broadcaster_id = int(broadcaster_id)
        self.token = token
        self.active_session = self._load_json(ACTIVE_SESSION_FILE)
        self._ensure_session_shape(self.active_session)

        live_stream = await self.fetch_live_stream()
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
            await self.finalize_active_session(
                ended_at=self._now_iso(),
                reason="recovered_new_live_stream_on_bootstrap",
            )

        if self.active_session and not live_stream:
            self.logger.info("Finalizing stale active session without stream.offline event")
            await self.finalize_active_session(
                ended_at=self._now_iso(),
                reason="recovered_offline_on_bootstrap",
            )

        if live_stream and not self.active_session:
            self.logger.info("Creating active session from current live stream snapshot %s", live_stream.id)
            self.start_session_from_stream(live_stream, source="bootstrap")
            await self.capture_runtime_sample(stream_snapshot=live_stream, reason="bootstrap")
            await self.ensure_sampling_started()

    async def fetch_live_stream(self):
        streams = await self.bot.fetch_streams(
            user_logins=[TWITCH_CHANNEL],
            token=self.token,
            type="live",
        )
        return streams[0] if streams else None

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
            await self.finalize_active_session(
                ended_at=self._now_iso(),
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
            ended_at=ended_at or self._now_iso(),
            reason="eventsub.stream_offline",
        )

    def handle_channel_update(self, data):
        if not self.active_session:
            self.logger.info("Ignoring channel.update for collector without active session")
            return

        changed_at = self._now_iso()
        if self.active_session.get("title") != data.title:
            self.active_session["title"] = data.title
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
        self._recalculate_metrics(self.active_session)
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
        self.active_session = {
            "version": COLLECTOR_STATE_VERSION,
            "status": "active",
            "channel_login": TWITCH_CHANNEL,
            "broadcaster_id": str(self.broadcaster_id) if self.broadcaster_id is not None else None,
            "stream_id": str(stream_id),
            "started_at": started_at,
            "ended_at": None,
            "duration_minutes": None,
            "title": title,
            "category_name": category_name,
            "category_id": category_id,
            "title_history": [],
            "category_history": [],
            "game_segments": [],
            "events": [],
            "metrics": {
                "sample_interval_seconds": self.sample_interval_seconds,
                "viewer_samples": [],
                "follower_samples": [],
                "follow_events": [],
                "avg_viewers": None,
                "max_viewers": None,
                "followers_start": None,
                "followers_end": None,
                "followers_delta": None,
                "followers_delta_exact": None,
            },
            "collector": {
                "created_at": self._now_iso(),
                "updated_at": self._now_iso(),
                "source": source,
            },
        }

        if title:
            self.active_session["title_history"].append(
                {"title": title, "changed_at": started_at, "source": source}
            )

        if category_name:
            self.active_session["category_history"].append(
                {
                    "category_id": category_id,
                    "category_name": category_name,
                    "changed_at": started_at,
                    "source": source,
                }
            )
            self.active_session["game_segments"].append(
                {
                    "category_id": category_id,
                    "category_name": category_name,
                    "started_at": started_at,
                    "ended_at": None,
                    "source": source,
                }
            )

        self._record_event(
            "stream.session_started",
            {
                "stream_id": str(stream_id),
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

        await self.capture_runtime_sample(stream_snapshot=None, reason="stream.finalize", allow_offline_followers=True)

        session = deepcopy(self.active_session)
        session["status"] = "completed"
        session["ended_at"] = ended_at
        session["duration_minutes"] = self._calculate_duration_minutes(
            session.get("started_at"),
            ended_at,
        )

        for segment in session.get("game_segments", []):
            if segment.get("ended_at") is None:
                segment["ended_at"] = ended_at

        self._recalculate_metrics(session)
        self._append_completed_session(session, reason)
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
        sample_timestamp = self._now_iso()

        current_stream = stream_snapshot
        if current_stream is None:
            try:
                current_stream = await self.fetch_live_stream()
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
            follower_count = await self.fetch_followers_count()
            if follower_count is not None:
                self._append_follower_sample(follower_count, sample_timestamp, reason)

        self._recalculate_metrics(self.active_session)
        self._save_active_session()

    async def fetch_followers_count(self) -> int | None:
        route = Route(
            "GET",
            "channels/followers",
            query=[
                ("broadcaster_id", str(self.broadcaster_id)),
                ("first", "1"),
            ],
            token=self.token,
        )

        try:
            response = await self.bot._http.request(route, paginate=False, full_body=True)
        except Exception as exc:
            if self.followers_sampling_enabled:
                self.logger.warning(
                    "Followers sampling disabled: failed to fetch followers total. "
                    "Check moderator:read:followers scope and moderator access. (%s)",
                    exc,
                )
            self.followers_sampling_enabled = False
            return None

        total = response.get("total")
        return int(total) if total is not None else None

    def _sync_session_from_stream_snapshot(self, stream, reason: str):
        changed_at = self._now_iso()

        if not self.active_session.get("title") and stream.title:
            self.active_session["title"] = stream.title
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

    def _append_completed_session(self, session: dict, reason: str):
        completed = self._load_json(COMPLETED_SESSIONS_FILE, default={"version": COLLECTOR_STATE_VERSION, "sessions": []})
        completed.setdefault("version", COLLECTOR_STATE_VERSION)
        completed.setdefault("sessions", [])

        session["collector"]["completed_at"] = self._now_iso()
        session["collector"]["completion_reason"] = reason
        completed["sessions"].append(session)
        self._write_json(COMPLETED_SESSIONS_FILE, completed)

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
                "timestamp": self._now_iso(),
                "payload": payload,
            }
        )
        self.active_session["collector"]["updated_at"] = self._now_iso()

    def _recalculate_metrics(self, session: dict):
        self._ensure_session_shape(session)
        metrics = session.get("metrics", {})
        viewer_samples = metrics.get("viewer_samples", [])
        follower_samples = metrics.get("follower_samples", [])

        if viewer_samples:
            viewer_counts = [sample["viewer_count"] for sample in viewer_samples]
            metrics["avg_viewers"] = round(sum(viewer_counts) / len(viewer_counts), 2)
            metrics["max_viewers"] = max(viewer_counts)

        if follower_samples:
            followers_totals = [sample["followers_total"] for sample in follower_samples]
            metrics["followers_start"] = followers_totals[0]
            metrics["followers_end"] = followers_totals[-1]
            metrics["followers_delta"] = followers_totals[-1] - followers_totals[0]

        follow_events = metrics.get("follow_events", [])
        metrics["followers_delta_exact"] = len(follow_events)
        self._recalculate_segment_follow_metrics(session)

    def _save_active_session(self):
        if self.active_session is None:
            if ACTIVE_SESSION_FILE.exists():
                ACTIVE_SESSION_FILE.unlink()
            return

        self._ensure_session_shape(self.active_session)
        self.active_session["collector"]["updated_at"] = self._now_iso()
        self._write_json(ACTIVE_SESSION_FILE, self.active_session)

    def _ensure_session_shape(self, session: dict | None):
        if not session:
            return

        session.setdefault("version", COLLECTOR_STATE_VERSION)
        session.setdefault("events", [])
        session.setdefault("title_history", [])
        session.setdefault("category_history", [])
        session.setdefault("game_segments", [])
        session.setdefault("collector", {})
        session["collector"].setdefault("created_at", self._now_iso())
        session["collector"].setdefault("updated_at", self._now_iso())
        session["collector"].setdefault("source", "unknown")
        session.setdefault(
            "metrics",
            {
                "sample_interval_seconds": self.sample_interval_seconds,
                "viewer_samples": [],
                "follower_samples": [],
                "follow_events": [],
                "avg_viewers": None,
                "max_viewers": None,
                "followers_start": None,
                "followers_end": None,
                "followers_delta": None,
                "followers_delta_exact": None,
            },
        )
        session["metrics"].setdefault("sample_interval_seconds", self.sample_interval_seconds)
        session["metrics"].setdefault("viewer_samples", [])
        session["metrics"].setdefault("follower_samples", [])
        session["metrics"].setdefault("follow_events", [])
        session["metrics"].setdefault("avg_viewers", None)
        session["metrics"].setdefault("max_viewers", None)
        session["metrics"].setdefault("followers_start", None)
        session["metrics"].setdefault("followers_end", None)
        session["metrics"].setdefault("followers_delta", None)
        session["metrics"].setdefault("followers_delta_exact", None)

    def _recalculate_segment_follow_metrics(self, session: dict):
        segments = session.get("game_segments", [])
        follow_events = session.get("metrics", {}).get("follow_events", [])

        for segment in segments:
            segment["followers_gained"] = 0
            segment["followers_per_hour"] = 0.0

        if not segments:
            return

        for event in follow_events:
            followed_at = self._parse_iso(event.get("followed_at"))
            if followed_at is None:
                continue

            for segment in segments:
                started_at = self._parse_iso(segment.get("started_at"))
                ended_at = self._parse_iso(segment.get("ended_at"))
                if started_at is None or ended_at is None:
                    continue

                if started_at <= followed_at <= ended_at:
                    segment["followers_gained"] += 1
                    break

        for segment in segments:
            started_at = self._parse_iso(segment.get("started_at"))
            ended_at = self._parse_iso(segment.get("ended_at"))
            if started_at is None or ended_at is None or ended_at <= started_at:
                continue

            duration_hours = (ended_at - started_at).total_seconds() / 3600
            if duration_hours > 0:
                segment["followers_per_hour"] = round(segment["followers_gained"] / duration_hours, 2)

    @staticmethod
    def _load_json(path: Path, default=None):
        if not path.exists():
            return deepcopy(default)

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    @staticmethod
    def _write_json(path: Path, payload):
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    @staticmethod
    def _calculate_duration_minutes(started_at: str | None, ended_at: str | None) -> float | None:
        if not started_at or not ended_at:
            return None

        start_dt = datetime.fromisoformat(started_at)
        end_dt = datetime.fromisoformat(ended_at)
        duration = end_dt - start_dt
        return round(duration.total_seconds() / 60, 2)

    @staticmethod
    def _parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None

        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
