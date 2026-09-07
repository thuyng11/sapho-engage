import os
from collections.abc import Generator
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def resolve_database_path(configured_path: str | None = None) -> Path:
    value = configured_path if configured_path is not None else os.getenv("DATABASE_PATH")
    path = Path(value.strip() if value and value.strip() else "sapho.db").expanduser()
    return path if path.is_absolute() else BASE_DIR / path


database_path = resolve_database_path()

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

    database_path.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    upgrade_influencer_table()
    upgrade_post_table()


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


def upgrade_post_table(database_engine=engine) -> None:
    """Add curated-post fields and make engagement counts nullable in SQLite."""
    if database_engine.dialect.name != "sqlite" or not inspect(database_engine).has_table("posts"):
        return
    columns = {column["name"]: column for column in inspect(database_engine).get_columns("posts")}
    requires_rebuild = not columns["likes"]["nullable"] or not columns["comments"]["nullable"]
    additions = {
        "topic": "VARCHAR(200) NOT NULL DEFAULT ''",
        "content_type": "VARCHAR(50) NOT NULL DEFAULT 'sample'",
        "collected_at": "DATETIME",
        "source_type": "VARCHAR(50) NOT NULL DEFAULT 'sample'",
        "is_sample": "BOOLEAN NOT NULL DEFAULT 1",
        "verification_status": "VARCHAR(50) NOT NULL DEFAULT 'sample'",
        "source_url": "VARCHAR(500)",
        "engagement_note": "TEXT",
    }
    if requires_rebuild:
        with database_engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            try:
                with connection.begin():
                    connection.exec_driver_sql("""
                        CREATE TABLE posts_new (
                            id INTEGER NOT NULL PRIMARY KEY,
                            influencer_id INTEGER NOT NULL REFERENCES influencers(id),
                            content TEXT NOT NULL,
                            post_url VARCHAR(500) NOT NULL,
                            posted_at DATETIME NOT NULL,
                            likes INTEGER,
                            comments INTEGER,
                            status VARCHAR(50) NOT NULL DEFAULT 'new',
                            topic VARCHAR(200) NOT NULL DEFAULT '',
                            content_type VARCHAR(50) NOT NULL DEFAULT 'sample',
                            collected_at DATETIME,
                            source_type VARCHAR(50) NOT NULL DEFAULT 'sample',
                            is_sample BOOLEAN NOT NULL DEFAULT 1,
                            verification_status VARCHAR(50) NOT NULL DEFAULT 'sample',
                            source_url VARCHAR(500),
                            engagement_note TEXT
                        )
                    """)
                    connection.exec_driver_sql("""
                        INSERT INTO posts_new (
                            id, influencer_id, content, post_url, posted_at,
                            likes, comments, status
                        )
                        SELECT id, influencer_id, content, post_url, posted_at,
                               likes, comments, status
                        FROM posts
                    """)
                    connection.exec_driver_sql("DROP TABLE posts")
                    connection.exec_driver_sql("ALTER TABLE posts_new RENAME TO posts")
                    connection.exec_driver_sql(
                        "CREATE INDEX ix_posts_influencer_id ON posts (influencer_id)"
                    )
            finally:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            if violations:
                raise RuntimeError("Post table upgrade produced foreign key violations")
        return

    existing = set(columns)
    with database_engine.begin() as connection:
        for name, definition in additions.items():
            if name not in existing:
                connection.exec_driver_sql(f"ALTER TABLE posts ADD COLUMN {name} {definition}")


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
