import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect

from app.database import BASE_DIR, resolve_database_path


class DatabaseConfigurationTests(unittest.TestCase):
    def test_default_and_overridden_database_paths(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_database_path(), BASE_DIR / "sapho.db")
        absolute = Path("/data/sapho.db")
        self.assertEqual(resolve_database_path(str(absolute)), absolute)
        self.assertEqual(
            resolve_database_path("var/deployment.db"),
            BASE_DIR / "var/deployment.db",
        )

    def test_init_db_creates_schema_and_missing_parent_for_override(self):
        with tempfile.TemporaryDirectory(prefix="sapho-deploy-init-") as directory:
            database_path = Path(directory) / "mounted" / "sapho.db"
            environment = os.environ.copy()
            environment["DATABASE_PATH"] = str(database_path)
            subprocess.run(
                [sys.executable, "-c", "from app.database import init_db; init_db()"],
                cwd=BASE_DIR,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertTrue(database_path.is_file())
            database_engine = create_engine(f"sqlite:///{database_path}")
            self.addCleanup(database_engine.dispose)
            self.assertEqual(
                set(inspect(database_engine).get_table_names()),
                {"activity_logs", "generated_responses", "influencers", "posts"},
            )


if __name__ == "__main__":
    unittest.main()
