from database.db import engine, Base
import database.models  # важно!

Base.metadata.create_all(bind=engine)

print("DB created")
