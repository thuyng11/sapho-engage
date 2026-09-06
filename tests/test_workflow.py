import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, enable_foreign_keys, get_db
from app.main import app
from app.models import GeneratedResponse, Influencer, Post
from app.services.placeholder_generator import generate_placeholder_response
from scripts.seed_data import seed_data


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory(prefix="sapho-tests-")
        self.addCleanup(temporary_directory.cleanup)
        self.engine = create_engine(
            f"sqlite:///{Path(temporary_directory.name) / 'test.db'}",
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)
        event.listen(self.engine, "connect", enable_foreign_keys)
        self.sessions = sessionmaker(bind=self.engine)
        # Start with a seeded Milestone 1 schema to exercise the additive upgrade.
        Base.metadata.create_all(self.engine, tables=[Influencer.__table__, Post.__table__])
        self.seed()
        self.assertNotIn("generated_responses", inspect(self.engine).get_table_names())

        def get_test_db():
            with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = get_test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.enterContext(patch("app.main.init_db", lambda: Base.metadata.create_all(self.engine)))
        self.client = self.enterContext(TestClient(app))
        self.form = {"goal": "Thought Leadership", "tone": "Professional", "length": "Short"}

    def seed(self):
        with patch("scripts.seed_data.SessionLocal", self.sessions), patch("scripts.seed_data.init_db"):
            seed_data()

    def generate(self, post_id=1, **settings):
        response = self.client.post(
            f"/posts/{post_id}/generate", data=self.form | settings, follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)
        location = response.headers["location"]
        page = self.client.get(location)
        self.assertEqual(page.status_code, 200)
        return int(location.split("response_id=")[1]), page

    def test_upgrade_preserves_queue_and_seed(self):
        self.assertIn("generated_responses", inspect(self.engine).get_table_names())
        self.seed()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Influencer)), 3)
            self.assertEqual(db.scalar(select(func.count()).select_from(Post)), 6)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.text.count('<article class="post"'), 6)
        self.assertEqual(page.text.count('>Generate Response</a>'), 6)
        self.assertLess(page.text.index('id="post-6"'), page.text.index('id="post-1"'))
        self.assertIn('<dd>0</dd>', page.text)
        self.assertIn('SAMPLE DATA ONLY.', page.text)
        self.assertEqual(self.client.get("/static/styles.css").status_code, 200)

    def test_detail_information_and_defaults(self):
        page = self.client.get("/posts/1")
        self.assertEqual(page.status_code, 200)
        self.assertIn('No drafts yet.', page.text)
        for value in self.form.values():
            self.assertIn(f'value="{value}" selected', page.text)
        with self.sessions() as db:
            post = db.get(Post, 1)
            for value in (post.influencer.name, post.influencer.title, post.influencer.company,
                          post.content, post.post_url, post.posted_at.strftime('%b %d, %Y at %H:%M')):
                self.assertIn(value, page.text)
            self.assertIn(f'<dd>{post.likes}</dd>', page.text)
            self.assertIn(f'<dd>{post.comments}</dd>', page.text)

    def test_generate_is_deterministic_and_persists_configuration(self):
        response_id, page = self.generate(
            goal="Relationship Building", tone="Technical", length="Detailed",
            custom_instruction="  Mention sample tracking.  ",
        )
        self.assertIn('[Demo response — Relationship Building / Technical / Detailed]', page.text)
        with self.sessions() as db:
            response = db.get(GeneratedResponse, response_id)
            self.assertEqual(response.edited_text, response.generated_text)
            self.assertEqual(response.status, "draft")
            self.assertEqual(response.post.status, "new")
            self.assertEqual(response.custom_instruction, "Mention sample tracking.")
            self.assertIsNotNone(response.created_at)
            self.assertIsNotNone(response.updated_at)
            expected = generate_placeholder_response(
                response.post, response.goal, response.tone, response.length, response.custom_instruction
            )
            self.assertEqual(response.generated_text, expected)
        self.client.get(f"/posts/1?response_id={response_id}")
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(GeneratedResponse)), 1)

    def test_save_persists_edit_and_keeps_original(self):
        response_id, _ = self.generate()
        with self.sessions() as db:
            response = db.get(GeneratedResponse, response_id)
            original = response.generated_text
            previous_updated_at = response.updated_at
        edited = 'My edited draft.\n<script>alert("test")</script>'
        result = self.client.post(
            f"/responses/{response_id}/save", data={"edited_text": edited}, follow_redirects=False
        )
        self.assertEqual(result.status_code, 303)
        page = self.client.get(result.headers["location"])
        self.assertIn("Draft saved.", page.text)
        self.assertIn("&lt;script&gt;", page.text)
        self.assertNotIn('<script>', page.text)
        self.seed()
        with self.sessions() as db:
            response = db.get(GeneratedResponse, response_id)
            self.assertEqual(response.edited_text, edited)
            self.assertEqual(response.generated_text, original)
            self.assertEqual(response.status, "draft")
            self.assertEqual(response.post.status, "drafted")
            self.assertGreater(response.updated_at, previous_updated_at)
        self.assertIn('<dd>drafted</dd>', self.client.get('/').text)
        self.assertIn('My edited draft.', self.client.get('/posts/1').text)

    def test_save_preserves_other_post_statuses(self):
        response_id, _ = self.generate(post_id=3)
        self.assertEqual(self.client.post(
            f"/responses/{response_id}/save", data={"edited_text": "Reviewed post draft."}
        ).status_code, 200)
        with self.sessions() as db:
            self.assertEqual(db.get(Post, 3).status, "reviewed")

    def test_draft_history_order_and_post_isolation(self):
        first_id, _ = self.generate()
        second_id, page = self.generate(goal="Engagement", tone="Conversational", length="Medium")
        self.assertLess(page.text.index(f'id="draft-{second_id}"'), page.text.index(f'id="draft-{first_id}"'))
        older = self.client.get(f"/posts/1?response_id={first_id}")
        self.assertIn(f'/responses/{first_id}/save', older.text)
        self.assertEqual(self.client.get(f"/posts/2?response_id={first_id}").status_code, 404)
        self.assertIn('No drafts yet.', self.client.get('/posts/2').text)

    def test_invalid_ids(self):
        for value, status in ((9999, 404), (-1, 404), ('invalid', 422)):
            with self.subTest(value=value):
                self.assertEqual(self.client.get(f'/posts/{value}').status_code, status)
                self.assertEqual(self.client.post(f'/posts/{value}/generate', data=self.form).status_code, status)
                self.assertEqual(self.client.post(f'/responses/{value}/save', data={'edited_text': 'Draft'}).status_code, status)
        self.assertEqual(self.client.get('/posts/1?response_id=9999').status_code, 404)

    def test_invalid_forms_do_not_write(self):
        for field in self.form:
            missing = {key: value for key, value in self.form.items() if key != field}
            for form in (missing, self.form | {field: 'invalid'}, self.form | {field: ''}):
                self.assertEqual(self.client.post('/posts/1/generate', data=form).status_code, 422)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(GeneratedResponse)), 0)
        response_id, _ = self.generate()
        for form in ({}, {'edited_text': ''}, {'edited_text': ' \n\t '}):
            self.assertEqual(self.client.post(f'/responses/{response_id}/save', data=form).status_code, 422)
        with self.sessions() as db:
            response = db.get(GeneratedResponse, response_id)
            self.assertEqual(response.edited_text, response.generated_text)
            self.assertEqual(response.post.status, 'new')
            self.assertIsNone(response.custom_instruction)


if __name__ == "__main__":
    unittest.main()
