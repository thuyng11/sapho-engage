import os
from collections.abc import Generator
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

database_path = Path(os.getenv("DATABASE_PATH", "sapho.db")).expanduser()
if not database_path.is_absolute():
    database_path = BASE_DIR / database_path

engine = create_engine(
    URL.create("sqlite", database=str(database_path)),
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine)


@event.listens_for(engine, "connect")
def enable_foreign_keys(connection, connection_record):
    # SQLite requires foreign key enforcement on each connection.
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app import models  # Register both tables before creating them.

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
