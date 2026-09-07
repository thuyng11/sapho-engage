import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import BASE_DIR, Base, get_db
from app.main import app
from app.models import ActivityLog, GeneratedResponse, Influencer, Post


class DeploymentTests(unittest.TestCase):
    def run_python(self, arguments: list[str], database_path: Path) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        environment["DATABASE_PATH"] = str(database_path)
        environment["GEMINI_API_KEY"] = ""
        return subprocess.run(
            [sys.executable, *arguments],
            cwd=BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_import_bootstrap_is_idempotent_and_preserves_workflow_data(self):
        with tempfile.TemporaryDirectory(prefix="sapho-deploy-import-") as directory:
            database_path = Path(directory) / "data" / "sapho.db"
            first_influencers = self.run_python(
                ["-m", "scripts.import_influencers"], database_path
            )
            first_posts = self.run_python(["-m", "scripts.import_posts"], database_path)
            self.assertIn("Created: 10", first_influencers.stdout)
            self.assertIn("Created: 29", first_posts.stdout)

            database_engine = create_engine(f"sqlite:///{database_path}")
            self.addCleanup(database_engine.dispose)
            sessions = sessionmaker(bind=database_engine)
            with sessions.begin() as session:
                post = session.scalar(
                    select(Post).where(Post.source_type == "manual_research")
                )
                self.assertIsNotNone(post)
                post.status = "approved"
                response = GeneratedResponse(
                    post=post,
                    goal="Thought Leadership",
                    tone="Professional",
                    length="Short",
                    generated_text="Persisted response.",
                    edited_text="Persisted edit.",
                    status="approved",
                )
                session.add(response)
                session.add(ActivityLog(post=post, response=response, action="approved"))
                post_id = post.id

            second_influencers = self.run_python(
                ["-m", "scripts.import_influencers"], database_path
            )
            second_posts = self.run_python(["-m", "scripts.import_posts"], database_path)
            self.assertIn("Created: 0", second_influencers.stdout)
            self.assertIn("Skipped: 10", second_influencers.stdout)
            self.assertIn("Created: 0", second_posts.stdout)
            self.assertIn("Skipped: 29", second_posts.stdout)

            with sessions() as session:
                self.assertEqual(
                    session.scalar(
                        select(func.count()).select_from(Influencer).where(
                            Influencer.source_type == "manual_research"
                        )
                    ),
                    10,
                )
                self.assertEqual(
                    session.scalar(
                        select(func.count()).select_from(Post).where(
                            Post.source_type == "manual_research"
                        )
                    ),
                    29,
                )
                self.assertEqual(session.get(Post, post_id).status, "approved")
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(GeneratedResponse)), 1
                )
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(ActivityLog)), 1
                )

    def test_health_and_browsing_start_without_gemini_key(self):
        with tempfile.TemporaryDirectory(prefix="sapho-deploy-web-") as directory:
            database_engine = create_engine(
                f"sqlite:///{Path(directory) / 'empty.db'}",
                connect_args={"check_same_thread": False},
            )
            self.addCleanup(database_engine.dispose)
            Base.metadata.create_all(database_engine)
            sessions = sessionmaker(bind=database_engine)

            def get_test_db():
                with sessions() as session:
                    yield session

            app.dependency_overrides[get_db] = get_test_db
            self.addCleanup(app.dependency_overrides.clear)
            with patch("app.main.init_db"), patch.dict(
                os.environ, {"GEMINI_API_KEY": ""}
            ), patch(
                "app.services.llm_service.genai.Client",
                side_effect=AssertionError("Browsing must not call Gemini"),
            ), TestClient(app) as client:
                health = client.get("/health")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json(), {"status": "ok"})
                for path in ("/", "/influencers", "/activity"):
                    self.assertEqual(client.get(path).status_code, 200)

    def test_production_startup_configuration(self):
        script = (BASE_DIR / "scripts/start_production.sh").read_text()
        self.assertIn("python -m scripts.import_influencers", script)
        self.assertIn("python -m scripts.import_posts", script)
        self.assertIn("exec python -m uvicorn app.main:app --host 0.0.0.0", script)
        self.assertIn('${PORT:-8000}', script)

        config = (BASE_DIR / "railway.toml").read_text()
        self.assertIn('startCommand = "sh scripts/start_production.sh"', config)
        self.assertIn('healthcheckPath = "/health"', config)


if __name__ == "__main__":
    unittest.main()
