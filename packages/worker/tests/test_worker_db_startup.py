"""Use fresh processes so import order matches API and installed worker startup."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entrypoint,configured,expected", [
    ("wuwa_story.db.session", {}, [5, 10]),
    ("wuwa_story_worker.cli", {}, [4, 4]),
    ("wuwa_story_worker.cli", {"WUWA_WORKER_DB_POOL_SIZE": "6", "WUWA_WORKER_DB_MAX_OVERFLOW": "2"}, [6, 2]),
    ("wuwa_story.db.session", {"WUWA_WORKER_DB_POOL_SIZE": "3"}, [3, 4]),
])
def test_engine_is_constructed_with_api_or_worker_limits(entrypoint, configured, expected):
    environment = {key: value for key, value in os.environ.items() if key not in {
        "WUWA_WORKER_DB_POOL_SIZE", "WUWA_WORKER_DB_MAX_OVERFLOW"}}
    environment.update(configured)
    source = f"""
import importlib, json
importlib.import_module({entrypoint!r})
from wuwa_story.db.session import engine
pool = engine.sync_engine.pool
print(json.dumps([pool.size(), pool._max_overflow]))
"""
    result = subprocess.run([sys.executable, "-c", source], env=environment,
                            capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
