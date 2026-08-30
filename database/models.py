from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from database.db import Base


# =====================================================
# CORE DOMAIN
# =====================================================


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    slug = Column(Text, unique=True)
    game_type = Column(String(20))
    parent_game_id = Column(Integer, ForeignKey("games.id"))
    franchise_id = Column(Integer, ForeignKey("games_franchises.id"))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    aliases = relationship("GameAlias", back_populates="game", cascade="all, delete-orphan")
    stream_games = relationship("StreamGame", back_populates="game", cascade="all, delete-orphan")
    igdb_metadata = relationship("GameMetadataIGDB", back_populates="game", uselist=False, cascade="all, delete-orphan")
    hltb_metadata = relationship("GameMetadataHLTB", back_populates="game", uselist=False, cascade="all, delete-orphan")
    game_stats = relationship("GameStats", back_populates="game", uselist=False, cascade="all, delete-orphan")


class Stream(Base):
    __tablename__ = "streams"

    id = Column(Integer, primary_key=True)
    external_id = Column(Text, unique=True)
    title = Column(Text)
    started_at = Column(DateTime, index=True)
    ended_at = Column(DateTime)
    duration_minutes = Column(Integer)
    avg_viewers = Column(Integer)
    max_viewers = Column(Integer)
    followers_gained = Column(Integer)
    views_gained = Column(Integer)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    stream_games = relationship("StreamGame", back_populates="stream", cascade="all, delete-orphan")
    stream_recordings = relationship("StreamRecording", back_populates="stream", cascade="all, delete-orphan")
    streamers_on_stream = relationship("User", secondary="streamers_on_stream", backref="streams_on")
    titles = relationship("StreamTitle", back_populates="stream", cascade="all, delete-orphan",
                          order_by="StreamTitle.started_at")


class StreamTitle(Base):
    __tablename__ = "stream_titles"

    id = Column(BigInteger, primary_key=True)
    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=False, index=True)
    title = Column(Text, nullable=False)
    started_at = Column(DateTime, nullable=False)
    is_initial = Column(Boolean, default=False)
    created_at = Column(DateTime)

    stream = relationship("Stream", back_populates="titles")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    twitch_user_id = Column(Text, unique=True)
    login = Column(Text, unique=True)
    display_name = Column(Text)
    profile_image_url = Column(Text)
    birthday_calendar_day_id = Column(Integer, ForeignKey("calendar_days.id"))
    birthday_set_at = Column(DateTime)
    birthday_changed_at = Column(DateTime)
    is_streamer = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    is_trusted = Column(Boolean, default=False)
    twitch_management = Column(Boolean, default=False)
    duels_win = Column(Integer)
    duels_lose = Column(Integer)
    duels_draw = Column(Integer)
    twitch_url = Column(Text)
    last_seen_at = Column(DateTime)
    created_at = Column(DateTime)


class Duels(Base):
    __tablename__ = "duels"

    id = Column(BigInteger, primary_key=True)
    challenger_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    winner_id = Column(Integer, ForeignKey("users.id"))
    is_draw = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False)


class GamesFranchise(Base):
    __tablename__ = "games_franchises"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)


class Genre(Base):
    __tablename__ = "genres"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    slug = Column(Text, unique=True)
    abbreviation = Column(Text)
    created_at = Column(DateTime)


class Platform(Base):
    __tablename__ = "platforms"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    slug = Column(Text, unique=True)
    abbreviation = Column(Text)
    created_at = Column(DateTime)


class Clip(Base):
    __tablename__ = "clips"

    id = Column(BigInteger, primary_key=True)
    external_id = Column(Text, unique=True, nullable=False)
    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"))
    creator_user_id = Column(Integer, ForeignKey("users.id"))
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    thumbnail_url = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False)
    clip_offset_seconds = Column(Integer)
    duration_seconds = Column(Integer)
    views_count = Column(Integer)
    synced_at = Column(DateTime)


clip_tags = Table(
    "clip_tags",
    Base.metadata,
    Column("clip_id", BigInteger, ForeignKey("clips.id"), nullable=False, primary_key=True),
    Column("tag", Text, nullable=False, primary_key=True),
    Column("normalized_tag", Text, nullable=False),
    Index("ix_clip_tags_normalized_tag", "normalized_tag"),
    UniqueConstraint("clip_id", "normalized_tag", name="uq_clip_tags_clip_normalized"),
)


class CalendarDay(Base):
    __tablename__ = "calendar_days"

    id = Column(Integer, primary_key=True)
    day = Column(SmallInteger, nullable=False)
    month = Column(SmallInteger, nullable=False)
    day_of_year = Column(SmallInteger)
    __table_args__ = (
        UniqueConstraint("month", "day", name="uq_calendar_days_month_day"),
    )


