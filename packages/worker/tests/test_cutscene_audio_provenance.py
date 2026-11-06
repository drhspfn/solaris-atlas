from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wuwa_story_worker import cutscene_import
from wuwa_story_worker.wwise_banks import WWISE_RENDERER_SCHEMA, WWISE_REPAIR_CUTSCENES


@pytest.mark.asyncio
@pytest.mark.parametrize('target', sorted(WWISE_REPAIR_CUTSCENES) + ['cutscene:unaffected'])
@pytest.mark.parametrize('schema', [None, WWISE_RENDERER_SCHEMA])
@pytest.mark.parametrize('assembly', [None, cutscene_import.CUTSCENE_ASSEMBLY_SCHEMA])
async def test_durable_audio_refresh_requires_audited_assembly_before_recipe_build(monkeypatch, target, schema, assembly):
    session_factory = Mock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(cutscene_import, 'SessionFactory', session_factory)
    reference = SimpleNamespace(source_path=target, metadata_json={
        'publication_complete': True, 'game_source': {'wwise_renderer_schema': schema,
                                                    'cutscene_assembly_schema': assembly},
    })
    monkeypatch.setattr(cutscene_import, 'verified_references', AsyncMock(return_value=[reference]))
    context = {'asset_version': '3.7.1', 'download_id': 'pinned', 'tier': 'hd', 'tools': {'wwiser': 'pinned'}}
    missing = await cutscene_import.missing_cutscene_identities({target}, source_context=context, storage=Mock())
    assert missing == ({target} if assembly is None or target in WWISE_REPAIR_CUTSCENES and schema is None else set())
