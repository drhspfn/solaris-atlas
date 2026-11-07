import json
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion.story_presentation_backfill import candidates


@pytest.mark.asyncio
async def test_discovery_includes_source_visuals_but_not_plain_dialogue_or_audio():
    sequence = {'Actions': json.dumps([{'Name': 'ShowTalk', 'Params': {
        'SequenceDataAsset': '/Game/Story/DA.DA'}}])}
    plain = {'Actions': json.dumps([{'Name': 'ShowTalk', 'Params': {
        'TalkItems': [{'TidTalk': 'text'}]}}, {'Name': 'PostAkEvent', 'Params': {}}])}
    db = AsyncMock()
    db.execute.return_value = [('flow_state:with_scene', sequence), ('flow_state:plain', plain)]
    assert await candidates(db, 21) == ['flow_state:with_scene']
    statement = str(db.execute.await_args.args[0])
    assert 'release_id' in statement and 'flowstate' in str(db.execute.await_args.args[0].compile().params)
