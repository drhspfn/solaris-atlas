import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wuwa_story.db.migration_safety import validate_online_storage_verification


def migration():
    source = Path(__file__).parents[1] / "migrations/versions/0029_storage_verification_invalidation.py"
    spec = importlib.util.spec_from_file_location("storage_verification_migration", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_and_downgrade_preserve_existing_fences():
    module = migration()
    original = "BEGIN isolation_and_ordering_fences; " + module.ANCHOR + " original_invalidation; END;"
    execute = Mock()
    bind = SimpleNamespace(scalar=Mock(return_value=original))
    module.op = SimpleNamespace(get_bind=lambda: bind, execute=execute)
    module.upgrade()
    updated = execute.call_args.args[0]
    assert updated == original.replace(module.ANCHOR, module.VERIFICATION_FILTER + module.ANCHOR)
    bind.scalar.return_value = updated
    module.downgrade()
    assert execute.call_args.args[0] == original


@pytest.mark.parametrize("definition", ["missing", "duplicate duplicate", "already_filtered"])
def test_unexpected_function_aborts_before_replacement(definition):
    module = migration()
    definition = definition.replace("duplicate", module.ANCHOR)
    if definition == "already_filtered":
        definition = module.VERIFICATION_FILTER + module.ANCHOR
    execute = Mock()
    module.op = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(scalar=Mock(return_value=definition)), execute=execute,
    )
    with pytest.raises(RuntimeError, match="migration aborted"):
        module.upgrade()
    assert execute.call_count == 1  # Transaction-local lock timeout only.


def test_online_guard_accepts_only_the_exact_compatible_step():
    validate_online_storage_verification("0029_storage_verification_noop", ("0028_agent_reader_role",))


@pytest.mark.parametrize(("target", "current"), [
    ("head", ("0028_agent_reader_role",)),
    ("0028_agent_reader_role", ("0028_agent_reader_role",)),
    ("0029_storage_verification_noop", ()),
    ("0029_storage_verification_noop", ("0027_agent_workspace",)),
    ("0029_storage_verification_noop", ("0029_storage_verification_noop",)),
    ("0029_storage_verification_noop", ("0028_agent_reader_role", "another_head")),
])
def test_online_guard_rejects_other_migrations(target, current):
    with pytest.raises(RuntimeError, match="exactly revision 0028 -> 0029"):
        validate_online_storage_verification(target, current)
