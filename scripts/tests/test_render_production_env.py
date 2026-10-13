import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TelegramEnvironmentTests(unittest.TestCase):
    def test_bot_credentials_are_optional_and_written_only_to_worker_config(self):
        script = Path(__file__).resolve().parents[1] / "render-production-env.py"
        for token in ("", "fake-bot-token"):
            with self.subTest(configured=bool(token)), tempfile.TemporaryDirectory() as directory:
                environment = {**os.environ, "POSTGRES_PASSWORD": "fake-password", "RABBITMQ_DEFAULT_PASS": "fake-password",
                               "AUTH_CSRF_SECRET": "fake-secret", "MINIO_ROOT_PASSWORD": "fake-password", "USE_MINIO": "true",
                               "TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": "-1004484145903", "TELEGRAM_ERROR_THREAD_ID": "2"}
                result = subprocess.run([sys.executable, str(script), directory], env=environment, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                worker = (Path(directory) / "worker.env").read_text()
                self.assertIn("TELEGRAM_ERROR_THREAD_ID=2", worker)
                self.assertEqual("TELEGRAM_BOT_TOKEN=" in worker, bool(token))
                for name in ("api.env", "shared.env", "compose.env"):
                    self.assertNotIn("TELEGRAM_BOT_TOKEN", (Path(directory) / name).read_text())
