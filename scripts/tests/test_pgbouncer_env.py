"""Verify deployment derives separate API, migration and worker connection settings."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


class PgBouncerEnvironmentTests(unittest.TestCase):
    def test_only_api_uses_transaction_pooling_and_credentials_are_url_encoded(self):
        source = (ROOT / "scripts/deploy-remote.sh").read_text()
        renderer = source.split("python3 <<'PY'\n", 1)[1].split("\nPY", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "compose.env").write_text(
                'DOMAIN=example.org\nUSE_MINIO=true\nPOSTGRES_USER=wuwa\n'
                'POSTGRES_PASSWORD=fake:p@ss$word\nRABBITMQ_DEFAULT_USER=wuwa\n'
                'RABBITMQ_DEFAULT_PASS=fake\nMINIO_ROOT_USER=wuwa\nMINIO_ROOT_PASSWORD=fake\n'
            )
            for name in ("shared.env", "api.env", "worker.env"):
                (path / name).write_text("")
            result = subprocess.run(
                [sys.executable, "-c", renderer],
                env={**os.environ, "ENV_DIR": directory, "GHCR_OWNER": "test", "IMAGE_TAG": "test", "DEPLOY_COMPONENT": "api"},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            def read(name):
                return dict(line.split("=", 1) for line in (path / name).read_text().splitlines())

            api, worker = read("api.runtime.env"), read("runtime.env")
            self.assertEqual(urlsplit(api["DATABASE_URL"]).hostname, "pgbouncer")
            self.assertEqual(urlsplit(api["DATABASE_URL"]).port, 6432)
            self.assertEqual(api["MIGRATION_DATABASE_URL"], worker["DATABASE_URL"])
            self.assertEqual(urlsplit(worker["DATABASE_URL"]).hostname, "postgres")
            self.assertEqual(api["WUWA_DB_TRANSACTION_POOLING"], "1")
            self.assertNotIn("WUWA_DB_TRANSACTION_POOLING", worker)
            self.assertIn("fake%3Ap%40ss%24word", api["DATABASE_URL"])
