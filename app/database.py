import os
from collections.abc import Generator
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, event, inspect
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
    from app import models  # Register all models before creating missing tables.

    Base.metadata.create_all(bind=engine)
    upgrade_influencer_table()


def upgrade_influencer_table(database_engine=engine) -> None:
    """Add Milestone 4 fields to an existing prototype SQLite database."""
    if database_engine.dialect.name != "sqlite" or not inspect(database_engine).has_table("influencers"):
        return
    existing = {column["name"] for column in inspect(database_engine).get_columns("influencers")}
    additions = {
        "activity_score": "FLOAT NOT NULL DEFAULT 0",
        "engagement_score": "FLOAT NOT NULL DEFAULT 0",
        "credibility_score": "FLOAT NOT NULL DEFAULT 0",
        "overall_score": "FLOAT NOT NULL DEFAULT 0",
        "source_type": "VARCHAR(50) NOT NULL DEFAULT 'sample'",
        "is_sample": "BOOLEAN NOT NULL DEFAULT 1",
    }
    with database_engine.begin() as connection:
        for name, definition in additions.items():
            if name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE influencers ADD COLUMN {name} {definition}"
                )


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
