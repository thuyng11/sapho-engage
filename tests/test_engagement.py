import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, enable_foreign_keys, get_db
from app.main import app
from app.models import ActivityLog, GeneratedResponse, Influencer, Post
from app.services.llm_service import GenerationError
from scripts.seed_data import seed_data


class EngagementWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="sapho-engagement-")
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(
            f"sqlite:///{Path(directory.name) / 'test.db'}",
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

        def get_test_db():
            with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.enterContext(patch("app.main.init_db"))
        self.generator = self.enterContext(
            patch("app.main.generate_llm_response", return_value="Generated response one.")
        )
        self.sdk = self.enterContext(
            patch(
                "app.services.llm_service.genai.Client",
                side_effect=AssertionError("Real Gemini calls are forbidden in tests"),
            )
        )
        self.client = self.enterContext(TestClient(app))
        self.form = {
            "goal": "Thought Leadership",
            "tone": "Professional",
            "length": "Short",
            "custom_instruction": "Focus on quality.",
        }

    def generate(self, post_id=1):
        result = self.client.post(
            f"/posts/{post_id}/generate", data=self.form, follow_redirects=False
        )
        self.assertEqual(result.status_code, 303)
        return int(result.headers["location"].split("response_id=")[1])

    def test_regenerate_creates_history_and_logs_events(self):
        first_id = self.generate()
        self.generator.return_value = "Generated response two."
        result = self.client.post(
            f"/responses/{first_id}/regenerate", follow_redirects=False
        )
        self.assertEqual(result.status_code, 303)
        second_id = int(result.headers["location"].split("response_id=")[1].split("&")[0])
        self.assertNotEqual(first_id, second_id)
        page = self.client.get(result.headers["location"])
        self.assertIn("Response regenerated", page.text)
        self.assertIn(f'id="draft-{first_id}"', page.text)
        self.assertIn(f'id="draft-{second_id}"', page.text)
        self.assertLess(
            page.text.index(f'id="draft-{second_id}"'),
            page.text.index(f'id="draft-{first_id}"'),
        )
        with self.sessions() as db:
            responses = db.scalars(
                select(GeneratedResponse).order_by(GeneratedResponse.id)
            ).all()
            self.assertEqual([item.generated_text for item in responses], [
                "Generated response one.", "Generated response two."
            ])
            self.assertEqual(
                db.scalars(select(ActivityLog.action).order_by(ActivityLog.id)).all(),
                ["generated", "regenerated"],
            )
        args = self.generator.call_args.args
        self.assertEqual(
            args[1:],
            ("Thought Leadership", "Professional", "Short", "Focus on quality."),
        )

    def test_failed_regeneration_preserves_existing_response(self):
        response_id = self.generate()
        self.generator.side_effect = GenerationError("Provider failure")
        result = self.client.post(f"/responses/{response_id}/regenerate")
        self.assertEqual(result.status_code, 503)
        self.assertIn("Unable to generate a response right now.", result.text)
        self.assertIn(f'id="draft-{response_id}"', result.text)
        with self.sessions() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(GeneratedResponse)), 1
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ActivityLog)), 1
            )

    def test_approval_editing_and_posted_lifecycle(self):
        response_id = self.generate()
        saved = self.client.post(
            f"/responses/{response_id}/save",
            data={"edited_text": "First saved edit."},
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 303)
        approved = self.client.post(
            f"/responses/{response_id}/approve",
            data={"edited_text": "Approved current edit."},
            follow_redirects=False,
        )
        self.assertEqual(approved.status_code, 303)
        self.assertIn("approved=true", approved.headers["location"])
        with self.sessions() as db:
            response = db.get(GeneratedResponse, response_id)
            self.assertEqual(response.edited_text, "Approved current edit.")
            self.assertEqual(response.status, "approved")
            self.assertEqual(response.post.status, "approved")

        changed = self.client.post(
            f"/responses/{response_id}/save",
            data={"edited_text": "Changed after approval."},
            follow_redirects=False,
        )
        self.assertEqual(changed.status_code, 303)
        with self.sessions() as db:
            response = db.get(GeneratedResponse, response_id)
            self.assertEqual(response.status, "draft")
            self.assertEqual(response.post.status, "drafted")

        denied = self.client.post(f"/responses/{response_id}/posted")
        self.assertEqual(denied.status_code, 409)
        self.client.post(
            f"/responses/{response_id}/approve",
            data={"edited_text": "Final approved response."},
        )
        posted = self.client.post(
            f"/responses/{response_id}/posted", follow_redirects=False
        )
        self.assertEqual(posted.status_code, 303)
        page = self.client.get(posted.headers["location"])
        self.assertIn("Marked as posted", page.text)
        self.assertIn("Recorded manually", page.text)
        self.assertIn("readonly", page.text)
        with self.sessions() as db:
            response = db.get(GeneratedResponse, response_id)
            self.assertEqual(response.status, "posted")
            self.assertEqual(response.post.status, "posted")
            actions = db.scalars(
                select(ActivityLog.action).order_by(ActivityLog.id)
            ).all()
            self.assertEqual(actions, [
                "generated", "draft_saved", "approved", "draft_saved",
                "approved", "marked_posted",
            ])

    def test_activity_page_is_newest_first(self):
        response_id = self.generate()
        self.client.post(
            f"/responses/{response_id}/approve",
            data={"edited_text": "Approved response."},
        )
        self.client.post(f"/responses/{response_id}/posted")
        page = self.client.get("/activity")
        self.assertEqual(page.status_code, 200)
        self.assertLess(
            page.text.index("Marked as posted"),
            page.text.index("Response approved"),
        )
        self.assertLess(
            page.text.index("Response approved"),
            page.text.index("Response generated"),
        )
        self.assertIn("SAMPLE — Dr. Avery Chen", page.text)

    def test_copy_uses_current_text_without_changing_status(self):
        response_id = self.generate()
        page = self.client.get(f"/posts/1?response_id={response_id}")
        self.assertIn("Copy Response", page.text)
        script = self.client.get("/static/app.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("navigator.clipboard.writeText(textarea.value)", script.text)
        with self.sessions() as db:
            self.assertEqual(db.get(GeneratedResponse, response_id).status, "draft")


class QueueFilterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="sapho-filters-")
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(
            f"sqlite:///{Path(directory.name) / 'test.db'}",
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)
        event.listen(self.engine, "connect", enable_foreign_keys)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions.begin() as db:
            first = Influencer(
                id=10, name="First Curated", title="Director", company="Lab One",
                linkedin_url="https://www.linkedin.com/in/first", relevance_score=90,
                notes="Research", source_type="manual_research", is_sample=False,
            )
            second = Influencer(
                id=11, name="Second Curated", title="Manager", company="Lab Two",
                linkedin_url="https://www.linkedin.com/in/second", relevance_score=85,
                notes="Research", source_type="manual_research", is_sample=False,
            )
            db.add_all([first, second])
            db.add_all([
                Post(
                    id=100, influencer=first, content="First quality summary",
                    post_url="https://www.linkedin.com/posts/first-1",
                    posted_at=datetime(2026, 9, 3), likes=None, comments=None,
                    status="new", topic="Quality", content_type="public_post_summary",
                    source_type="manual_research", is_sample=False,
                ),
                Post(
                    id=101, influencer=first, content="First sterility summary",
                    post_url="https://www.linkedin.com/posts/first-2",
                    posted_at=datetime(2026, 9, 2), likes=2, comments=1,
                    status="approved", topic="Sterility", content_type="public_post_summary",
                    source_type="manual_research", is_sample=False,
                ),
                Post(
                    id=102, influencer=second, content="Second quality summary",
                    post_url="https://www.linkedin.com/posts/second-1",
                    posted_at=datetime(2026, 9, 1), likes=4, comments=2,
                    status="posted", topic="Quality", content_type="public_post_summary",
                    source_type="manual_research", is_sample=False,
                ),
            ])

        def get_test_db():
            with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.enterContext(patch("app.main.init_db"))
        self.client = self.enterContext(TestClient(app))

    def assert_cards(self, params, expected):
        page = self.client.get("/", params=params)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.text.count('<article class="post"'), expected)
        return page

    def test_individual_and_combined_filters(self):
        influencer = self.assert_cards({"influencer": "10"}, 2)
        self.assertIn("First Curated", influencer.text)
        self.assertNotIn("Second quality summary", influencer.text)
        topic = self.assert_cards({"topic": "Quality"}, 2)
        self.assertNotIn("First sterility summary", topic.text)
        status = self.assert_cards({"status": "posted"}, 1)
        self.assertIn("Second quality summary", status.text)
        combined = self.assert_cards(
            {"influencer": "10", "topic": "Sterility", "status": "approved"}, 1
        )
        self.assertIn("First sterility summary", combined.text)
        self.assertNotIn("Second quality summary", combined.text)

    def test_invalid_filters_are_ignored_with_feedback(self):
        page = self.assert_cards(
            {"influencer": "not-an-id", "topic": "Unknown", "status": "invalid"}, 3
        )
        self.assertIn("Unknown influencer filter.", page.text)
        self.assertIn("Unknown topic filter.", page.text)
        self.assertIn("Unknown status filter.", page.text)


if __name__ == "__main__":
    unittest.main()
