import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, enable_foreign_keys, get_db
from app.main import app
from app.models import GeneratedResponse, Influencer, Post
from app.services.placeholder_generator import generate_placeholder_response
from app.services.llm_service import GenerationError, generate_response as real_generate_response
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
        self.llm = self.enterContext(patch("app.main.generate_llm_response", return_value="A useful industry perspective."))
        self.sdk = self.enterContext(patch("app.services.llm_service.genai.Client", side_effect=AssertionError("Real SDK calls are forbidden in workflow tests")))

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
        self.assertIn('No previous responses for this post.', page.text)
        for value in self.form.values():
            self.assertIn(f'value="{value}" selected', page.text)
        with self.sessions() as db:
            post = db.get(Post, 1)
            for value in (post.influencer.name, post.influencer.title, post.influencer.company,
                          post.content, post.post_url, post.posted_at.strftime('%b %d, %Y at %H:%M')):
                self.assertIn(value, page.text)
            self.assertIn(f'<dd>{post.likes}</dd>', page.text)
            self.assertIn(f'<dd>{post.comments}</dd>', page.text)

    def test_mocked_generation_persists_output_and_configuration(self):
        response_id, page = self.generate(
            goal="Relationship Building", tone="Technical", length="Detailed",
            custom_instruction="  Mention sample tracking.  ",
        )
        self.assertIn(self.llm.return_value, page.text)
        args = self.llm.call_args.args
        self.assertEqual(inspect(args[0]).identity, (1,))
        self.assertEqual(args[1:], ("Relationship Building", "Technical", "Detailed", "Mention sample tracking."))
        with self.sessions() as db:
            response = db.get(GeneratedResponse, response_id)
            self.assertEqual(response.edited_text, response.generated_text)
            self.assertEqual(response.status, "draft")
            self.assertEqual(response.post.status, "drafted")
            self.assertEqual(response.custom_instruction, "Mention sample tracking.")
            self.assertIsNotNone(response.created_at)
            self.assertIsNotNone(response.updated_at)
            self.assertEqual(response.generated_text, self.llm.return_value)
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
        self.assertIn('status-drafted">Drafted</span>', self.client.get('/').text)
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
        self.assertIn('No previous responses for this post.', self.client.get('/posts/2').text)

    def test_invalid_ids(self):
        for value, status in ((9999, 404), (-1, 404), ('invalid', 422)):
            with self.subTest(value=value):
                self.assertEqual(self.client.get(f'/posts/{value}').status_code, status)
                self.assertEqual(self.client.post(f'/posts/{value}/generate', data=self.form).status_code, status)
                self.assertEqual(self.client.post(f'/responses/{value}/save', data={'edited_text': 'Draft'}).status_code, status)
        self.assertEqual(self.client.get('/posts/1?response_id=9999').status_code, 404)
        self.llm.assert_not_called()

    def test_invalid_forms_do_not_write(self):
        for field in self.form:
            missing = {key: value for key, value in self.form.items() if key != field}
            for form in (missing, self.form | {field: 'invalid'}, self.form | {field: ''}):
                self.assertEqual(self.client.post('/posts/1/generate', data=form).status_code, 422)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(GeneratedResponse)), 0)
        self.llm.assert_not_called()
        response_id, _ = self.generate()
        for form in ({}, {'edited_text': ''}, {'edited_text': ' \n\t '}):
            self.assertEqual(self.client.post(f'/responses/{response_id}/save', data=form).status_code, 422)
        with self.sessions() as db:
            response = db.get(GeneratedResponse, response_id)
            self.assertEqual(response.edited_text, response.generated_text)
            self.assertEqual(response.post.status, 'drafted')
            self.assertIsNone(response.custom_instruction)

    def test_generation_failure_preserves_form_and_existing_drafts(self):
        response_id, _ = self.generate()
        self.llm.side_effect = GenerationError("Sensitive provider details must not be shown")
        form = {
            "goal": "Lead Generation", "tone": "Educational", "length": "Medium",
            "custom_instruction": "  Focus on turnaround time. <script>test</script>  ",
        }
        result = self.client.post('/posts/1/generate', data=form)
        self.assertEqual(result.status_code, 503)
        self.assertIn("Unable to generate a response right now.", result.text)
        self.assertNotIn("Sensitive provider details", result.text)
        for field in ("goal", "tone", "length"):
            self.assertIn(f'value="{form[field]}" selected', result.text)
        self.assertIn("  Focus on turnaround time. &lt;script&gt;test&lt;/script&gt;  ", result.text)
        self.assertIn(f'id="draft-{response_id}"', result.text)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(GeneratedResponse)), 1)
            self.assertEqual(db.get(Post, 1).status, "drafted")
        self.llm.side_effect = None
        self.generate(goal="Lead Generation", tone="Educational", length="Medium")

    def test_missing_configuration_shows_error_without_writing(self):
        self.llm.side_effect = real_generate_response
        for config in ({"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "test-only", "GEMINI_MODEL": " "}):
            with self.subTest(configured_model="GEMINI_MODEL" in config), patch.dict(os.environ, config):
                result = self.client.post('/posts/1/generate', data=self.form)
                self.assertEqual(result.status_code, 503)
                self.assertIn("Please check the API configuration and try again.", result.text)
        self.sdk.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(GeneratedResponse)), 0)

    def test_placeholder_utility_remains_deterministic(self):
        with self.sessions() as db:
            post = db.get(Post, 1)
            args = (post, "Thought Leadership", "Professional", "Short", None)
            self.assertEqual(generate_placeholder_response(*args), generate_placeholder_response(*args))
            self.assertIn("[Demo response", generate_placeholder_response(*args))
        self.llm.assert_not_called()

    def test_service_timeout_and_empty_output_do_not_create_drafts(self):
        self.llm.side_effect = real_generate_response
        self.sdk.side_effect = None
        client = self.sdk.return_value.__enter__.return_value
        request = httpx.Request("POST", "https://generativelanguage.googleapis.com/")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only", "GEMINI_MODEL": "gemini-3.7-flash"}):
            for error in (httpx.ReadTimeout("Timed out", request=request), None):
                client.models.generate_content.side_effect = error
                client.models.generate_content.return_value = SimpleNamespace(candidates=[SimpleNamespace(finish_reason="STOP")], text=" \n ")
                result = self.client.post('/posts/1/generate', data=self.form)
                self.assertEqual(result.status_code, 503)
                self.assertIn("Unable to generate a response right now.", result.text)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(GeneratedResponse)), 0)


if __name__ == "__main__":
    unittest.main()
