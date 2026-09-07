import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, enable_foreign_keys, get_db
from app.main import app
from app.models import GeneratedResponse, Influencer, Post
from scripts.import_posts import import_posts, read_and_validate
from scripts.seed_data import seed_data


HEADERS = [
    "influencer_name", "influencer_linkedin_url", "content", "content_type",
    "post_url", "posted_at", "likes", "comments", "topic", "collected_at",
    "verification_status", "engagement_note", "source_url",
]
PROFILE_URL = "https://www.linkedin.com/in/curated-person"
VALID_ROW = {
    "influencer_name": "Curated Person",
    "influencer_linkedin_url": PROFILE_URL,
    "content": "A researched summary about improving release testing workflows.",
    "content_type": "public_post_summary",
    "post_url": "https://www.linkedin.com/posts/curated-person_release-testing-1",
    "posted_at": "2026-08-28",
    "likes": "87",
    "comments": "",
    "topic": "Release testing",
    "collected_at": "2026-09-01",
    "verification_status": "publicly_verified",
    "engagement_note": "Reaction count was publicly visible.",
    "source_url": "https://www.linkedin.com/posts/curated-person_release-testing-1",
}


class CuratedPostTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="sapho-posts-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.engine = create_engine(
            f"sqlite:///{self.directory / 'test.db'}",
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)
        event.listen(self.engine, "connect", enable_foreign_keys)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with patch("scripts.seed_data.SessionLocal", self.sessions), patch(
            "scripts.seed_data.init_db"
        ):
            seed_data()
        with self.sessions.begin() as db:
            db.add(Influencer(
                id=20,
                name="Curated Person",
                title="Quality Director",
                company="Curated Laboratory",
                linkedin_url=PROFILE_URL + "/",
                relevance_score=95,
                activity_score=90,
                engagement_score=85,
                credibility_score=95,
                overall_score=92,
                notes="Manually researched profile.",
                source_type="manual_research",
                is_sample=False,
            ))

    def write_csv(self, rows):
        path = self.directory / "posts.csv"
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def run_import(self, path):
        with patch("scripts.import_posts.init_db"):
            return import_posts(path, self.sessions)

    def curated_map(self):
        with self.sessions() as db:
            influencer = db.get(Influencer, 20)
            db.expunge(influencer)
            return {PROFILE_URL: influencer}

    def test_valid_import_is_idempotent_and_preserves_nullable_counts(self):
        path = self.write_csv([VALID_ROW])
        first = self.run_import(path)
        second = self.run_import(path)
        self.assertEqual((first.created, first.updated, first.errors), (1, 0, 0))
        self.assertEqual(
            (second.created, second.updated, second.skipped, second.errors),
            (0, 0, 1, 0),
        )
        with self.sessions() as db:
            post = db.scalar(select(Post).where(Post.is_sample.is_(False)))
            self.assertEqual(post.influencer_id, 20)
            self.assertEqual(post.likes, 87)
            self.assertIsNone(post.comments)
            self.assertEqual(post.source_type, "manual_research")
            self.assertFalse(post.is_sample)
            self.assertEqual(post.content_type, "public_post_summary")
            self.assertEqual(db.scalar(select(func.count()).select_from(Post)), 7)
            samples = db.scalars(select(Post).where(Post.is_sample.is_(True))).all()
            self.assertEqual(len(samples), 6)
            self.assertTrue(all(post.source_type == "sample" for post in samples))

    def test_blank_likes_and_comments_are_unknown(self):
        path = self.write_csv([VALID_ROW | {"likes": "", "comments": ""}])
        self.run_import(path)
        with self.sessions() as db:
            post = db.scalar(select(Post).where(Post.is_sample.is_(False)))
            self.assertIsNone(post.likes)
            self.assertIsNone(post.comments)

    def test_validation_rejects_bad_counts_dates_influencers_and_duplicates(self):
        cases = (
            (VALID_ROW | {"likes": "-1"}, "likes must be blank or a non-negative integer"),
            (VALID_ROW | {"comments": "-2"}, "comments must be blank or a non-negative integer"),
            (VALID_ROW | {"posted_at": "not-a-date"}, "posted_at must be a valid ISO date"),
            (VALID_ROW | {"influencer_linkedin_url": "https://www.linkedin.com/in/unknown"}, "unknown influencer LinkedIn URL"),
            (VALID_ROW | {"post_url": "not a url"}, "post_url must be a valid HTTP URL"),
        )
        for row, expected in cases:
            with self.subTest(expected=expected):
                rows, errors = read_and_validate(self.write_csv([row]), self.curated_map())
                self.assertEqual(rows, [])
                self.assertEqual(len(errors), 1)
                self.assertIn("row 2", errors[0])
                self.assertIn(expected, errors[0])

        rows, errors = read_and_validate(
            self.write_csv([VALID_ROW, VALID_ROW]), self.curated_map()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("row 3: duplicate post_url (first seen on row 2)", errors[0])

    def test_changed_metadata_updates_without_erasing_status_or_history(self):
        path = self.write_csv([VALID_ROW])
        self.run_import(path)
        with self.sessions.begin() as db:
            post = db.scalar(select(Post).where(Post.is_sample.is_(False)))
            post.status = "drafted"
            original_id = post.id
            db.add(GeneratedResponse(
                post=post,
                goal="Engagement",
                tone="Professional",
                length="Short",
                generated_text="Original generated response.",
                edited_text="Saved edited response.",
                status="draft",
            ))
        changed = VALID_ROW | {
            "content": "Updated researched summary.",
            "topic": "Updated topic",
            "likes": "101",
            "comments": "12",
            "verification_status": "rechecked",
        }
        self.write_csv([changed])
        summary = self.run_import(path)
        self.assertEqual((summary.created, summary.updated, summary.errors), (0, 1, 0))
        with self.sessions() as db:
            post = db.scalar(select(Post).where(Post.is_sample.is_(False)))
            self.assertEqual(post.id, original_id)
            self.assertEqual(post.status, "drafted")
            self.assertEqual(post.content, "Updated researched summary.")
            self.assertEqual(post.topic, "Updated topic")
            self.assertEqual((post.likes, post.comments), (101, 12))
            self.assertEqual(len(post.generated_responses), 1)
            self.assertEqual(post.generated_responses[0].edited_text, "Saved edited response.")

    def test_queue_detail_and_mocked_generation_use_curated_post(self):
        newer = VALID_ROW | {
            "content": "Newest curated summary with unknown engagement.",
            "post_url": "https://www.linkedin.com/posts/curated-person_newest-2",
            "source_url": "https://www.linkedin.com/posts/curated-person_newest-2",
            "posted_at": "2026-09-02",
            "likes": "",
            "comments": "",
            "topic": "Sterility testing",
        }
        self.run_import(self.write_csv([VALID_ROW, newer]))

        def get_test_db():
            with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        with patch("app.main.init_db"), patch(
            "app.main.generate_llm_response", return_value="Mocked curated response."
        ) as generator, TestClient(app) as client:
            queue = client.get("/")
            self.assertEqual(queue.status_code, 200)
            self.assertEqual(queue.text.count('<article class="post"'), 2)
            self.assertIn("Curated", queue.text)
            self.assertIn("Research summary", queue.text)
            self.assertIn("Newest curated summary", queue.text)
            self.assertNotIn("SAMPLE —", queue.text)
            self.assertNotIn(">0</dd>", queue.text)
            self.assertIn("<dd>—</dd>", queue.text)
            self.assertLess(
                queue.text.index("Newest curated summary"),
                queue.text.index(VALID_ROW["content"]),
            )
            with self.sessions() as db:
                post = db.scalar(select(Post).where(Post.post_url == newer["post_url"]))
                post_id = post.id
            detail = client.get(f"/posts/{post_id}")
            self.assertEqual(detail.status_code, 200)
            self.assertIn("Curated LinkedIn post", detail.text)
            self.assertIn("Research summary", detail.text)
            generated = client.post(
                f"/posts/{post_id}/generate",
                data={
                    "goal": "Engagement",
                    "tone": "Conversational",
                    "length": "Medium",
                    "custom_instruction": "End with a question.",
                },
                follow_redirects=False,
            )
            self.assertEqual(generated.status_code, 303)
            response_id = int(generated.headers["location"].split("response_id=")[1])
            args = generator.call_args.args
            self.assertEqual(inspect(args[0]).identity, (post_id,))
            self.assertEqual(
                args[1:],
                ("Engagement", "Conversational", "Medium", "End with a question."),
            )
            with self.sessions() as db:
                response = db.get(GeneratedResponse, response_id)
                self.assertEqual(response.generated_text, "Mocked curated response.")
                self.assertEqual(response.edited_text, "Mocked curated response.")
            saved = client.post(
                f"/responses/{response_id}/save",
                data={"edited_text": "Edited curated response."},
                follow_redirects=False,
            )
            self.assertEqual(saved.status_code, 303)
            history = client.get(saved.headers["location"])
            self.assertIn("Draft saved.", history.text)
            self.assertIn("Edited curated response.", history.text)
            self.assertIn(f'id="draft-{response_id}"', history.text)

    def test_queue_falls_back_to_demo_posts(self):
        def get_test_db():
            with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        with patch("app.main.init_db"), TestClient(app) as client:
            queue = client.get("/")
            self.assertEqual(queue.status_code, 200)
            self.assertEqual(queue.text.count('<article class="post"'), 6)
            self.assertIn("SAMPLE DATA ONLY.", queue.text)
            self.assertIn("Demo", queue.text)


if __name__ == "__main__":
    unittest.main()
