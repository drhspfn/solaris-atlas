import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    'illustrated_backfill', Path(__file__).parents[1] / 'backfill-illustrated-sequences.py')
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def sample():
    client = {'download_id': 'x', 'asset_version': '3.7.1', 'tier': 'hd'}
    row = {'reference_id': 10, 'release_id': 21, 'owner_node_id': 8,
           'owner': 'flow_state:fox', 'composition_reason': True,
           'source_path': 'story_sequence:fox', 'file_id': 12,
           'reference_type': 'story_presentation', 'bundle_available': True}
    target = {'owner': 8, 'path': row['source_path'], 'reference_type': row['reference_type'],
              'sequence_metadata_revision': 5, 'presentation_sha256': 'a' * 64, 'spine_recipe': 4}
    source = {**client, 'sequence_metadata_revision': 4, 'presentation_sha256': 'a' * 64,
              'spine_recipe': 4, 'source_files': [{'package_path': 'asset', 'sha256': 'a' * 64}],
              'control_sources_complete': True}
    ref = SimpleNamespace(id=10, release_id=21, owner_node_id=8, source_path=row['source_path'],
                          file_id=12, reference_type=row['reference_type'],
                          metadata_json={'game_source': source})
    return client, row, target, ref


def test_audit_scope_deduplicates_and_rejects_unreviewed_entity():
    _, row, _, _ = sample()
    assert script.audit_scope({'references': [row, row]}, 21) == {'flow_state:fox': [row, row]}
    with pytest.raises(ValueError):
        script.audit_scope({'references': [row]}, 21, 'flow_state:other')


def test_checkpoint_skips_submitted_held_and_uncertain_dispatches():
    scope = {'a': [], 'b': [], 'c': [], 'd': []}
    entries = {'a': {'state': 'submitted'}, 'b': {'state': 'dispatching'}, 'c': {'state': 'held'}}
    assert script.select_pending(scope, entries, 1) == (['d'], 0)


def test_requires_same_client_and_revision_four_control_complete():
    client, row, target, ref = sample()
    assert script.check_sources([row], [ref], client, [target]) == (None, 'retained_revision_4', [10])
    assert script.check_sources([row], [ref], client, [target], {'WUWA_FMODEL_PATH': 'x'})[0] == 'extraction_tool_mismatch'
    assert script.check_sources([row], [ref], {**client, 'download_id': 'other'}, [target])[0] == 'workspace_source_mismatch'
    ref.metadata_json['game_source']['control_sources_complete'] = False
    assert script.check_sources([row], [ref], client, [target])[0] == 'retained_control_sources_incomplete'
    ref.metadata_json['game_source']['control_sources_complete'] = True
    assert script.check_sources([row], [ref], client, [{**target, 'sequence_metadata_revision': None}])[0] == 'target_has_no_revision_5_invalidation'
    assert script.check_sources([row], [], client, [target])[0] == 'audit_reference_changed'


def test_legacy_spine_sequence_requires_full_source():
    client, row, target, ref = sample()
    row['reference_type'] = ref.reference_type = target['reference_type'] = 'story_spine'
    ref.metadata_json['game_source'].update(sequence_metadata_revision=None, presentation_sha256=None, spine_recipe=1)
    row['bundle_available'] = False
    assert script.check_sources([row], [ref], client, [target]) == (None, 'full_source_required', [])
    target['sequence_metadata_revision'] = None
    assert script.check_sources([row], [ref], client, [target])[0] == 'target_has_no_revision_5_invalidation'


def test_fresh_client_requires_explicit_distinct_source_and_never_reuses_bundle():
    client, row, target, ref = sample()
    ref.metadata_json['game_source'].update(download_id='a' * 64, asset_version='3.7.0')
    replacement = {**client, 'download_id': 'b' * 64}
    assert script.check_sources([row], [ref], replacement, [target])[0] == 'workspace_source_mismatch'
    assert script.check_sources([row], [ref], replacement, [target], fresh_client=True) == (
        None, 'full_source_required', [])
    ref.metadata_json['game_source']['presentation_sha256'] = 'c' * 64
    assert script.check_sources([row], [ref], replacement, [target], fresh_client=True)[0] == 'presentation_source_mismatch'
    ref.metadata_json['game_source'].update(download_id='b' * 64, asset_version='3.7.1')
    assert script.check_sources([row], [ref], replacement, [target], fresh_client=True)[0] == 'fresh_client_requires_distinct_audited_source'
    assert script.check_sources([row], [], replacement, [target], fresh_client=True)[0] == 'audit_reference_changed'


@pytest.mark.parametrize('status', ['pending', 'queued', 'running', 'waiting_dependency',
                                     'completed', 'partial', 'failed', 'enqueue_failed', 'cancelled'])
def test_never_republishes_an_existing_run(status):
    prior = SimpleNamespace(id=1, status=status)
    action, found = script.existing_run_action([prior] if status in script.ACTIVE else [], prior)
    assert found is prior
    assert action == ('hold_active_revision' if status in script.ACTIVE else 'hold_existing_run')