class Holiday(Base):
    __tablename__ = "holidays"

    id = Column(Integer, primary_key=True)
    calendar_day_id = Column(Integer, ForeignKey("calendar_days.id"), nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime)
    __table_args__ = (
        Index("ix_holidays_calendar_day_id", "calendar_day_id"),
    )


class FamousPerson(Base):
    __tablename__ = "famous_persons"

    id = Column(Integer, primary_key=True)
    calendar_day_id = Column(Integer, ForeignKey("calendar_days.id"), nullable=False)
    full_name = Column(Text, nullable=False)
    profession = Column(String(50))
    description = Column(Text)
    wiki_url = Column(Text)
    image_url = Column(Text)
    created_at = Column(DateTime)
    __table_args__ = (
        Index("ix_famous_persons_calendar_day_id", "calendar_day_id"),
    )


class BotCommand(Base):
    __tablename__ = "bot_commands"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False, unique=True)
    description = Column(Text, nullable=False)
    created_at = Column(DateTime)
    is_visible = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    bot_name = Column(String(50), nullable=False, default="self")


class BotCommandAlias(Base):
    __tablename__ = "bot_command_aliases"

    id = Column(Integer, primary_key=True)
    command_id = Column(Integer, ForeignKey("bot_commands.id"), nullable=False)
    alias = Column(String(50), nullable=False, unique=True)


