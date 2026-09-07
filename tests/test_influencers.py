import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.database import (
    Base,
    enable_foreign_keys,
    get_db,
    upgrade_influencer_table,
    upgrade_post_table,
)
from app.main import app
from app.models import GeneratedResponse, Influencer, Post
from app.services.influencer_scoring import calculate_overall_score
from scripts.import_influencers import import_influencers, read_and_validate
from scripts.seed_data import seed_data


HEADERS = [
    "name", "title", "company", "linkedin_url", "relevance_score",
    "activity_score", "engagement_score", "credibility_score", "notes",
]
VALID_ROW = {
    "name": "Research Person", "title": "Director", "company": "Research Lab",
    "linkedin_url": "https://www.linkedin.com/in/research-person",
    "relevance_score": "90", "activity_score": "80", "engagement_score": "70",
    "credibility_score": "100", "notes": "Public research snapshot.",
}


class ScoringTests(unittest.TestCase):
    def test_formula_and_rounding(self):
        self.assertEqual(calculate_overall_score(99, 96, 82, 99), 95.0)
        self.assertEqual(calculate_overall_score(91, 82, 73, 64), 80.2)

    def test_scores_outside_range_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "relevance_score must be between"):
            calculate_overall_score(-0.1, 50, 50, 50)
        with self.assertRaisesRegex(ValueError, "activity_score must be between"):
            calculate_overall_score(50, 100.1, 50, 50)


class InfluencerImportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="sapho-influencers-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.engine = create_engine(f"sqlite:///{self.directory / 'test.db'}")
        self.addCleanup(self.engine.dispose)
        event.listen(self.engine, "connect", enable_foreign_keys)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with patch("scripts.seed_data.SessionLocal", self.sessions), patch("scripts.seed_data.init_db"):
            seed_data()

    def write_csv(self, rows):
        path = self.directory / "influencers.csv"
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def run_import(self, path):
        with patch("scripts.import_influencers.init_db"):
            return import_influencers(path, self.sessions)

    def test_import_is_idempotent_and_updates_by_linkedin_url(self):
        path = self.write_csv([VALID_ROW])
        first = self.run_import(path)
        with self.sessions.begin() as db:
            curated = db.scalar(select(Influencer).where(Influencer.is_sample.is_(False)))
            db.add(Post(
                influencer=curated, content="Existing researched post", post_url="https://example.com/post",
                posted_at=datetime(2026, 1, 1), likes=0, comments=0,
            ))
        second = self.run_import(path)
        self.assertEqual((first.created, first.updated, first.errors), (1, 0, 0))
        self.assertEqual((second.created, second.updated, second.skipped, second.errors), (0, 0, 1, 0))
        changed = VALID_ROW | {"name": "Updated Research Person", "activity_score": "90"}
        self.write_csv([changed])
        third = self.run_import(path)
        self.assertEqual((third.created, third.updated), (0, 1))
        with self.sessions() as db:
            curated = db.scalar(select(Influencer).where(Influencer.is_sample.is_(False)))
            self.assertEqual(curated.name, "Updated Research Person")
            self.assertEqual(curated.activity_score, 90)
            self.assertEqual(curated.overall_score, 88.0)
            self.assertEqual(curated.source_type, "manual_research")
            self.assertFalse(curated.is_sample)
            self.assertEqual([post.content for post in curated.posts], ["Existing researched post"])
            self.assertEqual(db.scalar(select(func.count()).select_from(Influencer)), 4)
            samples = db.scalars(select(Influencer).where(Influencer.is_sample.is_(True))).all()
            self.assertEqual(len(samples), 3)
            self.assertTrue(all(item.source_type == "sample" for item in samples))

    def test_validation_errors_prevent_partial_import(self):
        invalid_rows = [
            VALID_ROW,
            VALID_ROW | {"name": "", "linkedin_url": "https://www.linkedin.com/in/blank-name"},
            VALID_ROW | {"linkedin_url": "https://www.linkedin.com/in/too-high", "activity_score": "101"},
            VALID_ROW | {"linkedin_url": "https://www.linkedin.com/in/not-numeric", "engagement_score": "many"},
            VALID_ROW | {"name": "Duplicate", "linkedin_url": VALID_ROW["linkedin_url"] + "/"},
        ]
        path = self.write_csv(invalid_rows)
        rows, errors = read_and_validate(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 4)
        self.assertTrue(any("name is required" in error for error in errors))
        self.assertTrue(any("must be between 0 and 100" in error for error in errors))
        self.assertTrue(any("must be numeric" in error for error in errors))
        self.assertTrue(any("duplicate linkedin_url" in error for error in errors))
        summary = self.run_import(path)
        self.assertGreater(summary.errors, 0)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Influencer)), 4)

    def test_curated_page_excludes_samples_and_sorts_descending(self):
        rows = [
            VALID_ROW | {"name": "Lower Score", "linkedin_url": "https://www.linkedin.com/in/lower", "relevance_score": "50"},
            VALID_ROW | {"name": "Higher Score", "linkedin_url": "https://www.linkedin.com/in/higher", "relevance_score": "100"},
        ]
        self.run_import(self.write_csv(rows))

        def get_test_db():
            with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        with patch("app.main.init_db", lambda: None), TestClient(app) as client:
            page = client.get("/influencers")
            self.assertEqual(page.status_code, 200)
            self.assertNotIn("SAMPLE —", page.text)
            self.assertLess(page.text.index("Higher Score"), page.text.index("Lower Score"))
            self.assertIn("Curated", page.text)
            self.assertIn("not an official LinkedIn metric", page.text)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/posts/1").status_code, 200)


