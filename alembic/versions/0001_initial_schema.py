"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =====================================================
    # CORE DOMAIN
    # =====================================================

    op.create_table(
        "games_franchises",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
    )

    op.create_table(
        "genres",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("slug", sa.Text(), unique=True),
        sa.Column("created_at", sa.DateTime()),
    )

    op.create_table(
        "platforms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("slug", sa.Text(), unique=True),
        sa.Column("created_at", sa.DateTime()),
    )

    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("slug", sa.Text(), unique=True),
        sa.Column("game_type", sa.String(20)),
        sa.Column("parent_game_id", sa.Integer(), sa.ForeignKey("games.id")),
        sa.Column("franchise_id", sa.Integer(), sa.ForeignKey("games_franchises.id")),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )

    op.create_table(
        "calendar_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("day_of_year", sa.SmallInteger()),
        sa.UniqueConstraint("month", "day", name="uq_calendar_days_month_day"),
    )

    op.create_table(
        "streams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.Text(), unique=True),
        sa.Column("title", sa.Text()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("ended_at", sa.DateTime()),
        sa.Column("duration_minutes", sa.Integer()),
        sa.Column("avg_viewers", sa.Integer()),
        sa.Column("max_viewers", sa.Integer()),
        sa.Column("followers_gained", sa.Integer()),
        sa.Column("views_gained", sa.Integer()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_streams_started_at", "streams", ["started_at"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("twitch_user_id", sa.Text(), unique=True),
        sa.Column("login", sa.Text(), unique=True),
        sa.Column("display_name", sa.Text()),
        sa.Column("profile_image_url", sa.Text()),
        sa.Column("birthday_calendar_day_id", sa.Integer(), sa.ForeignKey("calendar_days.id")),
        sa.Column("birthday_set_at", sa.DateTime()),
        sa.Column("birthday_changed_at", sa.DateTime()),
        sa.Column("is_streamer", sa.Boolean(), server_default="false"),
        sa.Column("is_admin", sa.Boolean(), server_default="false"),
        sa.Column("is_trusted", sa.Boolean(), server_default="false"),
        sa.Column("twitch_management", sa.Boolean(), server_default="false"),
        sa.Column("duels_win", sa.Integer()),
        sa.Column("duels_lose", sa.Integer()),
        sa.Column("duels_draw", sa.Integer()),
        sa.Column("twitch_url", sa.Text()),
        sa.Column("last_seen_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
    )

    op.create_table(
        "duels",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("challenger_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("winner_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("is_draw", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "holidays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("calendar_day_id", sa.Integer(), sa.ForeignKey("calendar_days.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_holidays_calendar_day_id", "holidays", ["calendar_day_id"])

    op.create_table(
        "famous_persons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("calendar_day_id", sa.Integer(), sa.ForeignKey("calendar_days.id"), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("profession", sa.String(50)),
        sa.Column("description", sa.Text()),
        sa.Column("wiki_url", sa.Text()),
        sa.Column("image_url", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_famous_persons_calendar_day_id", "famous_persons", ["calendar_day_id"])

    op.create_table(
        "bot_commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )

    op.create_table(
        "bot_command_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("command_id", sa.Integer(), sa.ForeignKey("bot_commands.id"), nullable=False),
        sa.Column("alias", sa.String(50), nullable=False, unique=True),
    )

    op.create_table(
        "command_usage_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("streamer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("command_id", sa.Integer(), sa.ForeignKey("bot_commands.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_command_usage_logs_user_id", "command_usage_logs", ["user_id"])
    op.create_index("ix_command_usage_logs_command_id", "command_usage_logs", ["command_id"])
    op.create_index("ix_command_usage_logs_streamer_id", "command_usage_logs", ["streamer_id"])
    op.create_index("ix_command_usage_logs_created_at", "command_usage_logs", ["created_at"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("streamer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])
    op.create_index("ix_chat_messages_streamer_id", "chat_messages", ["streamer_id"])
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])

    op.create_table(
        "vip_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("streamer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("granted_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("reason", sa.Text()),
    )
    op.create_index("ix_vip_history_streamer_id", "vip_history", ["streamer_id"])
    op.create_index("ix_vip_history_user_id", "vip_history", ["user_id"])

    op.create_table(
        "twitch_authorizations",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text()),
        sa.Column("expires_at", sa.DateTime()),
        sa.Column("last_refresh_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )

    op.create_table(
        "web_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime()),
    )

    op.create_table(
        "twitch_rewards",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("streamer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("twitch_reward_id", sa.String(100), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text()),
        sa.Column("cost", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("background_color", sa.String(20)),
        sa.Column("is_paused", sa.Boolean()),
        sa.Column("synced_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_twitch_rewards_streamer_id", "twitch_rewards", ["streamer_id"])

    op.create_table(
        "twitch_reward_redemptions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("reward_id", sa.BigInteger(), sa.ForeignKey("twitch_rewards.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("streamer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("twitch_redemption_id", sa.String(100)),
        sa.Column("user_input", sa.Text()),
        sa.Column("redeemed_at", sa.DateTime(), nullable=False),
        sa.Column("fulfilled_at", sa.DateTime()),
        sa.Column("status", sa.String(20)),
    )
    op.create_index("ix_twitch_reward_redemptions_reward_id", "twitch_reward_redemptions", ["reward_id"])
    op.create_index("ix_twitch_reward_redemptions_user_id", "twitch_reward_redemptions", ["user_id"])
    op.create_index("ix_twitch_reward_redemptions_streamer_id", "twitch_reward_redemptions", ["streamer_id"])

    # =====================================================
    # DUELS
    # =====================================================

    op.create_table(
        "duels_franchises",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )

    op.create_table(
        "duels_characters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("franchise_id", sa.Integer(), sa.ForeignKey("duels_franchises.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_duels_characters_franchise_id", "duels_characters", ["franchise_id"])

    op.create_table(
        "duels_character_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey("duels_characters.id"), nullable=False),
        sa.Column("version_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("power_tier", sa.SmallInteger()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_duels_character_versions_character_id", "duels_character_versions", ["character_id"])

    op.create_table(
        "duels_abilities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )

    op.create_table(
        "duels_version_abilities",
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("duels_character_versions.id"), nullable=False, primary_key=True),
        sa.Column("ability_id", sa.Integer(), sa.ForeignKey("duels_abilities.id"), nullable=False, primary_key=True),
        sa.UniqueConstraint("version_id", "ability_id", name="uq_duels_version_abilities"),
    )

    op.create_table(
        "duels_scenarios",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("franchise_id", sa.Integer(), sa.ForeignKey("duels_franchises.id"), nullable=False),
        sa.Column("challenger_version_id", sa.Integer(), sa.ForeignKey("duels_character_versions.id"), nullable=False),
        sa.Column("target_version_id", sa.Integer(), sa.ForeignKey("duels_character_versions.id"), nullable=False),
        sa.Column("step1", sa.String(250)),
        sa.Column("step2", sa.String(250)),
        sa.Column("step3", sa.String(250)),
        sa.Column("step4", sa.String(250)),
        sa.Column("is_draw", sa.Boolean(), server_default="false"),
        sa.Column("winner_version_id", sa.Integer(), sa.ForeignKey("duels_character_versions.id")),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_duels_scenarios_franchise_id", "duels_scenarios", ["franchise_id"])
    op.create_index("ix_duels_scenarios_challenger_version_id", "duels_scenarios", ["challenger_version_id"])
    op.create_index("ix_duels_scenarios_target_version_id", "duels_scenarios", ["target_version_id"])

    op.create_table(
        "duels_scenario_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("streamer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scenario_id", sa.BigInteger(), sa.ForeignKey("duels_scenarios.id"), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("streamer_id", "scenario_id", name="uq_duels_scenario_usage"),
    )
    op.create_index("ix_duels_scenario_usage_streamer_id", "duels_scenario_usage", ["streamer_id"])

    # =====================================================
    # RELATIONS
    # =====================================================

    op.create_table(
        "stream_games",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("ended_at", sa.DateTime()),
        sa.Column("duration_minutes", sa.Integer()),
        sa.Column("avg_viewers", sa.Integer()),
        sa.Column("peak_viewers", sa.Integer()),
        sa.Column("followers_gained", sa.Integer()),
        sa.UniqueConstraint("stream_id", "position", name="uq_stream_games_stream_position"),
    )
    op.create_index("ix_stream_games_stream_id", "stream_games", ["stream_id"])
    op.create_index("ix_stream_games_game_id", "stream_games", ["game_id"])

    op.create_table(
        "stream_recordings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("duration_minutes", sa.Integer()),
        sa.Column("recorded_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_stream_recordings_stream_id", "stream_recordings", ["stream_id"])
    op.create_index("ix_stream_recordings_source", "stream_recordings", ["source"])

    op.create_table(
        "streamers_on_stream",
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False, primary_key=True),
        sa.Column("streamer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, primary_key=True),
        sa.Column("role", sa.String(20)),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("stream_id", "streamer_id", name="uq_streamers_on_stream"),
    )

    op.create_table(
        "streamer_games",
        sa.Column("streamer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False, primary_key=True),
        sa.Column("interested", sa.Boolean(), server_default="false"),
        sa.Column("liked", sa.Boolean(), server_default="false"),
        sa.Column("completed", sa.Boolean(), server_default="false"),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("streamer_id", "game_id", name="uq_streamer_games"),
    )

    op.create_table(
        "game_recommendations",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False, primary_key=True),
        sa.Column("recommendation_note", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("user_id", "game_id", name="uq_game_recommendations_user_game"),
    )
    op.create_index("ix_game_recommendations_game_id", "game_recommendations", ["game_id"])

    op.create_table(
        "game_genres",
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False, primary_key=True),
        sa.Column("genre_id", sa.Integer(), sa.ForeignKey("genres.id"), nullable=False, primary_key=True),
        sa.UniqueConstraint("game_id", "genre_id", name="uq_game_genres"),
    )
    op.create_index("ix_game_genres_genre_id", "game_genres", ["genre_id"])

    op.create_table(
        "game_platforms",
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False, primary_key=True),
        sa.Column("platform_id", sa.Integer(), sa.ForeignKey("platforms.id"), nullable=False, primary_key=True),
        sa.UniqueConstraint("game_id", "platform_id", name="uq_game_platforms"),
    )
    op.create_index("ix_game_platforms_platform_id", "game_platforms", ["platform_id"])

    # =====================================================
    # EXTERNAL METADATA
    # =====================================================

    op.create_table(
        "game_metadata_igdb",
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), primary_key=True),
        sa.Column("igdb_id", sa.Text(), nullable=False, unique=True),
        sa.Column("is_primary", sa.Boolean(), server_default="false"),
        sa.Column("release_date", sa.DateTime()),
        sa.Column("igdb_name", sa.Text()),
        sa.Column("steam_url", sa.Text()),
        sa.Column("igdb_score", sa.Float()),
        sa.Column("steam_score", sa.Float()),
        sa.Column("description_en", sa.Text()),
        sa.Column("description_ru", sa.Text()),
        sa.Column("cover_url", sa.Text()),
        sa.Column("raw_payload", JSONB),
        sa.Column("synced_at", sa.DateTime()),
    )

    op.create_table(
        "game_metadata_hltb",
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), primary_key=True),
        sa.Column("hltb_id", sa.Text(), unique=True),
        sa.Column("hltb_name", sa.Text()),
        sa.Column("avg_hours", sa.Float()),
        sa.Column("main_story_hours", sa.Float()),
        sa.Column("main_extra_hours", sa.Float()),
        sa.Column("completionist_hours", sa.Float()),
        sa.Column("review_count", sa.Integer()),
        sa.Column("synced_at", sa.DateTime()),
    )

    op.create_table(
        "game_stats",
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), primary_key=True),
        sa.Column("streamed_hours", sa.Float()),
        sa.Column("avg_viewers", sa.Integer()),
        sa.Column("max_viewers", sa.Integer()),
        sa.Column("followers_per_hour", sa.Float()),
        sa.Column("streams_count", sa.Integer()),
        sa.Column("last_stream", sa.DateTime()),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "game_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean()),
        sa.Column("source", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("game_id", "normalized_alias", name="uq_game_aliases_game_normalized"),
    )
    op.create_index("ix_game_aliases_normalized_alias", "game_aliases", ["normalized_alias"])

    # =====================================================
    # CLIPS
    # =====================================================

    op.create_table(
        "clips",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("external_id", sa.Text(), nullable=False, unique=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id")),
        sa.Column("creator_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("thumbnail_url", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("clip_offset_seconds", sa.Integer()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("views_count", sa.Integer()),
        sa.Column("synced_at", sa.DateTime()),
    )

    op.create_table(
        "clip_tags",
        sa.Column("clip_id", sa.BigInteger(), sa.ForeignKey("clips.id"), nullable=False, primary_key=True),
        sa.Column("tag", sa.Text(), nullable=False, primary_key=True),
        sa.Column("normalized_tag", sa.Text(), nullable=False),
        sa.UniqueConstraint("clip_id", "normalized_tag", name="uq_clip_tags_clip_normalized"),
    )
    op.create_index("ix_clip_tags_normalized_tag", "clip_tags", ["normalized_tag"])

    # =====================================================
    # RUNTIME ANALYTICS
    # =====================================================

    op.create_table(
        "stream_runtime_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False),
        sa.Column("sampled_at", sa.DateTime(), nullable=False),
        sa.Column("viewers", sa.Integer()),
        sa.Column("followers", sa.Integer()),
        sa.Column("title", sa.Text()),
        sa.Column("category_name", sa.Text()),
    )
    op.create_index("ix_stream_runtime_samples_stream_sampled", "stream_runtime_samples", ["stream_id", "sampled_at"])

    op.create_table(
        "stream_runtime_buckets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False),
        sa.Column("bucket_start", sa.DateTime()),
        sa.Column("bucket_end", sa.DateTime()),
        sa.Column("avg_viewers", sa.Integer()),
        sa.Column("max_viewers", sa.Integer()),
        sa.Column("followers_gained", sa.Integer()),
    )
    op.create_index("ix_stream_runtime_buckets_stream_start", "stream_runtime_buckets", ["stream_id", "bucket_start"])


def downgrade() -> None:
    op.drop_table("stream_runtime_buckets")
    op.drop_table("stream_runtime_samples")
    op.drop_table("clip_tags")
    op.drop_table("clips")
    op.drop_table("game_aliases")
    op.drop_table("game_stats")
    op.drop_table("game_metadata_hltb")
    op.drop_table("game_metadata_igdb")
    op.drop_table("game_platforms")
    op.drop_table("game_genres")
    op.drop_table("game_recommendations")
    op.drop_table("streamer_games")
    op.drop_table("streamers_on_stream")
    op.drop_table("stream_recordings")
    op.drop_table("stream_games")
    op.drop_table("duels_scenario_usage")
    op.drop_table("duels_scenarios")
    op.drop_table("duels_version_abilities")
    op.drop_table("duels_abilities")
    op.drop_table("duels_character_versions")
    op.drop_table("duels_characters")
    op.drop_table("duels_franchises")
    op.drop_table("twitch_reward_redemptions")
    op.drop_table("twitch_rewards")
    op.drop_table("web_sessions")
    op.drop_table("twitch_authorizations")
    op.drop_table("vip_history")
    op.drop_table("chat_messages")
    op.drop_table("command_usage_logs")
    op.drop_table("bot_command_aliases")
    op.drop_table("bot_commands")
    op.drop_table("famous_persons")
    op.drop_table("holidays")
    op.drop_table("duels")
    op.drop_table("users")
    op.drop_table("calendar_days")
    op.drop_table("streams")
    op.drop_table("games")
    op.drop_table("platforms")
    op.drop_table("genres")
    op.drop_table("games_franchises")
