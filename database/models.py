from sqlalchemy import Column, Integer, String, Float, Table, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship

from database.db import Base


stream_games = Table(
    "stream_games",
    Base.metadata,
    Column("stream_id", Integer, ForeignKey("streams.id")),
    Column("game_id", Integer, ForeignKey("games.id"))
)

stream_participants = Table(
    "stream_participants",
    Base.metadata,
    Column("stream_id", ForeignKey("streams.id"), primary_key=True),
    Column("participant_id", ForeignKey("participants.id"), primary_key=True),
)


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True)

    streams = relationship("Stream", secondary=stream_games, back_populates="games")


class Stream(Base):
    __tablename__ = "streams"

    id = Column(Integer, primary_key=True)

    # 🔑 важно
    external_id = Column(String, unique=True)  # twitchtracker / twitch id

    date = Column(DateTime, index=True)
    duration = Column(Float)

    avg_viewers = Column(Integer)
    max_viewers = Column(Integer)
    followers = Column(Integer)
    views = Column(Integer)

    title = Column(String)

    # 🔗 новое
    vod_url = Column(String, nullable=True)
    clips_url = Column(String, nullable=True)

    games = relationship("Game", secondary=stream_games, back_populates="streams")
    participants = relationship(
        "Participant",
        secondary=stream_participants,
        back_populates="streams"
    )


class GameStats(Base):
    __tablename__ = "games_stats"

    game_id = Column(Integer, ForeignKey("games.id"), primary_key=True)

    # 🧠 ключевая вещь
    period = Column(String, primary_key=True)
    # например: "all", "30d", "7d"

    hours_streamed = Column(Float)
    avg_viewers = Column(Integer)
    max_viewers = Column(Integer)
    followers_per_hour = Column(Float)

    streams_count = Column(Integer)

    last_stream = Column(DateTime)

    game = relationship("Game")


class GameMeta(Base):
    __tablename__ = "games_meta"

    game_id = Column(Integer, ForeignKey("games.id"), primary_key=True)

    # ✍️ ручные данные
    liked = Column(Boolean, nullable=True)
    completed = Column(Boolean, nullable=True)
    review_url = Column(String, nullable=True) #клип/пост

    # ⏱ мета
    hltb_hours = Column(Float, nullable=True)

    # 🔗 ссылки
    steam_url = Column(String, nullable=True)
    clips_url = Column(String, nullable=True)

    game = relationship("Game")


class Participant(Base):
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True)

    name = Column(String, unique=True, index=True)   # Juice
    display_name = Column(String)                    # @Juice

    # опционально (очень полезно)
    twitch_url = Column(String, nullable=True)

    streams = relationship(
        "Stream",
        secondary=stream_participants,
        back_populates="participants"
    )
