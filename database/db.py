import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

db_path = os.path.join(BASE_DIR, "storage", "streams.db")

os.makedirs(os.path.join(BASE_DIR, "storage"), exist_ok=True)

DATABASE_URL = f"sqlite:///{db_path}"

engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()
