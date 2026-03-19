from sqlalchemy import Column, Integer, String, Float, Table, ForeignKey, DateTime
from sqlalchemy.orm import relationship

from database.db import Base


stream_games = Table(
    "stream_games",
    Base.metadata,
    Column("stream_id", Integer, ForeignKey("streams.id")),
    Column("game_id", Integer, ForeignKey("games.id"))
)


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)

    streams = relationship("Stream", secondary=stream_games, back_populates="games")


class Stream(Base):
    __tablename__ = "streams"

    id = Column(Integer, primary_key=True)
    date = Column(DateTime)
    duration = Column(Float)
    avg_viewers = Column(Integer)
    max_viewers = Column(Integer)
    followers = Column(Integer)
    views = Column(Integer)
    title = Column(String)

    games = relationship("Game", secondary=stream_games, back_populates="streams")


class GameStats(Base):
    __tablename__ = "games_stats"

    game_id = Column(Integer, primary_key=True)
    rank = Column(Integer)
    hours_streamed = Column(Float)
    avg_viewers = Column(Integer)
    max_viewers = Column(Integer)
    followers_per_hour = Column(Float)
    last_stream = Column(DateTime)
