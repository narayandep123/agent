"""SQLite database setup (SQLAlchemy 2.x).

A single local SQLite file persists user accounts (and their enrollment status).
The path can be overridden with the ``CAMPUSFLOW_DB`` environment variable so the
test-suite can point at a throwaway database.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "campusflow.db")
DB_PATH = os.getenv("CAMPUSFLOW_DB", _DEFAULT_DB)
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    """FastAPI dependency yielding a request-scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
