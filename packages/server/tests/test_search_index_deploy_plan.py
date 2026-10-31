"""Ensure only the designated revision can run with clients connected."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from wuwa_story.db.migration_safety import validate_online_search_index

SERVER = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("search_index_deploy_plan", SERVER / "scripts/search_index_deploy_plan.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    "heads,expected",
    [([], "before:more"), (["0021_authored_tree_order"], "before:more"),
     (["0022_tree_index_schema_version"], "ready:more"), (["0023_search_body_trgm"], "done:more"),
     (["0024_browse_read_models"], "done:more"),
     (["0025_event_asset_dependencies"], "done:last")],
)
def test_plan_existing_migration_history(heads, expected):
    scripts = ScriptDirectory.from_config(Config(str(SERVER / "alembic.ini")))
    assert module.plan(scripts, heads) == expected


def test_rejects_unknown_and_multiple_database_heads():
    scripts = ScriptDirectory.from_config(Config(str(SERVER / "alembic.ini")))
    for heads in (["unknown"], [module.PREVIOUS, module.ONLINE]):
        with pytest.raises(ValueError):
            module.plan(scripts, heads)
    with pytest.raises(ValueError, match="Unversioned database"):
        module.plan(scripts, [], document_exists=True)


def test_future_revision_requires_a_separate_stopped_phase():
    revisions = [
        SimpleNamespace(revision="0024_future", down_revision=module.ONLINE, dependencies=None),
        SimpleNamespace(revision=module.ONLINE, down_revision=module.PREVIOUS, dependencies=None),
        SimpleNamespace(revision=module.PREVIOUS, down_revision=None, dependencies=None),
    ]
    scripts = SimpleNamespace(get_heads=lambda: ["0024_future"], walk_revisions=lambda: revisions)
    assert module.plan(scripts, [module.PREVIOUS]) == "ready:more"
    assert module.plan(scripts, [module.ONLINE]) == "done:more"
    assert module.plan(scripts, ["0024_future"]) == "done:last"
    revisions[1].down_revision = (module.PREVIOUS, "other_branch")
    with pytest.raises(ValueError):
        module.plan(scripts, [module.PREVIOUS])


def test_online_guard_only_accepts_exact_transition():
    validate_online_search_index(module.ONLINE, (module.PREVIOUS,))
    for target, current in [
        ("0024_future", (module.PREVIOUS,)),
        (module.ONLINE, ("0021_authored_tree_order",)),
        (module.ONLINE, (module.ONLINE,)),
        (module.ONLINE, (module.PREVIOUS, "other")),
    ]:
        with pytest.raises(RuntimeError):
            validate_online_search_index(target, current)