class CommandUsageLog(Base):
    __tablename__ = "command_usage_logs"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    streamer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    command_id = Column(Integer, ForeignKey("bot_commands.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)
    __table_args__ = (
        Index("ix_command_usage_logs_user_id", "user_id"),
        Index("ix_command_usage_logs_command_id", "command_id"),
        Index("ix_command_usage_logs_streamer_id", "streamer_id"),
        Index("ix_command_usage_logs_created_at", "created_at"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    streamer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    __table_args__ = (
        Index("ix_chat_messages_user_id", "user_id"),
        Index("ix_chat_messages_streamer_id", "streamer_id"),
        Index("ix_chat_messages_created_at", "created_at"),
    )


class VipHistory(Base):
    __tablename__ = "vip_history"

    id = Column(BigInteger, primary_key=True)
    streamer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    granted_by_user_id = Column(Integer, ForeignKey("users.id"))
    granted_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime)
    reason = Column(Text)
    __table_args__ = (
        Index("ix_vip_history_streamer_id", "streamer_id"),
        Index("ix_vip_history_user_id", "user_id"),
    )


class TwitchAuthorization(Base):
    __tablename__ = "twitch_authorizations"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    scopes = Column(Text)
    expires_at = Column(DateTime)
    last_refresh_at = Column(DateTime)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class WebSession(Base):
    __tablename__ = "web_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False)
    last_activity_at = Column(DateTime)


class TwitchReward(Base):
    __tablename__ = "twitch_rewards"

    id = Column(BigInteger, primary_key=True)
    streamer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    twitch_reward_id = Column(String(100), nullable=False, unique=True)
    title = Column(Text, nullable=False)
    prompt = Column(Text)
    cost = Column(Integer, nullable=False)
    is_enabled = Column(Boolean, nullable=False)
    background_color = Column(String(20))
    is_paused = Column(Boolean)
    synced_at = Column(DateTime)
    created_at = Column(DateTime)
    __table_args__ = (
        Index("ix_twitch_rewards_streamer_id", "streamer_id"),
    )


class TwitchRewardRedemption(Base):
    __tablename__ = "twitch_reward_redemptions"

    id = Column(BigInteger, primary_key=True)
    reward_id = Column(BigInteger, ForeignKey("twitch_rewards.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    streamer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    twitch_redemption_id = Column(String(100))
    user_input = Column(Text)
    redeemed_at = Column(DateTime, nullable=False)
    fulfilled_at = Column(DateTime)
    status = Column(String(20))
    __table_args__ = (
        Index("ix_twitch_reward_redemptions_reward_id", "reward_id"),
        Index("ix_twitch_reward_redemptions_user_id", "user_id"),
        Index("ix_twitch_reward_redemptions_streamer_id", "streamer_id"),
    )


# =====================================================
# DUELS
# =====================================================


class DuelsFranchise(Base):
    __tablename__ = "duels_franchises"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    description = Column(Text)
    created_at = Column(DateTime)


class DuelsCharacter(Base):
    __tablename__ = "duels_characters"

    id = Column(Integer, primary_key=True)
    franchise_id = Column(Integer, ForeignKey("duels_franchises.id"), nullable=False)
    name = Column(Text, nullable=False)
    created_at = Column(DateTime)
    __table_args__ = (
        Index("ix_duels_characters_franchise_id", "franchise_id"),
    )


class DuelsCharacterVersion(Base):
    __tablename__ = "duels_character_versions"

    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("duels_characters.id"), nullable=False)
    version_name = Column(Text, nullable=False)
    description = Column(Text)
    power_tier = Column(SmallInteger)
    created_at = Column(DateTime)
    __table_args__ = (
        Index("ix_duels_character_versions_character_id", "character_id"),
    )


class DuelsAbility(Base):
    __tablename__ = "duels_abilities"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime)


duels_version_abilities = Table(
    "duels_version_abilities",
    Base.metadata,
    Column("version_id", Integer, ForeignKey("duels_character_versions.id"), nullable=False, primary_key=True),
    Column("ability_id", Integer, ForeignKey("duels_abilities.id"), nullable=False, primary_key=True),
    UniqueConstraint("version_id", "ability_id", name="uq_duels_version_abilities"),
)


class DuelsScenario(Base):
    __tablename__ = "duels_scenarios"

    id = Column(BigInteger, primary_key=True)
    franchise_id = Column(Integer, ForeignKey("duels_franchises.id"), nullable=False)
    challenger_version_id = Column(Integer, ForeignKey("duels_character_versions.id"), nullable=False)
    target_version_id = Column(Integer, ForeignKey("duels_character_versions.id"), nullable=False)
    step1 = Column(String(250))
    step2 = Column(String(250))
    step3 = Column(String(250))
    step4 = Column(String(250))
    is_draw = Column(Boolean, default=False)
    winner_version_id = Column(Integer, ForeignKey("duels_character_versions.id"))
    created_at = Column(DateTime)
    __table_args__ = (
        Index("ix_duels_scenarios_franchise_id", "franchise_id"),
        Index("ix_duels_scenarios_challenger_version_id", "challenger_version_id"),
        Index("ix_duels_scenarios_target_version_id", "target_version_id"),
    )


class DuelsScenarioUsage(Base):
    __tablename__ = "duels_scenario_usage"

    id = Column(BigInteger, primary_key=True)
    streamer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scenario_id = Column(BigInteger, ForeignKey("duels_scenarios.id"), nullable=False)
    used_at = Column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint("streamer_id", "scenario_id", name="uq_duels_scenario_usage"),
        Index("ix_duels_scenario_usage_streamer_id", "streamer_id"),
    )


# =====================================================
# RELATIONS
# =====================================================


class StreamGame(Base):
    __tablename__ = "stream_games"

    id = Column(BigInteger, primary_key=True)
    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    position = Column(Integer, nullable=False)
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    duration_minutes = Column(Integer)
    avg_viewers = Column(Integer)
    peak_viewers = Column(Integer)
    followers_gained = Column(Integer)
    __table_args__ = (
        UniqueConstraint("stream_id", "position", name="uq_stream_games_stream_position"),
        Index("ix_stream_games_stream_id", "stream_id"),
        Index("ix_stream_games_game_id", "game_id"),
    )

    stream = relationship("Stream", back_populates="stream_games")
    game = relationship("Game", back_populates="stream_games")


class StreamRecording(Base):
    __tablename__ = "stream_recordings"

    id = Column(BigInteger, primary_key=True)
    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=False)
    source = Column(String(20), nullable=False)
    url = Column(Text, nullable=False)
    duration_minutes = Column(Integer)
    recorded_at = Column(DateTime)
    created_at = Column(DateTime)
    __table_args__ = (
        Index("ix_stream_recordings_stream_id", "stream_id"),
        Index("ix_stream_recordings_source", "source"),
    )

    stream = relationship("Stream", back_populates="stream_recordings")


streamers_on_stream = Table(
    "streamers_on_stream",
    Base.metadata,
    Column("stream_id", Integer, ForeignKey("streams.id"), nullable=False, primary_key=True),
    Column("streamer_id", Integer, ForeignKey("users.id"), nullable=False, primary_key=True),
    Column("role", String(20)),
    Column("created_at", DateTime),
    UniqueConstraint("stream_id", "streamer_id", name="uq_streamers_on_stream"),
)


streamer_games = Table(
    "streamer_games",
    Base.metadata,
    Column("streamer_id", Integer, ForeignKey("users.id"), nullable=False, primary_key=True),
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False, primary_key=True),
    Column("interested", Boolean, default=False),
    Column("liked", Boolean, default=False),
    Column("completed", Boolean, default=False),
    Column("updated_at", DateTime),
    UniqueConstraint("streamer_id", "game_id", name="uq_streamer_games"),
)


