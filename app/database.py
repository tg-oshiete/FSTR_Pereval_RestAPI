from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os
from dotenv import load_dotenv

load_dotenv('.env')

DB_HOST = os.getenv("FSTR_DB_HOST")
DB_PORT = os.getenv("FSTR_DB_PORT")
DB_NAME = os.getenv("FSTR_DB_NAME")
DB_LOGIN = os.getenv("FSTR_DB_LOGIN")
DB_PASS = os.getenv("FSTR_DB_PASS")

DATABASE_URL = f"postgresql://{DB_LOGIN}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
print(DATABASE_URL)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()