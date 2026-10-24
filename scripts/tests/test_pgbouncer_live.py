"""Opt-in local Docker smoke: RUN_PGBOUNCER_SMOKE=1 server-python -m unittest discover

Uses disposable containers, fake credentials and loopback-only ephemeral ports.
Requires the server Python dependencies (SQLAlchemy and asyncpg).
"""

import asyncio
import os
import re
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.getenv("RUN_PGBOUNCER_SMOKE") == "1", "opt-in local Docker smoke")
class PgBouncerLiveTests(unittest.TestCase):
    def test_scram_prepared_statements_transactions_and_direct_session_locks(self):
        from sqlalchemy import text
        from sqlalchemy.pool import NullPool

        sys.path.insert(0, str(ROOT / "packages/server/src"))
        prefix = "solaris-pgbouncer-smoke-" + uuid4().hex[:10]
        network, postgres, pooler = prefix, prefix + "-postgres", prefix + "-pooler"

        def docker(*args):
            return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=90).stdout.strip()

        # Refuse a remote daemon: production deployment belongs to the parent task.
        endpoint = docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
        self.assertTrue(endpoint.startswith(("npipe:", "unix:")), "Smoke requires a local Docker daemon")
        self.assertFalse(os.getenv("DOCKER_HOST"), "Unset DOCKER_HOST for local smoke")
        image = re.search(r"image: (pgbouncer/pgbouncer:\S+)", (ROOT / "infrastructure/prod/compose.yml").read_text())[1]
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / "userlist.txt"
            try:
                docker("network", "create", network)
                docker("run", "-d", "--name", postgres, "--network", network, "--network-alias", "postgres",
                       "-e", "POSTGRES_USER=wuwa", "-e", "POSTGRES_PASSWORD=smoke-only-password",
                       "-e", "POSTGRES_DB=wuwa_story", "-p", "127.0.0.1::5432", "pgvector/pgvector:pg17")
                for _ in range(60):
                    ready = subprocess.run(["docker", "exec", postgres, "pg_isready", "-U", "wuwa"], capture_output=True, check=False)
                    if ready.returncode == 0:
                        break
                    time.sleep(0.5)
                else:
                    self.fail("Disposable PostgreSQL did not become ready")
                verifier = subprocess.run(
                    ["docker", "exec", "-i", postgres, "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "wuwa", "-d", "wuwa_story"],
                    input=(ROOT / "infrastructure/prod/pgbouncer/auth.sql").read_text(),
                    check=True, capture_output=True, text=True, timeout=10,
                ).stdout
                self.assertTrue(verifier.startswith('"wuwa" "SCRAM-SHA-256$'))
                auth.write_text(verifier)
                auth.chmod(0o644)  # Synthetic verifier only; let container UID 70 read it on Linux.
                docker("run", "-d", "--name", pooler, "--network", network, "--read-only", "--tmpfs", "/tmp",
                       "-p", "127.0.0.1::6432",
                       "-v", f"{ROOT / 'infrastructure/prod/pgbouncer/pgbouncer.ini'}:/etc/pgbouncer/pgbouncer.ini:ro",
                       "-v", f"{auth}:/etc/pgbouncer/userlist.txt:ro",
                       "--entrypoint", "/opt/pgbouncer/pgbouncer", image, "/etc/pgbouncer/pgbouncer.ini")
                for _ in range(30):
                    ready = subprocess.run(["docker", "exec", pooler, "nc", "-z", "127.0.0.1", "6432"], capture_output=True, check=False)
                    if ready.returncode == 0:
                        break
                    time.sleep(0.2)
                else:
                    self.fail("Disposable PgBouncer did not become ready")
                pooled_port = docker("port", pooler, "6432/tcp").rsplit(":", 1)[1]
                direct_port = docker("port", postgres, "5432/tcp").rsplit(":", 1)[1]

                def engine(port, pooled):
                    url = f"postgresql+asyncpg://wuwa:smoke-only-password@127.0.0.1:{port}/wuwa_story"
                    with patch.dict(os.environ, {"WUWA_DB_TRANSACTION_POOLING": "1" if pooled else "0",
                                                 "WUWA_WORKER_DB_POOL_SIZE": "4", "WUWA_WORKER_DB_MAX_OVERFLOW": "4"}), \
                         patch("wuwa_story.config.settings.get_settings", return_value=SimpleNamespace(database_url=url)):
                        return runpy.run_path(str(ROOT / "packages/server/src/wuwa_story/db/session.py"))["engine"]

                async def verify():
                    api, worker = engine(pooled_port, True), engine(direct_port, False)
                    try:
                        self.assertIsInstance(api.pool, NullPool)
                        self.assertEqual(worker.pool.size(), 4)

                        async def query(value):
                            async with api.connect() as connection:
                                for _ in range(3):
                                    row = (await connection.execute(text("SELECT CAST(:value AS integer) AS value, pg_backend_pid() AS pid, pg_sleep(0.02)"), {"value": value})).one()
                                    self.assertEqual(row.value, value)
                                    await connection.commit()
                                return row.pid

                        # More simultaneous API clients than both the old 5+10 pool and backend cap.
                        results = await asyncio.wait_for(asyncio.gather(*(query(i) for i in range(60))), 30)
                        self.assertLessEqual(len(set(results)), 25)
                        async with api.begin() as connection:
                            await connection.execute(text("CREATE TABLE pooler_smoke (value integer)"))
                        async with api.connect() as connection:
                            await connection.execute(text("INSERT INTO pooler_smoke VALUES (1)"))
                            await connection.rollback()
                            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM pooler_smoke")), 0)
                            await connection.commit()
                            self.assertTrue(await connection.scalar(text("SELECT pg_try_advisory_xact_lock(918273)")))
                            await connection.commit()
                            async with worker.begin() as direct:
                                self.assertTrue(await direct.scalar(text("SELECT pg_try_advisory_xact_lock(918273)")))
                        # Worker session lock remains held across commits until explicit unlock.
                        async with worker.connect() as owner, worker.connect() as competitor:
                            self.assertTrue(await owner.scalar(text("SELECT pg_try_advisory_lock(817263)")))
                            await owner.commit()
                            self.assertFalse(await competitor.scalar(text("SELECT pg_try_advisory_lock(817263)")))
                            await competitor.commit()
                            self.assertTrue(await owner.scalar(text("SELECT pg_advisory_unlock(817263)")))
                            await owner.commit()
                            self.assertTrue(await competitor.scalar(text("SELECT pg_try_advisory_lock(817263)")))
                            self.assertTrue(await competitor.scalar(text("SELECT pg_advisory_unlock(817263)")))
                    finally:
                        await api.dispose()
                        await worker.dispose()

                asyncio.run(verify())
            finally:
                for container in (pooler, postgres):
                    subprocess.run(["docker", "rm", "-fv", container], capture_output=True, check=False)
                subprocess.run(["docker", "network", "rm", network], capture_output=True, check=False)