game_recommendations = Table(
    "game_recommendations",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, primary_key=True),
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False, primary_key=True),
    Column("recommendation_note", Text),
    Column("created_at", DateTime),
    Index("ix_game_recommendations_game_id", "game_id"),
    UniqueConstraint("user_id", "game_id", name="uq_game_recommendations_user_game"),
)


game_genres = Table(
    "game_genres",
    Base.metadata,
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False, primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id"), nullable=False, primary_key=True),
    UniqueConstraint("game_id", "genre_id", name="uq_game_genres"),
    Index("ix_game_genres_genre_id", "genre_id"),
)


game_platforms = Table(
    "game_platforms",
    Base.metadata,
    Column("game_id", Integer, ForeignKey("games.id"), nullable=False, primary_key=True),
    Column("platform_id", Integer, ForeignKey("platforms.id"), nullable=False, primary_key=True),
    UniqueConstraint("game_id", "platform_id", name="uq_game_platforms"),
    Index("ix_game_platforms_platform_id", "platform_id"),
)


# =====================================================
# EXTERNAL METADATA
# =====================================================


class GameMetadataIGDB(Base):
    __tablename__ = "game_metadata_igdb"

    game_id = Column(Integer, ForeignKey("games.id"), primary_key=True)
    igdb_id = Column(Text, nullable=False, unique=True)
    is_primary = Column(Boolean, default=False)
    release_date = Column(DateTime)
    igdb_name = Column(Text)
    steam_url = Column(Text)
    igdb_score = Column(Float)
    steam_score = Column(Float)
    description_en = Column(Text)
    description_ru = Column(Text)
    cover_url = Column(Text)
    raw_payload = Column(JSONB)
    synced_at = Column(DateTime)

    game = relationship("Game", back_populates="igdb_metadata")


class GameMetadataHLTB(Base):
    __tablename__ = "game_metadata_hltb"

    game_id = Column(Integer, ForeignKey("games.id"), primary_key=True)
    hltb_id = Column(Text)
    hltb_name = Column(Text)
    hltb_main_story = Column(Float)
    hltb_main_extra = Column(Float)
    hltb_completionist = Column(Float)
    hltb_all_styles = Column(Float)
    hltb_coop = Column(Float)
    hltb_multiplayer = Column(Float)
    hltb_review_score = Column(Integer)
    review_count = Column(Integer)
    synced_at = Column(DateTime)

    game = relationship("Game", back_populates="hltb_metadata")


class GameStats(Base):
    __tablename__ = "game_stats"

    game_id = Column(Integer, ForeignKey("games.id"), primary_key=True)
    streamed_hours = Column(Float)
    avg_viewers = Column(Integer)
    max_viewers = Column(Integer)
    followers_per_hour = Column(Float)
    streams_count = Column(Integer)
    last_stream = Column(DateTime)
    synced_at = Column(DateTime, nullable=False)

    game = relationship("Game", back_populates="game_stats")


class GameAlias(Base):
    __tablename__ = "game_aliases"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    alias = Column(Text, nullable=False)
    normalized_alias = Column(Text, nullable=False)
    is_primary = Column(Boolean)
    source = Column(Text)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    __table_args__ = (
        UniqueConstraint("game_id", "normalized_alias", name="uq_game_aliases_game_normalized"),
        Index("ix_game_aliases_normalized_alias", "normalized_alias"),
    )

    game = relationship("Game", back_populates="aliases")


# =====================================================
# RUNTIME ANALYTICS
# =====================================================


class StreamRuntimeSample(Base):
    __tablename__ = "stream_runtime_samples"

    id = Column(Integer, primary_key=True)
    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=False)
    sampled_at = Column(DateTime, nullable=False)
    viewers = Column(Integer)
    followers = Column(Integer)
    title = Column(Text)
    category_name = Column(Text)
    __table_args__ = (
        Index("ix_stream_runtime_samples_stream_sampled", "stream_id", "sampled_at"),
    )


class StreamRuntimeBucket(Base):
    __tablename__ = "stream_runtime_buckets"

    id = Column(Integer, primary_key=True)
    stream_id = Column(Integer, ForeignKey("streams.id"), nullable=False)
    bucket_start = Column(DateTime)
    bucket_end = Column(DateTime)
    avg_viewers = Column(Integer)
    max_viewers = Column(Integer)
    followers_gained = Column(Integer)
    __table_args__ = (
        Index("ix_stream_runtime_buckets_stream_start", "stream_id", "bucket_start"),
    )