class DatabaseUpgradeTests(unittest.TestCase):
    def test_existing_database_rows_and_related_history_survive_upgrade(self):
        directory = tempfile.TemporaryDirectory(prefix="sapho-upgrade-")
        self.addCleanup(directory.cleanup)
        engine = create_engine(f"sqlite:///{Path(directory.name) / 'old.db'}")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE influencers (
                    id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL,
                    title VARCHAR(200) NOT NULL, company VARCHAR(200) NOT NULL,
                    linkedin_url VARCHAR(500) NOT NULL, relevance_score FLOAT NOT NULL,
                    notes TEXT NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE posts (
                    id INTEGER PRIMARY KEY, influencer_id INTEGER NOT NULL REFERENCES influencers(id),
                    content TEXT NOT NULL, post_url VARCHAR(500) NOT NULL,
                    posted_at DATETIME NOT NULL, likes INTEGER NOT NULL,
                    comments INTEGER NOT NULL, status VARCHAR(50) DEFAULT 'new' NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE generated_responses (
                    id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL REFERENCES posts(id),
                    goal VARCHAR(50) NOT NULL, tone VARCHAR(50) NOT NULL,
                    length VARCHAR(50) NOT NULL, custom_instruction TEXT,
                    generated_text TEXT NOT NULL, edited_text TEXT,
                    status VARCHAR(50) NOT NULL, created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """)
            connection.exec_driver_sql("INSERT INTO influencers VALUES (1, 'SAMPLE Old', 'Title', 'Company', 'https://example.com', 0.9, 'Notes')")
            connection.exec_driver_sql("INSERT INTO posts VALUES (1, 1, 'Post', 'https://example.com/post', '2026-01-01', 1, 1, 'drafted')")
            connection.exec_driver_sql("INSERT INTO generated_responses VALUES (1, 1, 'Engagement', 'Professional', 'Short', NULL, 'Original', 'Edited', 'draft', '2026-01-01', '2026-01-02')")
        upgrade_influencer_table(engine)
        upgrade_post_table(engine)
        columns = {column["name"] for column in inspect(engine).get_columns("influencers")}
        self.assertTrue({"activity_score", "engagement_score", "credibility_score", "overall_score", "source_type", "is_sample"} <= columns)
        post_columns = {
            column["name"]: column for column in inspect(engine).get_columns("posts")
        }
        self.assertTrue({
            "topic", "content_type", "collected_at", "source_type", "is_sample",
            "verification_status", "source_url", "engagement_note",
        } <= set(post_columns))
        self.assertTrue(post_columns["likes"]["nullable"])
        self.assertTrue(post_columns["comments"]["nullable"])
        with engine.connect() as connection:
            sample = connection.execute(text("SELECT source_type, is_sample FROM influencers WHERE id=1")).one()
            self.assertEqual(sample, ("sample", 1))
            self.assertEqual(connection.scalar(text("SELECT COUNT(*) FROM posts")), 1)
            self.assertEqual(connection.scalar(text("SELECT COUNT(*) FROM generated_responses")), 1)
            post = connection.execute(text(
                "SELECT status, source_type, is_sample FROM posts WHERE id=1"
            )).one()
            self.assertEqual(post, ("drafted", "sample", 1))


if __name__ == "__main__":
    unittest.main()
