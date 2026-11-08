from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.admin.composition import install_admin
from wuwa_story.config.settings import Settings


def test_archive_models_are_registered_and_read_only():
    engine = create_async_engine(
        "postgresql+asyncpg://user:password@localhost/test")
    admin = install_admin(FastAPI(), engine, Settings(
        _env_file=None, app_env="test"))
    views = {view.identity: view for group in admin._views for view in group.views}
    for identity in ("cutscenes", "characters", "npcs", "items", "speakers", "speaker-links",
                     "nodes", "edges", "edge-evidence", "node-revisions", "relation-types"):
        assert identity in views
        assert views[identity].row_actions == ["view"]
