from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from wuwa_story.ingestion.entity_media import MediaRequest
from wuwa_story.ingestion.media_provenance import missing_media_targets


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', ['WUWA_VGMSTREAM_PATH', 'WUWA_WWISER_PATH'])
@pytest.mark.parametrize('changed', [False, True])
async def test_sequence_audio_revision3_reuse_checks_decoder_and_renderer(tool, changed):
    value = {**target('story_sequence:flow_state:Story'), 'kind': 'spine',
             'reference_type': 'story_presentation', 'spine_recipe': 4, 'sequence_metadata_revision': 3}
    stored = {'WUWA_VGMSTREAM_PATH': 'decoder', 'WUWA_WWISER_PATH': 'renderer'}
    reference, file, location = row(value, spine_recipe=4, sequence_metadata_revision=3, tools=stored)
    reference.reference_type = 'story_presentation'
    session = AsyncMock()
    session.execute.return_value = [(reference, file, location)]
    storage = SimpleNamespace(backend='s3', bucket='media', stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    current = {**stored, **({tool: 'changed'} if changed else {})}
    result = await missing_media_targets(session, [value], source={'asset_version': '3.7.0', 'tools': current}, storage=storage)
    assert result == ([value] if changed else [])


@pytest.mark.parametrize(('path', 'revision', 'kind', 'tool'), [
    ('story_sequence:flow_state:Story', 2, 'spine', 'WUWA_WWISER_PATH'),
    ('spine_background:7', 3, 'spine', 'WUWA_WWISER_PATH'),
    ('/Game/Icon', None, 'image', 'WUWA_VGMSTREAM_PATH'),
])
def test_sequence_audio_tool_gate_preserves_other_targets(path, revision, kind, tool):
    from wuwa_story.ingestion.media_provenance import matches_source

    value = {**target(path), 'kind': kind, 'spine_recipe': 4, 'sequence_metadata_revision': revision}
    reference, _, _ = row(value, spine_recipe=4, sequence_metadata_revision=revision, tools={tool: 'old'})
    assert matches_source(reference.metadata_json, {'tools': {tool: 'new'}}, value)


@pytest.mark.parametrize(('revision', 'matches'), [(None, False), (1, False), (2, True)])
def test_scoped_sequence_metadata_revision_requires_recompile_but_not_global_spine_upgrade(revision, matches):
    from wuwa_story.ingestion.media_provenance import matches_source

    value = {**target('story_sequence:flow_state:Story'), 'kind': 'spine',
             'reference_type': 'story_presentation', 'spine_recipe': 4,
             'sequence_metadata_revision': 2}
    reference, _, _ = row(value, spine_recipe=4, sequence_metadata_revision=revision)
    source = {'asset_version': '3.7.0', 'download_id': 'a' * 64, 'tier': 'hd', 'tools': {}}
    # Derive the source exactly as this test helper constructs it.
    source = {key: reference.metadata_json['game_source'][key] for key in ('asset_version', 'download_id')}
    assert matches_source(reference.metadata_json, source, value) is matches
    value['path'] = 'spine_background:7'
    reference.metadata_json['game_source']['package_path'] = value['path']
    assert matches_source(reference.metadata_json, source, value)
    value['sequence_metadata_revision'] = 5
    assert not matches_source(reference.metadata_json, source, value)


@pytest.mark.asyncio
@pytest.mark.parametrize('recipe,missing_part,playable,missing', [(0, False, True, True), (1, True, True, True), (1, False, False, True), (1, False, True, False)])
async def test_spine_reuse_verifies_recipe_and_every_open_component(recipe, missing_part, playable, missing):
    value = {**target('spine_background:7'), 'kind': 'spine', 'reference_type': 'story_spine', 'spine_recipe': 1}
    reference, file, location = row(value, spine_recipe=recipe)
    reference.reference_type = 'story_spine'
    reference.metadata_json['playback'] = {'variants': [{}] if playable else [], 'unsupported': [],
                                          'components': [{'object_key': 'objects/scene', 'size_bytes': 3}]}
    async def stat(key):
        if key == 'objects/scene':
            return None if missing_part else SimpleNamespace(size_bytes=3)
        return SimpleNamespace(size_bytes=12)
    session = AsyncMock()
    session.execute.return_value = [(reference, file, location)]
    storage = SimpleNamespace(backend='s3', bucket='media', stat=AsyncMock(side_effect=stat))
    result = await missing_media_targets(session, [value], source={'asset_version': '3.7.0'}, storage=storage, require_spine_playback=True)
    assert result == ([value] if missing else [])


@pytest.mark.asyncio
async def test_archive_only_spine_reuse_needs_no_browser_components():
    value = {**target('spine_background:7'), 'kind': 'spine', 'reference_type': 'story_spine', 'spine_recipe': 1}
    reference, file, location = row(value, spine_recipe=1)
    reference.reference_type = 'story_spine'
    session = AsyncMock()
    session.execute.return_value = [(reference, file, location)]
    storage = SimpleNamespace(backend='s3', bucket='media', stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    assert await missing_media_targets(session, [value], source={'asset_version': '3.7.0'}, storage=storage) == []
    storage.stat.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('state,reasons,missing', [
    ('ready', [], False),
    ('unsupported', [{'code': 'plan_unsupported'}], False),
    ('failed', [{'code': 'render_failed'}], True),
    ('unsupported', [{'code': 'renderer_disabled'}], True),
])
async def test_sequence_stills_are_reusable_but_render_failures_remain_retryable(state, reasons, missing):
    value = {**target('story_sequence:flow_state:Story'), 'kind': 'spine',
             'reference_type': 'story_presentation', 'spine_recipe': 1}
    reference, file, location = row(value, spine_recipe=1)
    reference.reference_type = 'story_presentation'
    reference.metadata_json['sequence_media'] = {
        'composition': {'state': state, 'reasons': reasons}, 'components': [],
    }
    session = AsyncMock()
    session.execute.return_value = [(reference, file, location)]
    storage = SimpleNamespace(backend='s3', bucket='media', stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    result = await missing_media_targets(session, [value], source={'asset_version': '3.7.0'},
                                         storage=storage, require_spine_playback=True)
    assert result == ([value] if missing else [])


@pytest.mark.asyncio
@pytest.mark.parametrize('require_playback', [False, True])
@pytest.mark.parametrize('fragment_size', [None, 2, 3])
async def test_sequence_fragments_are_verified_even_without_open_playback(require_playback, fragment_size):
    value = {**target('story_sequence:flow_state:Story'), 'kind': 'spine',
             'reference_type': 'story_presentation', 'spine_recipe': 1}
    reference, file, location = row(value, spine_recipe=1)
    reference.reference_type = 'story_presentation'
    part = {'object_key': 'objects/fragment', 'size_bytes': 3}
    reference.metadata_json['sequence_media'] = {'complete_video': False, 'components': [part, part]}
    reference.metadata_json['playback'] = {'variants': [{}], 'components': [
        {'object_key': 'objects/scene', 'size_bytes': 4}], 'unsupported': []}

    async def stat(key):
        size = fragment_size if key == 'objects/fragment' else 4 if key == 'objects/scene' else 12
        return SimpleNamespace(size_bytes=size) if size is not None else None

    session = AsyncMock()
    session.execute.return_value = [(reference, file, location)]
    storage = SimpleNamespace(backend='s3', bucket='media', stat=AsyncMock(side_effect=stat))
    result = await missing_media_targets(session, [value], source={'asset_version': '3.7.0'},
                                         storage=storage, require_spine_playback=require_playback)
    assert result == ([] if fragment_size == 3 else [value])
    assert [call.args[0] for call in storage.stat.await_args_list].count('objects/fragment') == 1
    assert ('objects/scene' in [call.args[0] for call in storage.stat.await_args_list]) == require_playback
    # A missing child must not mark the still-valid parent ZIP unavailable.
    assert location.available is True


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_part', [False, True])
async def test_native_preview_reuse_verifies_every_rendered_video(missing_part):
    value = {**target('spine_background:7'), 'kind': 'spine', 'reference_type': 'story_spine', 'spine_recipe': 1}
    reference, file, location = row(value, spine_recipe=1)
    reference.reference_type = 'story_spine'
    reference.metadata_json['animation_preview'] = {'recipe': 'riglyph-native-v1', 'status': 'ready',
        'variants': [{'object_key': 'objects/video', 'size_bytes': 3}]}

    async def stat(key):
        if key == 'objects/video':
            return None if missing_part else SimpleNamespace(size_bytes=3)
        return SimpleNamespace(size_bytes=12)

    session = AsyncMock()
    session.execute.return_value = [(reference, file, location)]
    storage = SimpleNamespace(backend='s3', bucket='media', stat=AsyncMock(side_effect=stat))
    result = await missing_media_targets(session, [value], source={'asset_version': '3.7.0'},
                                          storage=storage, require_spine_playback=True)
    assert result == ([value] if missing_part else [])


@pytest.mark.asyncio
@pytest.mark.parametrize('archived_hash', [None, 'old', 'new'])
async def test_spine_reuse_requires_the_same_authored_control_source(archived_hash):
    value = {**target('spine_background:7'), 'kind': 'spine', 'reference_type': 'story_spine',
             'spine_recipe': 2, 'presentation_sha256': 'new'}
    reference, file, location = row(
        value, spine_recipe=2, presentation_sha256=archived_hash)
    reference.reference_type = 'story_spine'
    session = AsyncMock()
    session.execute.return_value = [(reference, file, location)]
    storage = SimpleNamespace(backend='s3', bucket='media', stat=AsyncMock(
        return_value=SimpleNamespace(size_bytes=12)))
    result = await missing_media_targets(session, [value], source={'asset_version': '3.7.0'}, storage=storage)
    assert result == ([] if archived_hash == 'new' else [value])


def test_incomplete_controller_archive_remains_retryable():
    from wuwa_story.ingestion.media_provenance import matches_source
    value = {**target('spine_background:7'), 'kind': 'spine', 'spine_recipe': 2,
             'presentation_sha256': 'same'}
    reference, _, _ = row(value, spine_recipe=2, presentation_sha256='same', control_sources_complete=False)
    assert not matches_source(reference.metadata_json, {'asset_version': '3.7.0'}, value)


@pytest.mark.parametrize('archived_version', [None, 0, 1])
def test_sequence_media_upgrade_revisits_only_outdated_controller_archives(archived_version):
    from wuwa_story.ingestion.media_provenance import matches_source
    value = {**target('story_sequence:flow_state:Story'), 'kind': 'spine',
             'spine_recipe': 2, 'presentation_sha256': 'same', 'sequence_media_schema': 1}
    reference, _, _ = row(value, spine_recipe=2, presentation_sha256='same',
                          sequence_media_schema=archived_version)
    assert matches_source(reference.metadata_json, {'asset_version': '3.7.0'}, value) == (archived_version == 1)
    # Non-sequence Spine and character trials retain their existing recipe.
    value.pop('sequence_media_schema')
    assert matches_source(reference.metadata_json, {'asset_version': '3.7.0'}, value)


def target(path="/Game/Icon.Icon", language=None):
    return {"owner": 4, "release_id": 2, "path": path,
            "language": language, "kind": "voice" if language else "image"}


def row(value, **source):
    reference = SimpleNamespace(
        owner_node_id=value["owner"], release_id=value["release_id"],
        source_path=value["path"], source_name=value["language"],
        file_id=10,
        reference_type="character_voice" if value["language"] else "entity_image",
        metadata_json={"game_source": {"schema_version": 1,
            "package_path": value["path"].split(".")[0],
            "source_files": [{"package_path": "Client/Content/Icon.uasset", "sha256": "a" * 64}],
            "asset_version": "3.7.0", "download_id": "a" * 64, **source}},
    )
    return reference, SimpleNamespace(size_bytes=12), SimpleNamespace(object_key="objects/icon")


@pytest.mark.asyncio
@pytest.mark.parametrize("change,exists,missing", [
    ({}, 12, False), ({}, None, True), ({}, 10, True),
    ({"asset_version": "3.6.0"}, 12, True),
    ({"download_id": "b" * 64}, 12, True),
    ({"schema_version": 0}, 12, True), ({"source_files": []}, 12, True),
])
async def test_skip_requires_matching_provenance_and_live_blob(change, exists, missing):
    value = target()
    session = AsyncMock()
    session.execute.return_value = [row(value, **change)]
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(
        return_value=None if exists is None else SimpleNamespace(size_bytes=exists)))
    result = await missing_media_targets(session, [value],
        source={"asset_version": "3.7.0", "download_id": "a" * 64}, storage=storage)
    assert result == ([value] if missing else [])
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_languages_and_shared_blobs_are_batched():
    values = [target(language=language) for language in ("en", "ja", "ko", "zh")]
    session = AsyncMock()
    session.execute.return_value = [row(value) for value in values[:2]]
    storage = SimpleNamespace(backend="s3", bucket="media",
                             stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    assert await missing_media_targets(session, values,
        source={"asset_version": "3.7.0"}, storage=storage) == values[2:]
    session.execute.assert_awaited_once()
    storage.stat.assert_awaited_once_with("objects/icon")


@pytest.mark.parametrize("workspace_id", [0, -1, True, "1"])
def test_workspace_id_requires_positive_integer(workspace_id):
    with pytest.raises(ValidationError):
        MediaRequest(download_id="a" * 64, asset_version="3.7.0", game_version="1.0",
                     entities=["character:1"], workspace_id=workspace_id)


@pytest.mark.asyncio
async def test_matching_client_is_reused_across_releases_after_live_verification():
    old, new = target(), {**target(), "release_id": 9}
    session = AsyncMock()
    session.add = Mock()
    session.execute.return_value = [row(old)]
    storage = SimpleNamespace(backend="s3", bucket="media",
                             stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    assert await missing_media_targets(session, [new], source={"asset_version": "3.7.0"},
                                       storage=storage) == []
    reference = session.add.call_args.args[0]
    assert reference.release_id == 9 and reference.file_id == 10
    assert reference.source_path == old["path"]
    assert reference.metadata_json["game_source"]["download_id"] == "a" * 64


@pytest.mark.asyncio
async def test_storage_outage_is_not_silently_reported_as_missing():
    session = AsyncMock()
    session.execute.return_value = [row(target())]
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(side_effect=OSError))
    with pytest.raises(OSError):
        await missing_media_targets(session, [target()], source={}, storage=storage)


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [True, False])
async def test_availability_flags_follow_live_storage_not_stale_database_state(exists):
    reference, file, location = row(target())
    location.available = not exists
    session = AsyncMock()
    session.execute.return_value = [(reference, file, location)]
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(
        return_value=SimpleNamespace(size_bytes=12) if exists else None))
    result = await missing_media_targets(session, [target()], source={}, storage=storage)
    assert result == ([] if exists else [target()])
    assert location.available is exists and location.verified_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_tool,missing", [("a" * 64, False), ("b" * 64, True)])
async def test_changed_tool_version_requires_export(stored_tool, missing):
    session = AsyncMock()
    session.execute.return_value = [row(target(), tools={"WUWA_FMODEL_PATH": stored_tool})]
    storage = SimpleNamespace(backend="s3", bucket="media",
                             stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    result = await missing_media_targets(session, [target()],
        source={"tools": {"WUWA_FMODEL_PATH": "a" * 64}}, storage=storage)
    assert result == ([target()] if missing else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,reference_type,path,source_name", [
    ("voice", "voice_audio", "Client/Content/Audio/en_vo_plot_F.wem", "en_vo_plot_F.wem"),
    ("video", "cutscene_video", "Client/Content/Aki/Movies/CG.mp4", "main"),
    ("image", "event_banner", "upstream-banner-alias", None),
    ("image", "map_texture", "/Game/Map/Terrain.Terrain", "layer:1"),
])
async def test_generic_media_identity_preserves_filenames_roles_and_package_aliases(
    kind, reference_type, path, source_name,
):
    value = {"kind": kind, "reference_type": reference_type, "owner": None,
             "release_id": 9, "path": path, "source_name": source_name,
             "package_path": "Client/Content/exact-package.uasset"}
    reference = SimpleNamespace(
        owner_node_id=None, release_id=9, source_path=path, source_name=source_name,
        reference_type=reference_type, file_id=10, metadata_json={"game_source": {
            "schema_version": 1, "package_path": value["package_path"],
            "source_files": [{"package_path": value["package_path"], "sha256": "a" * 64}],
            "recipe_sha256": "b" * 64,
        }},
    )
    session = AsyncMock()
    session.execute.return_value = [(reference, SimpleNamespace(size_bytes=12),
                                     SimpleNamespace(object_key="objects/file"))]
    storage = SimpleNamespace(backend="s3", bucket="media",
                             stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    assert await missing_media_targets(session, [value],
        source={"recipe_sha256": "b" * 64}, storage=storage) == []
    assert await missing_media_targets(session, [value],
        source={"recipe_sha256": "c" * 64}, storage=storage) == [value]
